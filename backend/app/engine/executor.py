"""
Deterministic Workflow Execution Engine
Orchestrates topological execution, zero-cost revalidation, safety caps,
multi-source context passing, and self-correcting retries.
"""

import time
import json
import logging
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
from sqlalchemy import insert, select, update
from sqlalchemy.orm import Session

from backend.app.db.models import WorkflowExecution, NodeExecution, ExecutionEvent
from backend.app.db.repository import ExecutionRepository
from backend.app.engine.validator import validate_workflow_graph, normalize_node_type
from backend.app.engine.state_machine import NodeState, WorkflowState
from backend.app.engine.context import (
    sanitize_model_output,
    build_multi_source_context,
    build_team_transcript,
    extract_team_excerpt,
    format_agent_prompt,
)
from backend.app.engine.events import (
    event_hub,
    build_team_message_event,
    persist_and_broadcast_team_message,
)
from backend.app.runtime.base import AgentRuntime, ExecuteAgentTask
from backend.app.runtime.ledger import run_agent_task, KIND_WORKFLOW_NODE

logger = logging.getLogger("agentos.executor")

# Safety Caps
MAX_AI_CALLS_PER_EXECUTION = 10
MAX_ATTEMPTS_PER_NODE = 3
DEFAULT_PER_NODE_TIMEOUT = 120
MAX_PAYLOAD_BYTES = 500_000

# Cancellation registry: execution_id -> bool
_cancellation_registry: Dict[str, bool] = {}


def cancel_running_execution(execution_id: str):
    """Signals an active execution to cancel immediately."""
    _cancellation_registry[execution_id] = True


def is_execution_cancelled(execution_id: str) -> bool:
    """Checks if cancellation has been requested for an execution."""
    return _cancellation_registry.get(execution_id, False)


def clear_cancellation_flag(execution_id: str):
    """Cleans up the cancellation registry after completion."""
    _cancellation_registry.pop(execution_id, None)


# ---------------------------------------------------------------------------
# Atomic, conditional state writes.
#
# The database is the single source of truth for cancellation. Every status
# change below is ONE guarded UPDATE (compare-and-set), so a committed cancel
# can never be overwritten by a later success/failure, and a cancel can never
# overwrite an execution that already reached a terminal state. The in-memory
# flag is only a fast-path hint for the running task.
# ---------------------------------------------------------------------------
_EXEC_ACTIVE = (WorkflowState.PENDING.value, WorkflowState.RUNNING.value)
_NODE_ACTIVE = (NodeState.PENDING.value, NodeState.READY.value, NodeState.RUNNING.value)


def _transition_execution(db: Session, execution_id: str, from_states, **values) -> bool:
    """Atomically set `values` iff the execution is currently in `from_states`."""
    try:
        res = db.execute(
            update(WorkflowExecution)
            .where(WorkflowExecution.id == execution_id, WorkflowExecution.status.in_(tuple(from_states)))
            .values(**values)
            .execution_options(synchronize_session=False)
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.expire_all()
    return res.rowcount == 1


def _transition_node(db: Session, node_rec_id: str, from_states, **values) -> bool:
    """Atomically set `values` on a node iff it is in `from_states` AND its
    owning execution is still active (pending/running, i.e. not cancelled)."""
    try:
        res = db.execute(
            update(NodeExecution)
            .where(
                NodeExecution.id == node_rec_id,
                NodeExecution.status.in_(tuple(from_states)),
                NodeExecution.execution_id.in_(
                    select(WorkflowExecution.id).where(WorkflowExecution.status.in_(_EXEC_ACTIVE))
                ),
            )
            .values(**values)
            .execution_options(synchronize_session=False)
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.expire_all()
    return res.rowcount == 1


def _cancel_open_nodes(db: Session, execution_id: str, message: str, *, only_if_cancelled: bool = False) -> None:
    stmt = update(NodeExecution).where(
        NodeExecution.execution_id == execution_id,
        NodeExecution.status.in_(_NODE_ACTIVE),
    )
    if only_if_cancelled:
        stmt = stmt.where(
            NodeExecution.execution_id.in_(
                select(WorkflowExecution.id).where(WorkflowExecution.status == WorkflowState.CANCELLED.value)
            )
        )
    db.execute(
        stmt.values(
            status=NodeState.CANCELLED.value,
            finished_at=datetime.now(timezone.utc),
            error_message=message,
        ).execution_options(synchronize_session=False)
    )


def cancel_execution_rows(
    db: Session,
    execution_id: str,
    *,
    summary: str = "Execution cancelled by user request",
    node_message: str = "Cancelled by user request",
    event_json: Optional[str] = None,
) -> bool:
    """Atomically cancel a non-terminal execution and its unfinished nodes.

    The execution CAS, node cancellation and (optional) audit event commit in
    ONE transaction. Returns True iff this call moved the execution to
    cancelled; False when it is unknown or already terminal (nothing written).
    """
    now = datetime.now(timezone.utc)
    try:
        res = db.execute(
            update(WorkflowExecution)
            .where(WorkflowExecution.id == execution_id, WorkflowExecution.status.in_(_EXEC_ACTIVE))
            .values(status=WorkflowState.CANCELLED.value, finished_at=now, error_summary=summary)
            .execution_options(synchronize_session=False)
        )
        won = res.rowcount == 1
        if won:
            _cancel_open_nodes(db, execution_id, node_message)
            if event_json is not None:
                db.execute(insert(ExecutionEvent).values(
                    execution_id=execution_id, event_type="execution.cancelled", event_json=event_json
                ))
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.expire_all()
    return won


def _current_status(db: Session, execution_id: str) -> Optional[str]:
    db.expire_all()
    return db.execute(
        select(WorkflowExecution.status).where(WorkflowExecution.id == execution_id)
    ).scalar_one_or_none()


def _cancel_requested(db: Session, execution_id: str) -> bool:
    return is_execution_cancelled(execution_id) or _current_status(db, execution_id) == WorkflowState.CANCELLED.value


def _lost_result(db: Session, execution_id: str) -> Dict[str, Any]:
    """A guarded write lost the race: report the execution's real status and
    make sure no node of a cancelled execution is left non-terminal."""
    status_now = _current_status(db, execution_id)
    if status_now == WorkflowState.CANCELLED.value:
        try:
            _cancel_open_nodes(db, execution_id, "Cancelled by user request", only_if_cancelled=True)
            db.commit()
        except Exception:
            db.rollback()
            raise
        db.expire_all()
        return {"status": "cancelled"}
    return {"status": status_now or "unknown"}


async def execute_workflow(
    execution_id: str,
    db: Session,
    runtime: AgentRuntime,
    workflow_definition: Dict[str, Any],
    initial_input: Optional[Any] = None
) -> Dict[str, Any]:
    """
    Executes a validated workflow strictly adhering to the deterministic contract.
    """
    exec_repo = ExecutionRepository(db)
    wf_exec = exec_repo.get_execution(execution_id)
    if not wf_exec:
        raise ValueError(f"Execution record '{execution_id}' does not exist")

    # 1. Re-validate graph before execution
    validation = validate_workflow_graph(workflow_definition)
    if not validation.valid:
        error_msg = f"Graph validation failed: {'; '.join(validation.errors)}"
        if not _transition_execution(
            db, execution_id, _EXEC_ACTIVE,
            status=WorkflowState.FAILED.value, error_summary=error_msg, finished_at=datetime.now(timezone.utc),
        ):
            return _lost_result(db, execution_id)
        await event_hub.broadcast(execution_id, "execution.failed", {"error": error_msg})
        return {"status": "failed", "error": error_msg}

    # 2. Mark Workflow as RUNNING (never resurrects a cancelled/terminal execution)
    if not _transition_execution(
        db, execution_id, _EXEC_ACTIVE,
        status=WorkflowState.RUNNING.value, started_at=datetime.now(timezone.utc),
    ):
        clear_cancellation_flag(execution_id)
        return _lost_result(db, execution_id)
    await event_hub.broadcast(execution_id, "execution.status.changed", {"status": "running"})

    nodes: List[Dict[str, Any]] = workflow_definition.get("nodes", [])
    edges: List[Dict[str, Any]] = workflow_definition.get("edges", [])

    node_map: Dict[str, Dict[str, Any]] = {str(n["id"]): n for n in nodes}
    topological_order = validation.topological_order

    # Build upstream map: target_id -> list of source_ids
    # and downstream map: source_id -> list of target_ids (team.message recipients)
    upstream_map: Dict[str, List[str]] = {nid: [] for nid in node_map}
    downstream_map: Dict[str, List[str]] = {nid: [] for nid in node_map}
    for e in edges:
        s = str(e.get("source"))
        t = str(e.get("target"))
        if t in upstream_map and s in node_map:
            upstream_map[t].append(s)
        if s in downstream_map and t in node_map:
            downstream_map[s].append(t)

    # Sequential team-chat turn tracking (deterministic: topological order).
    turn_counter: int = 0
    node_turns: Dict[str, int] = {}
    node_display_names: Dict[str, str] = {}

    def _node_role(nid: str) -> str:
        n_def = node_map.get(nid, {})
        n_data = n_def.get("data", {}) if isinstance(n_def, dict) else {}
        return str(n_data.get("role_type") or n_data.get("role") or normalize_node_type(n_def.get("type")) or "unknown")

    def _node_display(nid: str) -> str:
        n_def = node_map.get(nid, {})
        n_data = n_def.get("data", {}) if isinstance(n_def, dict) else {}
        name = n_data.get("display_name") or n_data.get("name")
        role = _node_role(nid)
        base = str(name).strip() if name else role.capitalize()
        return f"{base} ({nid})" if base != nid else nid

    async def _emit_team_message(
        nid: str,
        parsed_output: Any,
        n_rec_id: str,
    ) -> None:
        """Increments turn, persists + broadcasts team.message for a completed node."""
        nonlocal turn_counter
        turn_counter += 1
        node_turns[nid] = turn_counter
        node_display_names[nid] = _node_display(nid)
        excerpt = extract_team_excerpt(parsed_output)
        payload = build_team_message_event(
            execution_id=execution_id,
            turn=turn_counter,
            from_node_id=nid,
            from_display_name=node_display_names[nid],
            from_role=_node_role(nid),
            to_node_ids=downstream_map.get(nid, []),
            excerpt=excerpt,
            full_output_ref=n_rec_id,
        )
        await persist_and_broadcast_team_message(
            execution_id=execution_id,
            db=db,
            payload=payload,
            node_execution_id=n_rec_id,
        )

    node_outputs: Dict[str, Any] = {}
    node_states: Dict[str, str] = {nid: NodeState.PENDING.value for nid in node_map}
    node_db_records: Dict[str, NodeExecution] = {}

    # Initialize all NodeExecution records in DB
    for nid in topological_order:
        n_def = node_map[nid]
        n_data = n_def.get("data", {})
        n_type = normalize_node_type(n_def.get("type"))
        n_rec = exec_repo.record_node_execution({
            "execution_id": execution_id,
            "node_id": nid,
            "role_type": n_data.get("role_type") or n_type,
            "model_id": n_data.get("model_id"),
            "native_variant_id": n_data.get("variant"),
            "task_depth": n_data.get("task_depth", "Standard"),
            "status": NodeState.PENDING.value
        })
        node_db_records[nid] = n_rec

    total_ai_calls = 0

    try:
        for nid in topological_order:
            # Check cancellation before node execution
            if _cancel_requested(db, execution_id):
                logger.info(f"Execution {execution_id} was cancelled before node {nid}")
                for k, v in list(node_states.items()):
                    if v in _NODE_ACTIVE:
                        node_states[k] = NodeState.CANCELLED.value
                # No-op CAS when the cancel route already committed; otherwise
                # (flag-only cancel) this call performs the cancellation.
                won = cancel_execution_rows(
                    db, execution_id,
                    summary="Execution cancelled by user", node_message="Cancelled by user",
                )
                res = _lost_result(db, execution_id)
                if won:
                    await event_hub.broadcast(execution_id, "execution.cancelled", {"reason": "User cancelled"})
                return res

            n_def = node_map[nid]
            n_type = normalize_node_type(n_def.get("type"))
            n_data = n_def.get("data", {})
            n_rec = node_db_records[nid]
            upstreams = upstream_map.get(nid, [])

            # Check upstream status
            any_upstream_failed = any(node_states.get(u) in (NodeState.FAILED.value, NodeState.SKIPPED.value, NodeState.CANCELLED.value) for u in upstreams)
            if any_upstream_failed:
                if not _transition_node(
                    db, n_rec.id, (NodeState.PENDING.value,),
                    status=NodeState.SKIPPED.value, finished_at=datetime.now(timezone.utc),
                ):
                    return _lost_result(db, execution_id)
                node_states[nid] = NodeState.SKIPPED.value
                await event_hub.broadcast(execution_id, "node.status.changed", {
                    "nodeId": nid, "status": "skipped", "reason": "Upstream node failed or skipped"
                })
                continue

            # Transition to READY
            if not _transition_node(db, n_rec.id, (NodeState.PENDING.value,), status=NodeState.READY.value):
                return _lost_result(db, execution_id)
            node_states[nid] = NodeState.READY.value
            await event_hub.broadcast(execution_id, "node.status.changed", {"nodeId": nid, "status": "ready"})

            # Transition to RUNNING
            if not _transition_node(
                db, n_rec.id, (NodeState.READY.value,),
                status=NodeState.RUNNING.value, started_at=datetime.now(timezone.utc),
            ):
                return _lost_result(db, execution_id)
            node_states[nid] = NodeState.RUNNING.value
            await event_hub.broadcast(execution_id, "node.status.changed", {"nodeId": nid, "status": "running"})

            node_t0 = time.time()

            # --- CASE 1: START NODE ---
            if n_type == "start":
                start_output = initial_input if initial_input is not None else {"input": "Start workflow"}
                if not _transition_node(
                    db, n_rec.id, (NodeState.RUNNING.value,),
                    status=NodeState.SUCCEEDED.value,
                    output_json=json.dumps(start_output, ensure_ascii=False) if not isinstance(start_output, str) else start_output,
                    finished_at=datetime.now(timezone.utc),
                    duration_ms=int((time.time() - node_t0) * 1000),
                ):
                    return _lost_result(db, execution_id)
                node_outputs[nid] = start_output
                node_states[nid] = NodeState.SUCCEEDED.value

                await event_hub.broadcast(execution_id, "node.status.changed", {
                    "nodeId": nid, "status": "succeeded", "output": start_output
                })
                await _emit_team_message(nid, start_output, n_rec.id)
                continue

            # --- CASE 2: OUTPUT NODE ---
            elif n_type == "output":
                # Combine outputs of all nodes feeding into output
                output_context = build_multi_source_context(
                    node_id=nid,
                    upstream_node_ids=upstreams,
                    node_definitions=node_map,
                    node_outputs=node_outputs,
                    workflow_input=initial_input
                )
                final_output = output_context["upstream_context"]
                if not _transition_node(
                    db, n_rec.id, (NodeState.RUNNING.value,),
                    status=NodeState.SUCCEEDED.value,
                    output_json=json.dumps(final_output, ensure_ascii=False),
                    finished_at=datetime.now(timezone.utc),
                    duration_ms=int((time.time() - node_t0) * 1000),
                ):
                    return _lost_result(db, execution_id)
                node_outputs[nid] = final_output
                node_states[nid] = NodeState.SUCCEEDED.value

                await event_hub.broadcast(execution_id, "node.status.changed", {
                    "nodeId": nid, "status": "succeeded", "output": final_output
                })
                await _emit_team_message(nid, {"summary": f"Workflow output assembled from {len(upstreams)} upstream team member(s)."}, n_rec.id)
                continue

            # --- CASE 3: AI AGENT NODE ---
            elif n_type == "agent":
                model_id = n_data.get("model_id")
                role_type = n_data.get("role_type", "assistant")
                variant = n_data.get("variant")
                task_depth = n_data.get("task_depth", "Standard")
                timeout = int(n_data.get("timeout", DEFAULT_PER_NODE_TIMEOUT))
                instructions = n_data.get("instructions", f"Perform your role as {role_type}.")

                # Pre-node zero-cost revalidation
                free_models = await runtime.list_available_models(free_only=True)
                target_free_model = next((m for m in free_models if m.id == model_id), None)
                if not target_free_model:
                    err_msg = f"Zero-cost revalidation failed: Model '{model_id}' is no longer confirmed free (0/0) or active."
                    logger.error(err_msg)
                    if not _transition_node(
                        db, n_rec.id, (NodeState.RUNNING.value,),
                        status=NodeState.FAILED.value, error_message=err_msg,
                        finished_at=datetime.now(timezone.utc),
                    ):
                        return _lost_result(db, execution_id)
                    node_states[nid] = NodeState.FAILED.value
                    await event_hub.broadcast(execution_id, "node.status.changed", {"nodeId": nid, "status": "failed", "error": err_msg})
                    if not _fail_workflow(db, execution_id, err_msg):
                        return _lost_result(db, execution_id)
                    return {"status": "failed", "error": err_msg}

                # Multi-source context aggregation + team transcript
                multi_context = build_multi_source_context(
                    node_id=nid,
                    upstream_node_ids=upstreams,
                    node_definitions=node_map,
                    node_outputs=node_outputs,
                    workflow_input=initial_input
                )
                team_transcript = build_team_transcript(
                    multi_context,
                    node_turns=node_turns,
                    node_display_names=node_display_names,
                )

                # max_retries counts additional attempts after the first call.
                configured_retries = n_data.get("max_retries")
                max_retries = int(configured_retries) if configured_retries is not None else MAX_ATTEMPTS_PER_NODE - 1
                node_succeeded = False
                last_error = ""
                retry_feedback = None

                for attempt in range(1, max_retries + 2):
                    # Check safety cap on total AI calls
                    if total_ai_calls >= MAX_AI_CALLS_PER_EXECUTION:
                        last_error = f"Safety cap exceeded: maximum allowed AI calls per workflow ({MAX_AI_CALLS_PER_EXECUTION}) reached."
                        break

                    if _cancel_requested(db, execution_id):
                        break

                    total_ai_calls += 1
                    if not _transition_node(db, n_rec.id, (NodeState.RUNNING.value,), attempt_count=attempt):
                        return _lost_result(db, execution_id)

                    prompt = format_agent_prompt(
                        base_instructions=instructions,
                        node_role=role_type,
                        context_data=multi_context,
                        task_depth=task_depth,
                        retry_error_feedback=retry_feedback,
                        team_transcript=team_transcript,
                    )

                    await event_hub.broadcast(execution_id, "node.attempt", {
                        "nodeId": nid, "attempt": attempt, "taskDepth": task_depth
                    })
                    if _cancel_requested(db, execution_id):
                        cancel_execution_rows(db, execution_id)
                        return _lost_result(db, execution_id)

                    try:
                        task = ExecuteAgentTask(
                            prompt=prompt,
                            model_id=model_id,
                            variant=variant,
                            task_depth=task_depth,
                            session_title=f"workflow-{execution_id}-{nid}",
                            timeout=timeout
                        )
                        agent_res = await run_agent_task(runtime, task, kind=KIND_WORKFLOW_NODE)

                        # Output Sanitizer: strip Markdown fences
                        clean_text = sanitize_model_output(agent_res.text)

                        # Verify valid JSON
                        parsed_json = json.loads(clean_text)

                        # Success! Record output
                        duration_ms = int((time.time() - node_t0) * 1000)
                        # Guarded write: a late runtime result is ignored if the
                        # execution was cancelled (or the node is no longer running).
                        if not _transition_node(
                            db, n_rec.id, (NodeState.RUNNING.value,),
                            status=NodeState.SUCCEEDED.value,
                            output_json=json.dumps(parsed_json, ensure_ascii=False),
                            finished_at=datetime.now(timezone.utc),
                            duration_ms=duration_ms,
                        ):
                            logger.info(f"Ignoring late result for node {nid}: execution {execution_id} no longer active")
                            return _lost_result(db, execution_id)
                        node_outputs[nid] = parsed_json
                        node_states[nid] = NodeState.SUCCEEDED.value
                        node_succeeded = True

                        await event_hub.broadcast(execution_id, "node.status.changed", {
                            "nodeId": nid,
                            "status": "succeeded",
                            "attempt": attempt,
                            "output": parsed_json,
                            "duration_ms": duration_ms
                        })
                        await _emit_team_message(nid, parsed_json, n_rec.id)
                        break

                    except Exception as e:
                        from backend.app.runtime.errors import safe_error_message
                        last_error = safe_error_message(e)
                        if _cancel_requested(db, execution_id):
                            # Late failure after cancel: ignore it, record nothing.
                            break
                        logger.warning(f"Attempt {attempt} failed on node {nid}: {last_error}")
                        # Format feedback for next attempt
                        retry_feedback = (
                            f"Validation/Parsing Error on attempt {attempt}: {last_error}.\n"
                            f"Ensure your output is strictly parseable as valid JSON matching the instructions."
                        )
                        exec_repo.record_event(
                            execution_id=execution_id,
                            node_execution_id=n_rec.id,
                            event_type="node.attempt.failed",
                            event_json=json.dumps({"attempt": attempt, "error": last_error})
                        )

                if not node_succeeded:
                    if _cancel_requested(db, execution_id):
                        # Cancellation (not a node failure) ended this node.
                        won = cancel_execution_rows(
                            db, execution_id,
                            summary="Execution cancelled by user", node_message="Cancelled by user",
                        )
                        res = _lost_result(db, execution_id)
                        if won:
                            await event_hub.broadcast(execution_id, "execution.cancelled", {"reason": "User cancelled"})
                        return res

                    attempts = db.execute(
                        select(NodeExecution.attempt_count).where(NodeExecution.id == n_rec.id)
                    ).scalar_one_or_none() or 0
                    node_error = f"Node failed after {attempts} attempts. Last error: {last_error}"
                    if not _transition_node(
                        db, n_rec.id, (NodeState.RUNNING.value,),
                        status=NodeState.FAILED.value, error_message=node_error,
                        finished_at=datetime.now(timezone.utc),
                        duration_ms=int((time.time() - node_t0) * 1000),
                    ):
                        return _lost_result(db, execution_id)
                    node_states[nid] = NodeState.FAILED.value

                    await event_hub.broadcast(execution_id, "node.status.changed", {
                        "nodeId": nid, "status": "failed", "error": node_error
                    })

                    # Fail workflow (conditional: a committed cancel wins)
                    if not _fail_workflow(db, execution_id, f"Workflow stopped: Node '{nid}' failed."):
                        return _lost_result(db, execution_id)
                    return {"status": "failed", "failed_node": nid, "error": node_error}

        # 3. All nodes completed: Succeeded (conditional: cannot overwrite a cancel)
        final_output = node_outputs.get(topological_order[-1], {})
        if not _transition_execution(
            db, execution_id, (WorkflowState.RUNNING.value,),
            status=WorkflowState.SUCCEEDED.value,
            final_output_json=json.dumps(final_output, ensure_ascii=False) if not isinstance(final_output, str) else final_output,
            finished_at=datetime.now(timezone.utc),
        ):
            return _lost_result(db, execution_id)

        await event_hub.broadcast(execution_id, "execution.finished", {
            "status": "succeeded",
            "final_output": final_output
        })
        return {"status": "succeeded", "final_output": final_output}

    finally:
        clear_cancellation_flag(execution_id)


def _fail_workflow(db: Session, execution_id: str, error: str) -> bool:
    """Conditionally fail a still-active execution. False if it lost the race."""
    return _transition_execution(
        db, execution_id, _EXEC_ACTIVE,
        status=WorkflowState.FAILED.value, error_summary=error, finished_at=datetime.now(timezone.utc),
    )
