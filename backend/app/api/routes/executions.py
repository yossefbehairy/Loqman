"""
Execution Management, Cancellation, and SSE Event Stream Routes
"""

import asyncio
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
from backend.app.engine.executor import cancel_running_execution, cancel_execution_rows
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

    running_nodes = [node.node_id for node in exec_obj.node_executions if node.status == NodeState.RUNNING.value]
    # Single atomic compare-and-set: only a pending/running execution can be
    # cancelled; the node cancellation and audit event commit in the same
    # transaction. A concurrently-finished execution is never overwritten.
    won = cancel_execution_rows(
        db,
        execution_id,
        event_json='{"reason": "User requested cancellation"}',
    )
    if not won:
        current = repo.get_execution(execution_id)
        db.refresh(current)
        return {"status": current.status, "message": f"Execution already in terminal state '{current.status}'"}

    # Signal the in-memory flag only after the cancel is committed.
    cancel_running_execution(execution_id)

    # The database is already cancelled; stopping remote work is best effort.
    async def abort_running():
        await asyncio.gather(*(runtime.abort_task(f"workflow-{execution_id}-{node_id}")
                               for node_id in running_nodes), return_exceptions=True)
    try:
        await asyncio.wait_for(abort_running(), timeout=5)
    except asyncio.TimeoutError:
        pass

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

    return EventSourceResponse(
        stream_execution_events(execution_id=execution_id, load_past=lambda: list(exec_obj.events))
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
