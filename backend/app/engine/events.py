"""
SSE Event Hub and Real-Time Event Broadcaster
Manages in-memory live subscriber queues and SQLite replay for seamless reconnection.

Architectural Note (University Documentation):
Collaborative Multi-Agent Communication is implemented as a Sequential DAG with
Persistent Message-Passing (team.message events and cumulative transcript
injection), guaranteeing deterministic termination and zero infinite-loop risk
while preserving full conversational context across agents.
"""

import json
import asyncio
import logging
from datetime import datetime, timezone
from typing import Dict, Set, AsyncGenerator, Any, Optional

logger = logging.getLogger("agentos.events")


class ExecutionEventHub:
    """
    Central event broadcaster for execution SSE streams.
    Supports real-time pub/sub per execution_id and catch-up from SQLite.
    """
    def __init__(self):
        # execution_id -> Set of asyncio.Queue
        self._subscribers: Dict[str, Set[asyncio.Queue]] = {}

    def subscribe(self, execution_id: str) -> asyncio.Queue:
        """Subscribes a client to live events for an execution."""
        q: asyncio.Queue = asyncio.Queue()
        if execution_id not in self._subscribers:
            self._subscribers[execution_id] = set()
        self._subscribers[execution_id].add(q)
        return q

    def unsubscribe(self, execution_id: str, q: asyncio.Queue):
        """Unsubscribes a client queue."""
        if execution_id in self._subscribers:
            self._subscribers[execution_id].discard(q)
            if len(self._subscribers[execution_id]) == 0:
                del self._subscribers[execution_id]

    async def broadcast(self, execution_id: str, event_type: str, data: Dict[str, Any]):
        """
        Broadcasts an event to all active live subscribers for an execution.
        """
        if "timestamp" not in data:
            data["timestamp"] = datetime.now(timezone.utc).isoformat()
        if "executionId" not in data:
            data["executionId"] = execution_id

        event_payload = {
            "event": event_type,
            "data": data
        }

        queues = list(self._subscribers.get(execution_id, set()))
        for q in queues:
            try:
                q.put_nowait(event_payload)
            except Exception as e:
                logger.warning(f"Failed to queue event for subscriber on execution {execution_id}: {e}")


# Global event hub instance
event_hub = ExecutionEventHub()


# ---------------------------------------------------------------------------
# Persistent Team Messages (Sequential DAG Team-Chat)
# ---------------------------------------------------------------------------
TEAM_MESSAGE_EVENT_TYPE = "team.message"


def build_team_message_event(
    execution_id: str,
    turn: int,
    from_node_id: str,
    from_display_name: str,
    from_role: str,
    to_node_ids: list,
    excerpt: str,
    full_output_ref: str,
    timestamp: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Builds a strictly-schema'd team.message payload for SQLite + SSE.
    excerpt is capped at 400 chars by the caller.
    """
    ts = timestamp or datetime.now(timezone.utc).isoformat()
    return {
        "executionId": execution_id,
        "turn": int(turn),
        "fromNodeId": from_node_id,
        "fromDisplayName": from_display_name,
        "fromRole": from_role,
        "toNodeIds": list(to_node_ids or []),
        "excerpt": (excerpt or "")[:400],
        "fullOutputRef": full_output_ref,
        "timestamp": ts,
    }


async def persist_and_broadcast_team_message(
    execution_id: str,
    db,
    payload: Dict[str, Any],
    node_execution_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Writes team.message to SQLite (execution_events) BEFORE/SIDE-BY-SIDE with
    the SSE broadcast so reconnects replay deterministically from the database.
    """
    # Lazy import to avoid circular dependencies (repository -> models only).
    from backend.app.db.repository import ExecutionRepository

    repo = ExecutionRepository(db)
    repo.record_event(
        execution_id=execution_id,
        node_execution_id=node_execution_id,
        event_type=TEAM_MESSAGE_EVENT_TYPE,
        event_json=json.dumps(payload, ensure_ascii=False),
    )
    await event_hub.broadcast(execution_id, TEAM_MESSAGE_EVENT_TYPE, dict(payload))
    return payload


async def stream_execution_events(
    execution_id: str,
    past_events: Optional[list] = None
) -> AsyncGenerator[Dict[str, str], None]:
    """
    Async generator yielding formatted SSE events.
    First yields past events (reconnection catch-up from SQLite),
    then waits on live event bus until completion or disconnect.
    """
    # 1. Replay historical events from SQLite if provided
    if past_events:
        for ev in past_events:
            ev_data = ev.event_json
            if isinstance(ev_data, str):
                try:
                    ev_data = json.loads(ev_data)
                except Exception:
                    ev_data = {"raw": ev_data}
            yield {
                "event": ev.event_type,
                "data": json.dumps(ev_data)
            }

    # 2. Subscribe to live event queue
    q = event_hub.subscribe(execution_id)
    try:
        while True:
            # Wait for next event with a periodic heartbeat to prevent connection timeout
            try:
                ev = await asyncio.wait_for(q.get(), timeout=15.0)
                yield {
                    "event": ev["event"],
                    "data": json.dumps(ev["data"])
                }
                # Terminate stream on terminal events
                if ev["event"] in ("execution.finished", "execution.failed", "execution.cancelled"):
                    break
            except asyncio.TimeoutError:
                # Keep-alive heartbeat ping
                yield {
                    "event": "ping",
                    "data": json.dumps({"timestamp": datetime.now(timezone.utc).isoformat()})
                }
    finally:
        event_hub.unsubscribe(execution_id, q)
