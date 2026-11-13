"""Deterministic workflow execution engine (stdlib port).

Topological execution, zero-cost revalidation, safety caps, multi-source
context passing and self-correcting retries. The database is the single
source of truth for cancellation: every status change is ONE guarded UPDATE
(compare-and-set), so a committed cancel can never be overwritten by a later
success/failure and vice versa. The in-memory flag is only a fast-path hint.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, List, Optional

from backend.pyworker.core import executions as exec_repo
from backend.pyworker.db.core import Database, ts
from backend.pyworker.engine.context import (
    build_multi_source_context,
    build_team_transcript,
    extract_team_excerpt,
    format_agent_prompt,
    sanitize_model_output,
)
from backend.pyworker.engine.events import (
    build_team_message_event,
    event_hub,
    persist_and_broadcast_team_message,
    record_event,
)
from backend.pyworker.engine.state_machine import NodeState, WorkflowState
from backend.pyworker.engine.validator import normalize_node_type, validate_workflow_graph
from backend.pyworker.engine.output_validation import validate_output
from backend.pyworker.runtime.base import AgentRuntime, ExecuteAgentTask
from backend.pyworker.runtime.errors import safe_error_message
from backend.pyworker.runtime.ledger import KIND_WORKFLOW_NODE, run_agent_task

logger = logging.getLogger("agentos.executor")

# Safety caps
MAX_AI_CALLS_PER_EXECUTION = 10
MAX_ATTEMPTS_PER_NODE = 3
DEFAULT_PER_NODE_TIMEOUT = 120
MAX_PAYLOAD_BYTES = 500_000

_cancellation_registry: Dict[str, bool] = {}


def cancel_running_execution(execution_id: str) -> None:
    _cancellation_registry[execution_id] = True


def is_execution_cancelled(execution_id: str) -> bool:
    return _cancellation_registry.get(execution_id, False)


def clear_cancellation_flag(execution_id: str) -> None:
    _cancellation_registry.pop(execution_id, None)


_EXEC_ACTIVE = (WorkflowState.PENDING.value, WorkflowState.RUNNING.value)
_NODE_ACTIVE = (NodeState.PENDING.value, NodeState.READY.value, NodeState.RUNNING.value)


def _marks(values) -> str:
    return ",".join("?" for _ in values)


def _transition_execution(db: Database, execution_id: str, from_states, **values) -> bool:
    """Atomically set ``values`` iff the execution is currently in ``from_states``."""
    states = tuple(from_states)
    return db.update("workflow_executions", values,
                     f"id=? AND status IN ({_marks(states)})", (execution_id, *states)) == 1


def _transition_node(db: Database, node_rec_id: str, from_states, **values) -> bool:
    """Atomically set ``values`` iff the node is in ``from_states`` AND its
    owning execution is still active (not cancelled)."""
    states = tuple(from_states)
    return db.update(
        "node_executions", values,
        f"id=? AND status IN ({_marks(states)}) AND execution_id IN "
        f"(SELECT id FROM workflow_executions WHERE status IN ({_marks(_EXEC_ACTIVE)}))",
        (node_rec_id, *states, *_EXEC_ACTIVE)) == 1


def _cancel_open_nodes(db: Database, execution_id: str, message: str, *, only_if_cancelled: bool = False) -> None:
    where = f"execution_id=? AND status IN ({_marks(_NODE_ACTIVE)})"
    params: list = [execution_id, *_NODE_ACTIVE]
    if only_if_cancelled:
        where += " AND execution_id IN (SELECT id FROM workflow_executions WHERE status=?)"
        params.append(WorkflowState.CANCELLED.value)
    db.update("node_executions",
              {"status": NodeState.CANCELLED.value, "finished_at": ts(), "error_message": message},
              where, params)


def cancel_execution_rows(
    db: Database,
    execution_id: str,
    *,
    summary: str = "Execution cancelled by user request",
    node_message: str = "Cancelled by user request",
    event_json: Optional[str] = None,
) -> bool:
    """Atomically cancel a non-terminal execution and its unfinished nodes.

    The execution CAS, node cancellation and the optional audit event commit
    in ONE transaction. True iff this call moved the execution to cancelled."""
    with db.tx():
        won = db.update(
            "workflow_executions",
            {"status": WorkflowState.CANCELLED.value, "finished_at": ts(), "error_summary": summary},
            f"id=? AND status IN ({_marks(_EXEC_ACTIVE)})", (execution_id, *_EXEC_ACTIVE)) == 1
        if won:
            _cancel_open_nodes(db, execution_id, node_message)
            if event_json is not None:
                record_event(db, execution_id, "execution.cancelled", event_json)
    return won


def _current_status(db: Database, execution_id: str) -> Optional[str]:
    return db.scalar("SELECT status FROM workflow_executions WHERE id=?", (execution_id,))


def _cancel_requested(db: Database, execution_id: str) -> bool:
    return is_execution_cancelled(execution_id) or _current_status(db, execution_id) == WorkflowState.CANCELLED.value


def _lost_result(db: Database, execution_id: str) -> Dict[str, Any]:
    """A guarded write lost the race: report the real status and make sure no
    node of a cancelled execution is left non-terminal."""
    status_now = _current_status(db, execution_id)
    if status_now == WorkflowState.CANCELLED.value:
        _cancel_open_nodes(db, execution_id, "Cancelled by user request", only_if_cancelled=True)
        return {"status": "cancelled"}
    return {"status": status_now or "unknown"}


def _fail_workflow(db: Database, execution_id: str, error: str) -> bool:
    return _transition_execution(db, execution_id, _EXEC_ACTIVE, status=WorkflowState.FAILED.value,
                                error_summary=error, finished_at=ts())


async def execute_workflow(
    execution_id: str,
    db: Database,
    runtime: AgentRuntime,
    workflow_definition: Dict[str, Any],
    initial_input: Optional[Any] = None,
) -> Dict[str, Any]:
    wf_exec = exec_repo.execution_get(db, execution_id)
    if not wf_exec:
        raise ValueError(f"Execution record '{execution_id}' does not exist")

    # 1. Re-validate the graph before execution
    validation = validate_workflow_graph(workflow_definition)
    if not validation.valid:
        error_msg = f"Graph validation failed: {'; '.join(validation.errors)}"
        if not _transition_execution(db, execution_id, _EXEC_ACTIVE, status=WorkflowState.FAILED.value,
                                     error_summary=error_msg, finished_at=ts()):
            return _lost_result(db, execution_id)
        await event_hub.broadcast(execution_id, "execution.failed", {"error": error_msg})
        return {"status": "failed", "error": error_msg}

    # 2. RUNNING (never resurrects a cancelled/terminal execution)
    if not _transition_execution(db, execution_id, _EXEC_ACTIVE, status=WorkflowState.RUNNING.value,
                                 started_at=ts()):
        clear_cancellation_flag(execution_id)
        return _lost_result(db, execution_id)
    await event_hub.broadcast(execution_id, "execution.status.changed", {"status": "running"})

    nodes: List[Dict[str, Any]] = workflow_definition.get("nodes", [])
    edges: List[Dict[str, Any]] = workflow_definition.get("edges", [])
    node_map: Dict[str, Dict[str, Any]] = {str(n["id"]): n for n in nodes}
    topological_order = validation.topological_order

    upstream_map: Dict[str, List[str]] = {nid: [] for nid in node_map}
    downstream_map: Dict[str, List[str]] = {nid: [] for nid in node_map}
    for e in edges:
        s, t = str(e.get("source")), str(e.get("target"))
        if t in upstream_map and s in node_map:
            upstream_map[t].append(s)
        if s in downstream_map and t in node_map:
            downstream_map[s].append(t)

    turn_counter = 0
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

    async def _emit_team_message(nid: str, parsed_output: Any, n_rec_id: str) -> None:
        nonlocal turn_counter
        turn_counter += 1
        node_turns[nid] = turn_counter
        node_display_names[nid] = _node_display(nid)
        payload = build_team_message_event(
            execution_id=execution_id, turn=turn_counter, from_node_id=nid,
            from_display_name=node_display_names[nid], from_role=_node_role(nid),
            to_node_ids=downstream_map.get(nid, []), excerpt=extract_team_excerpt(parsed_output),
            full_output_ref=n_rec_id)
        await persist_and_broadcast_team_message(
            execution_id=execution_id, db=db, payload=payload, node_execution_id=n_rec_id)

    node_outputs: Dict[str, Any] = {}
    node_states: Dict[str, str] = {nid: NodeState.PENDING.value for nid in node_map}
    node_db_records: Dict[str, Dict[str, Any]] = {}

    for nid in topological_order:
        n_def = node_map[nid]
        n_data = n_def.get("data", {})
        n_type = normalize_node_type(n_def.get("type"))
        node_db_records[nid] = exec_repo.record_node_execution(db, {
            "execution_id": execution_id, "node_id": nid,
            "role_type": n_data.get("role_type") or n_type, "model_id": n_data.get("model_id"),
            "native_variant_id": n_data.get("variant"), "task_depth": n_data.get("task_depth", "Standard"),
            "status": NodeState.PENDING.value})

    total_ai_calls = 0

    try:
        for nid in topological_order:
            if _cancel_requested(db, execution_id):
                logger.info("Execution %s was cancelled before node %s", execution_id, nid)
                for k, v in list(node_states.items()):
                    if v in _NODE_ACTIVE:
                        node_states[k] = NodeState.CANCELLED.value
                won = cancel_execution_rows(db, execution_id, summary="Execution cancelled by user",
                                            node_message="Cancelled by user")
                res = _lost_result(db, execution_id)
                if won:
                    await event_hub.broadcast(execution_id, "execution.cancelled", {"reason": "User cancelled"})
                return res

            n_def = node_map[nid]
            n_type = normalize_node_type(n_def.get("type"))
            n_data = n_def.get("data", {})
            n_rec = node_db_records[nid]
            upstreams = upstream_map.get(nid, [])

            if any(node_states.get(u) in (NodeState.FAILED.value, NodeState.SKIPPED.value,
                                          NodeState.CANCELLED.value) for u in upstreams):
                if not _transition_node(db, n_rec["id"], (NodeState.PENDING.value,),
                                        status=NodeState.SKIPPED.value, finished_at=ts()):
                    return _lost_result(db, execution_id)
                node_states[nid] = NodeState.SKIPPED.value
                await event_hub.broadcast(execution_id, "node.status.changed", {
                    "nodeId": nid, "status": "skipped", "reason": "Upstream node failed or skipped"})
                continue

            if not _transition_node(db, n_rec["id"], (NodeState.PENDING.value,), status=NodeState.READY.value):
                return _lost_result(db, execution_id)
            node_states[nid] = NodeState.READY.value
            await event_hub.broadcast(execution_id, "node.status.changed", {"nodeId": nid, "status": "ready"})

            if not _transition_node(db, n_rec["id"], (NodeState.READY.value,),
                                    status=NodeState.RUNNING.value, started_at=ts()):
                return _lost_result(db, execution_id)
            node_states[nid] = NodeState.RUNNING.value
            await event_hub.broadcast(execution_id, "node.status.changed", {"nodeId": nid, "status": "running"})

            node_t0 = time.time()

            # --- START NODE ---
            if n_type == "start":
                start_output = initial_input if initial_input is not None else {"input": "Start workflow"}
                if not _transition_node(
                    db, n_rec["id"], (NodeState.RUNNING.value,), status=NodeState.SUCCEEDED.value,
                    output_json=(json.dumps(start_output, ensure_ascii=False)
                                 if not isinstance(start_output, str) else start_output),
                    finished_at=ts(), duration_ms=int((time.time() - node_t0) * 1000),
                ):
                    return _lost_result(db, execution_id)
                node_outputs[nid] = start_output
                node_states[nid] = NodeState.SUCCEEDED.value
                await event_hub.broadcast(execution_id, "node.status.changed",
                                          {"nodeId": nid, "status": "succeeded", "output": start_output})
                await _emit_team_message(nid, start_output, n_rec["id"])
                continue

            # --- OUTPUT NODE ---
            elif n_type == "output":
                output_context = build_multi_source_context(
                    node_id=nid, upstream_node_ids=upstreams, node_definitions=node_map,
                    node_outputs=node_outputs, workflow_input=initial_input)
                final_output = output_context["upstream_context"]
                if not _transition_node(
                    db, n_rec["id"], (NodeState.RUNNING.value,), status=NodeState.SUCCEEDED.value,
                    output_json=json.dumps(final_output, ensure_ascii=False), finished_at=ts(),
                    duration_ms=int((time.time() - node_t0) * 1000),
                ):
                    return _lost_result(db, execution_id)
                node_outputs[nid] = final_output
                node_states[nid] = NodeState.SUCCEEDED.value
                await event_hub.broadcast(execution_id, "node.status.changed",
                                          {"nodeId": nid, "status": "succeeded", "output": final_output})
                await _emit_team_message(
                    nid, {"summary": f"Workflow output assembled from {len(upstreams)} upstream team member(s)."},
                    n_rec["id"])
                continue

            # --- AI AGENT NODE ---
            elif n_type == "agent":
                model_id = n_data.get("model_id")
                role_type = n_data.get("role_type", "assistant")
                variant = n_data.get("variant")
                task_depth = n_data.get("task_depth", "Standard")
                timeout = int(n_data.get("timeout", DEFAULT_PER_NODE_TIMEOUT))
                instructions = n_data.get("instructions", f"Perform your role as {role_type}.")
                output_schema = n_data.get("output_schema")
                if output_schema is not None:
                    instructions += "\nRequired output schema:\n" + json.dumps(output_schema)

                free_models = await runtime.list_available_models(free_only=True)
                if not next((m for m in free_models if m.id == model_id), None):
                    err_msg = (f"Zero-cost revalidation failed: Model '{model_id}' is no longer "
                               "confirmed free (0/0) or active.")
                    logger.error(err_msg)
                    if not _transition_node(db, n_rec["id"], (NodeState.RUNNING.value,),
                                            status=NodeState.FAILED.value, error_message=err_msg,
                                            finished_at=ts()):
                        return _lost_result(db, execution_id)
                    node_states[nid] = NodeState.FAILED.value
                    await event_hub.broadcast(execution_id, "node.status.changed",
                                              {"nodeId": nid, "status": "failed", "error": err_msg})
                    if not _fail_workflow(db, execution_id, err_msg):
                        return _lost_result(db, execution_id)
                    return {"status": "failed", "error": err_msg}

                multi_context = build_multi_source_context(
                    node_id=nid, upstream_node_ids=upstreams, node_definitions=node_map,
                    node_outputs=node_outputs, workflow_input=initial_input)
                team_transcript = build_team_transcript(
                    multi_context, node_turns=node_turns, node_display_names=node_display_names)

                configured_retries = n_data.get("max_retries")
                max_retries = int(configured_retries) if configured_retries is not None else MAX_ATTEMPTS_PER_NODE - 1
                node_succeeded = False
                last_error = ""
                retry_feedback = None

                for attempt in range(1, max_retries + 2):
                    if total_ai_calls >= MAX_AI_CALLS_PER_EXECUTION:
                        last_error = ("Safety cap exceeded: maximum allowed AI calls per workflow "
                                      f"({MAX_AI_CALLS_PER_EXECUTION}) reached.")
                        break
                    if _cancel_requested(db, execution_id):
                        break

                    total_ai_calls += 1
                    if not _transition_node(db, n_rec["id"], (NodeState.RUNNING.value,), attempt_count=attempt):
                        return _lost_result(db, execution_id)

                    prompt = format_agent_prompt(
                        base_instructions=instructions, node_role=role_type, context_data=multi_context,
                        task_depth=task_depth, retry_error_feedback=retry_feedback,
                        team_transcript=team_transcript)

                    await event_hub.broadcast(execution_id, "node.attempt",
                                              {"nodeId": nid, "attempt": attempt, "taskDepth": task_depth})
                    if _cancel_requested(db, execution_id):
                        cancel_execution_rows(db, execution_id)
                        return _lost_result(db, execution_id)

                    try:
                        task = ExecuteAgentTask(
                            prompt=prompt, model_id=model_id, variant=variant, task_depth=task_depth,
                            session_title=f"workflow-{execution_id}-{nid}", timeout=timeout)
                        agent_res = await run_agent_task(runtime, task, kind=KIND_WORKFLOW_NODE)
                        clean_text = sanitize_model_output(agent_res.text)
                        parsed_json = json.loads(clean_text)
                        validate_output(parsed_json, output_schema)
                        duration_ms = int((time.time() - node_t0) * 1000)
                        # Guarded: a late runtime result is ignored if the execution was cancelled.
                        if not _transition_node(
                            db, n_rec["id"], (NodeState.RUNNING.value,), status=NodeState.SUCCEEDED.value,
                            output_json=json.dumps(parsed_json, ensure_ascii=False), finished_at=ts(),
                            duration_ms=duration_ms,
                        ):
                            logger.info("Ignoring late result for node %s: execution %s no longer active",
                                        nid, execution_id)
                            return _lost_result(db, execution_id)
                        node_outputs[nid] = parsed_json
                        node_states[nid] = NodeState.SUCCEEDED.value
                        node_succeeded = True
                        await event_hub.broadcast(execution_id, "node.status.changed", {
                            "nodeId": nid, "status": "succeeded", "attempt": attempt,
                            "output": parsed_json, "duration_ms": duration_ms})
                        await _emit_team_message(nid, parsed_json, n_rec["id"])
                        break

                    except Exception as e:
                        last_error = safe_error_message(e)
                        if _cancel_requested(db, execution_id):
                            break  # late failure after cancel: record nothing
                        logger.warning("Attempt %s failed on node %s: %s", attempt, nid, last_error)
                        retry_feedback = (
                            f"Validation/Parsing Error on attempt {attempt}: {last_error}.\n"
                            "Ensure your output is strictly parseable as valid JSON matching the instructions.")
                        record_event(db, execution_id, "node.attempt.failed",
                                     json.dumps({"attempt": attempt, "error": last_error}), n_rec["id"])

                if not node_succeeded:
                    if _cancel_requested(db, execution_id):
                        won = cancel_execution_rows(db, execution_id, summary="Execution cancelled by user",
                                                    node_message="Cancelled by user")
                        res = _lost_result(db, execution_id)
                        if won:
                            await event_hub.broadcast(execution_id, "execution.cancelled",
                                                      {"reason": "User cancelled"})
                        return res

                    attempts = db.scalar("SELECT attempt_count FROM node_executions WHERE id=?",
                                         (n_rec["id"],)) or 0
                    node_error = f"Node failed after {attempts} attempts. Last error: {last_error}"
                    if not _transition_node(
                        db, n_rec["id"], (NodeState.RUNNING.value,), status=NodeState.FAILED.value,
                        error_message=node_error, finished_at=ts(),
                        duration_ms=int((time.time() - node_t0) * 1000),
                    ):
                        return _lost_result(db, execution_id)
                    node_states[nid] = NodeState.FAILED.value
                    await event_hub.broadcast(execution_id, "node.status.changed",
                                              {"nodeId": nid, "status": "failed", "error": node_error})
                    if not _fail_workflow(db, execution_id, f"Workflow stopped: Node '{nid}' failed."):
                        return _lost_result(db, execution_id)
                    return {"status": "failed", "failed_node": nid, "error": node_error}

        # 3. All nodes completed (conditional: cannot overwrite a cancel)
        final_output = node_outputs.get(topological_order[-1], {})
        if not _transition_execution(
            db, execution_id, (WorkflowState.RUNNING.value,), status=WorkflowState.SUCCEEDED.value,
            final_output_json=(json.dumps(final_output, ensure_ascii=False)
                               if not isinstance(final_output, str) else final_output),
            finished_at=ts(),
        ):
            return _lost_result(db, execution_id)

        await event_hub.broadcast(execution_id, "execution.finished",
                                  {"status": "succeeded", "final_output": final_output})
        return {"status": "succeeded", "final_output": final_output}

    finally:
        clear_cancellation_flag(execution_id)
