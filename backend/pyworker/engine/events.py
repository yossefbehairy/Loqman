"""Event hub for live execution / mission subscribers, plus persisted team messages.

Collaborative multi-agent communication is a sequential DAG with persistent
message-passing (``team.message`` events + cumulative transcript injection),
which guarantees deterministic termination and no infinite-loop risk.

The hub is a plain in-process pub/sub keyed by execution or mission id. Live
delivery is best-effort; SQLite is the authority and replays on reconnect.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Set

from backend.pyworker.db.core import Database, new_id, ts

logger = logging.getLogger("agentos.events")
MAX_SUBSCRIBER_EVENTS = 256
TEAM_MESSAGE_EVENT_TYPE = "team.message"


class ExecutionEventHub:
    def __init__(self):
        self._subscribers: Dict[str, Set[asyncio.Queue]] = {}

    def subscribe(self, key: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=MAX_SUBSCRIBER_EVENTS)
        self._subscribers.setdefault(key, set()).add(q)
        return q

    def unsubscribe(self, key: str, q: asyncio.Queue) -> None:
        subs = self._subscribers.get(key)
        if subs is not None:
            subs.discard(q)
            if not subs:
                del self._subscribers[key]

    def subscriber_count(self) -> int:
        return sum(len(v) for v in self._subscribers.values())

    async def broadcast(self, key: str, event_type: str, data: Dict[str, Any]) -> None:
        if "timestamp" not in data:
            data["timestamp"] = datetime.now(timezone.utc).isoformat()
        if "executionId" not in data:
            data["executionId"] = key
        payload = {"event": event_type, "data": data}
        for q in list(self._subscribers.get(key, set())):
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                # Close slow clients so they reconnect and replay current state.
                self.unsubscribe(key, q)
                while not q.empty():
                    q.get_nowait()
                q.put_nowait({"event": "stream.overflow", "data": {}})


event_hub = ExecutionEventHub()


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
    """Strictly-schema'd team.message payload for SQLite + live streams."""
    return {
        "executionId": execution_id,
        "turn": int(turn),
        "fromNodeId": from_node_id,
        "fromDisplayName": from_display_name,
        "fromRole": from_role,
        "toNodeIds": list(to_node_ids or []),
        "excerpt": (excerpt or "")[:400],
        "fullOutputRef": full_output_ref,
        "timestamp": timestamp or datetime.now(timezone.utc).isoformat(),
    }


def record_event(db: Database, execution_id: str, event_type: str, event_json: str = "{}",
                 node_execution_id: Optional[str] = None) -> Dict[str, Any]:
    row = {"id": new_id(), "execution_id": execution_id, "node_execution_id": node_execution_id,
           "event_type": event_type, "event_json": event_json, "created_at": ts()}
    db.insert("execution_events", row)
    return row


async def persist_and_broadcast_team_message(
    execution_id: str,
    db: Database,
    payload: Dict[str, Any],
    node_execution_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Write team.message to SQLite BEFORE broadcasting so reconnects replay deterministically."""
    record_event(db, execution_id, TEAM_MESSAGE_EVENT_TYPE,
                 json.dumps(payload, ensure_ascii=False), node_execution_id)
    await event_hub.broadcast(execution_id, TEAM_MESSAGE_EVENT_TYPE, dict(payload))
    return payload
