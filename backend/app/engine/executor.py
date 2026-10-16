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
        wf_exec.status = WorkflowState.FAILED.value
        wf_exec.error_summary = error_msg
        wf_exec.finished_at = datetime.now(timezone.utc)
        db.commit()
        await event_hub.broadcast(execution_id, "execution.failed", {"error": error_msg})
        return {"status": "failed", "error": error_msg}

    # 2. Mark Workflow as RUNNING
    wf_exec.status = WorkflowState.RUNNING.value
    wf_exec.started_at = datetime.now(timezone.utc)
    db.commit()
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
            if is_execution_cancelled(execution_id):
                logger.info(f"Execution {execution_id} was cancelled before node {nid}")
                _mark_remaining_nodes_cancelled(topological_order, node_states, node_db_records, db, execution_id)
                wf_exec.status = WorkflowState.CANCELLED.value
                wf_exec.finished_at = datetime.now(timezone.utc)
                wf_exec.error_summary = "Execution cancelled by user"
                db.commit()
                await event_hub.broadcast(execution_id, "execution.cancelled", {"reason": "User cancelled"})
                return {"status": "cancelled"}

            n_def = node_map[nid]
            n_type = normalize_node_type(n_def.get("type"))
            n_data = n_def.get("data", {})
            n_rec = node_db_records[nid]
            upstreams = upstream_map.get(nid, [])

            # Check upstream status
            any_upstream_failed = any(node_states.get(u) in (NodeState.FAILED.value, NodeState.SKIPPED.value, NodeState.CANCELLED.value) for u in upstreams)
            if any_upstream_failed:
                node_states[nid] = NodeState.SKIPPED.value
                n_rec.status = NodeState.SKIPPED.value
                n_rec.finished_at = datetime.now(timezone.utc)
                db.commit()
                await event_hub.broadcast(execution_id, "node.status.changed", {
                    "nodeId": nid, "status": "skipped", "reason": "Upstream node failed or skipped"
                })
                continue

            # Transition to READY
            node_states[nid] = NodeState.READY.value
            n_rec.status = NodeState.READY.value
            db.commit()
            await event_hub.broadcast(execution_id, "node.status.changed", {"nodeId": nid, "status": "ready"})

            # Transition to RUNNING
            node_states[nid] = NodeState.RUNNING.value
            n_rec.status = NodeState.RUNNING.value
            n_rec.started_at = datetime.now(timezone.utc)
            db.commit()
            await event_hub.broadcast(execution_id, "node.status.changed", {"nodeId": nid, "status": "running"})

            node_t0 = time.time()

            # --- CASE 1: START NODE ---
            if n_type == "start":
                start_output = initial_input if initial_input is not None else {"input": "Start workflow"}
                node_outputs[nid] = start_output
                n_rec.output_json = json.dumps(start_output, ensure_ascii=False) if not isinstance(start_output, str) else start_output
                n_rec.status = NodeState.SUCCEEDED.value
                n_rec.finished_at = datetime.now(timezone.utc)
                n_rec.duration_ms = int((time.time() - node_t0) * 1000)
                node_states[nid] = NodeState.SUCCEEDED.value
                db.commit()

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
                node_outputs[nid] = final_output
                n_rec.output_json = json.dumps(final_output, ensure_ascii=False)
                n_rec.status = NodeState.SUCCEEDED.value
                n_rec.finished_at = datetime.now(timezone.utc)
                n_rec.duration_ms = int((time.time() - node_t0) * 1000)
                node_states[nid] = NodeState.SUCCEEDED.value
                db.commit()

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
                    n_rec.status = NodeState.FAILED.value
                    n_rec.error_message = err_msg
                    n_rec.finished_at = datetime.now(timezone.utc)
                    node_states[nid] = NodeState.FAILED.value
                    db.commit()
                    await event_hub.broadcast(execution_id, "node.status.changed", {"nodeId": nid, "status": "failed", "error": err_msg})
                    _fail_workflow(wf_exec, err_msg, db, execution_id)
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

                # Self-correcting retry loop (max 3 attempts)
                node_succeeded = False
                last_error = ""
                retry_feedback = None

                for attempt in range(1, MAX_ATTEMPTS_PER_NODE + 1):
                    # Check safety cap on total AI calls
                    if total_ai_calls >= MAX_AI_CALLS_PER_EXECUTION:
                        last_error = f"Safety cap exceeded: maximum allowed AI calls per workflow ({MAX_AI_CALLS_PER_EXECUTION}) reached."
                        break

                    if is_execution_cancelled(execution_id):
                        break

                    total_ai_calls += 1
                    n_rec.attempt_count = attempt
                    db.commit()

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

                    try:
                        task = ExecuteAgentTask(
                            prompt=prompt,
                            model_id=model_id,
                            variant=variant,
                            task_depth=task_depth,
                            session_title=f"agentos-{execution_id[:8]}-{nid}",
                            timeout=timeout
                        )
                        agent_res = await runtime.execute_task(task)

                        # Output Sanitizer: strip Markdown fences
                        clean_text = sanitize_model_output(agent_res.text)

                        # Verify valid JSON
                        parsed_json = json.loads(clean_text)

                        # Success! Record output
                        node_outputs[nid] = parsed_json
                        n_rec.output_json = json.dumps(parsed_json, ensure_ascii=False)
                        n_rec.status = NodeState.SUCCEEDED.value
                        n_rec.finished_at = datetime.now(timezone.utc)
                        n_rec.duration_ms = int((time.time() - node_t0) * 1000)
                        node_states[nid] = NodeState.SUCCEEDED.value
                        node_succeeded = True
                        db.commit()

                        await event_hub.broadcast(execution_id, "node.status.changed", {
                            "nodeId": nid,
                            "status": "succeeded",
                            "attempt": attempt,
                            "output": parsed_json,
                            "duration_ms": n_rec.duration_ms
                        })
                        await _emit_team_message(nid, parsed_json, n_rec.id)
                        break

                    except Exception as e:
                        last_error = str(e)
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
                    n_rec.status = NodeState.FAILED.value
                    n_rec.error_message = f"Node failed after {n_rec.attempt_count} attempts. Last error: {last_error}"
                    n_rec.finished_at = datetime.now(timezone.utc)
                    n_rec.duration_ms = int((time.time() - node_t0) * 1000)
                    node_states[nid] = NodeState.FAILED.value
                    db.commit()

                    await event_hub.broadcast(execution_id, "node.status.changed", {
                        "nodeId": nid, "status": "failed", "error": n_rec.error_message
                    })

                    # Fail workflow
                    _fail_workflow(wf_exec, f"Workflow stopped: Node '{nid}' failed.", db, execution_id)
                    return {"status": "failed", "failed_node": nid, "error": n_rec.error_message}

        # 3. All nodes completed: Succeeded
        final_output = node_outputs.get(topological_order[-1], {})
        wf_exec.status = WorkflowState.SUCCEEDED.value
        wf_exec.final_output_json = json.dumps(final_output, ensure_ascii=False) if not isinstance(final_output, str) else final_output
        wf_exec.finished_at = datetime.now(timezone.utc)
        db.commit()

        await event_hub.broadcast(execution_id, "execution.finished", {
            "status": "succeeded",
            "final_output": final_output
        })
        return {"status": "succeeded", "final_output": final_output}

    finally:
        clear_cancellation_flag(execution_id)


def _mark_remaining_nodes_cancelled(order: List[str], states: Dict[str, str], records: Dict[str, NodeExecution], db: Session, exec_id: str):
    now = datetime.now(timezone.utc)
    for nid in order:
        if states.get(nid) in (NodeState.PENDING.value, NodeState.READY.value, NodeState.RUNNING.value):
            states[nid] = NodeState.CANCELLED.value
            rec = records.get(nid)
            if rec:
                rec.status = NodeState.CANCELLED.value
                rec.finished_at = now
                rec.error_message = "Cancelled by user"
    db.commit()


def _fail_workflow(wf_exec: WorkflowExecution, error: str, db: Session, exec_id: str):
    wf_exec.status = WorkflowState.FAILED.value
    wf_exec.error_summary = error
    wf_exec.finished_at = datetime.now(timezone.utc)
    db.commit()
