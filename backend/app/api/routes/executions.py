"""
Execution Management, Cancellation, and SSE Event Stream Routes
"""

from typing import List, Dict, Any
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, status
from sse_starlette.sse import EventSourceResponse
from sqlalchemy.orm import Session

from backend.app.db.session import get_db
from backend.app.db.repository import ExecutionRepository
from backend.app.schemas.execution import (
    WorkflowExecutionResponse,
    TeamMessageListResponse,
    TeamMessageResponse,
)
from backend.app.engine.events import (
    stream_execution_events,
    event_hub,
    TEAM_MESSAGE_EVENT_TYPE,
)
from backend.app.engine.executor import cancel_running_execution
from backend.app.engine.state_machine import WorkflowState, NodeState
from backend.app.runtime import get_runtime, AgentRuntime

router = APIRouter(prefix="/api/executions", tags=["executions"])


@router.get("", response_model=List[WorkflowExecutionResponse])
def list_executions(skip: int = 0, limit: int = 50, db: Session = Depends(get_db)):
    """
    Returns history of workflow executions ordered newest first.
    """
    repo = ExecutionRepository(db)
    return repo.list_executions(skip=skip, limit=limit)


@router.get("/{execution_id}", response_model=WorkflowExecutionResponse)
def get_execution(execution_id: str, db: Session = Depends(get_db)):
    """
    Returns full details, node executions, and audit events for a single execution.
    """
    repo = ExecutionRepository(db)
    exec_obj = repo.get_execution(execution_id)
    if not exec_obj:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Execution not found")
    return exec_obj


@router.post("/{execution_id}/cancel")
async def cancel_execution(
    execution_id: str,
    db: Session = Depends(get_db),
    runtime: AgentRuntime = Depends(get_runtime)
):
    """
    Cancels an active execution, marks pending/running nodes cancelled, and ignores late outputs.
    """
    repo = ExecutionRepository(db)
    exec_obj = repo.get_execution(execution_id)
    if not exec_obj:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Execution not found")

    if exec_obj.status in (WorkflowState.SUCCEEDED.value, WorkflowState.FAILED.value, WorkflowState.CANCELLED.value):
        return {"status": exec_obj.status, "message": f"Execution already in terminal state '{exec_obj.status}'"}

    # Signal in-memory flag
    cancel_running_execution(execution_id)

    # Transition DB states
    now = datetime.now(timezone.utc)
    exec_obj.status = WorkflowState.CANCELLED.value
    exec_obj.finished_at = now
    exec_obj.error_summary = "Execution cancelled by user request"

    for node_exec in exec_obj.node_executions:
        if node_exec.status in (NodeState.PENDING.value, NodeState.READY.value, NodeState.RUNNING.value):
            node_exec.status = NodeState.CANCELLED.value
            node_exec.finished_at = now
            node_exec.error_message = "Cancelled by user request"

    repo.record_event(
        execution_id=execution_id,
        event_type="execution.cancelled",
        event_json='{"reason": "User requested cancellation"}'
    )
    db.commit()

    # Broadcast event to SSE subscribers
    await event_hub.broadcast(execution_id, "execution.cancelled", {"reason": "User cancelled"})

    return {
        "status": "cancelled",
        "execution_id": execution_id,
        "message": "Execution marked as cancelled"
    }


@router.get("/{execution_id}/events")
async def get_execution_events_stream(execution_id: str, db: Session = Depends(get_db)):
    """
    Streams real-time Server-Sent Events (SSE). Replays historical events from SQLite on reconnect.
    """
    repo = ExecutionRepository(db)
    exec_obj = repo.get_execution(execution_id)
    if not exec_obj:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Execution not found")

    # Fetch past events for historical catch-up
    past_events = exec_obj.events

    return EventSourceResponse(
        stream_execution_events(execution_id=execution_id, past_events=past_events)
    )


@router.get("/{execution_id}/messages", response_model=TeamMessageListResponse)
def get_execution_team_messages(execution_id: str, db: Session = Depends(get_db)):
    """
    Returns the persistent Team-Chat history (team.message events) ordered by
    turn ascending. Ready for the React Team Chat panel.
    """
    import json as _json

    repo = ExecutionRepository(db)
    exec_obj = repo.get_execution(execution_id)
    if not exec_obj:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Execution not found")

    team_events = [e for e in (exec_obj.events or []) if e.event_type == TEAM_MESSAGE_EVENT_TYPE]

    parsed: list = []
    for e in team_events:
        try:
            payload = _json.loads(e.event_json) if isinstance(e.event_json, str) else dict(e.event_json)
        except Exception:
            continue
        try:
            turn_val = int(payload.get("turn", 0))
        except Exception:
            turn_val = 0
        parsed.append((turn_val, e.created_at, e, payload))

    parsed.sort(key=lambda t: (t[0], str(t[1])))

    messages = [
        TeamMessageResponse(
            execution_id=execution_id,
            turn=int(payload.get("turn", 0)),
            from_node_id=str(payload.get("fromNodeId", "")),
            from_display_name=str(payload.get("fromDisplayName", "")),
            from_role=str(payload.get("fromRole", "")),
            to_node_ids=list(payload.get("toNodeIds", []) or []),
            excerpt=str(payload.get("excerpt", "")),
            full_output_ref=str(payload.get("fullOutputRef", "")),
            timestamp=str(payload.get("timestamp", "")),
            created_at=e.created_at,
        )
        for _, _, e, payload in parsed
    ]
    return TeamMessageListResponse(execution_id=execution_id, count=len(messages), messages=messages)
