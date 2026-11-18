"""Event hub for live mission subscribers.

The hub is a plain in-process pub/sub keyed by mission id. Live
delivery is best-effort; SQLite is the authority and replays on reconnect.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Set

logger = logging.getLogger("agentos.events")
MAX_SUBSCRIBER_EVENTS = 256


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
