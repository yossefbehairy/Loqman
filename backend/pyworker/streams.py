"""Event streams: the pipe equivalent of the legacy SSE endpoints.

``stream.open`` returns a ``stream_id`` and the worker then pushes
``{"type":"stream","stream_id":..,"event":<name>,"id":<int|null>,"data":{..}}``
lines, ending with ``{"type":"stream_end","stream_id":..,"reason":..}``.
Run streams replay from SQLite after ``last_event_id`` (gap-free resume);
mission/execution streams replay their persisted state first, then tail live.
Delivery applies back-pressure to the stream task only, never to the worker.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any, Awaitable, Callable, Dict, Optional

from .core.conversation_runs import RunNotFound, TooManyRuns
from .engine.events import event_hub

logger = logging.getLogger("agentos.streams")

MAX_STREAMS = 64
EXECUTION_TERMINAL = ("execution.finished", "execution.failed", "execution.cancelled")
Emit = Callable[[Dict[str, Any]], Awaitable[None]]


class StreamRegistry:
    def __init__(self, ctx, emit: Emit, max_streams: int = MAX_STREAMS):
        self._ctx = ctx
        self._emit = emit
        self._max = max_streams
        self._streams: Dict[str, asyncio.Task] = {}

    @property
    def count(self) -> int:
        return len(self._streams)

    def open(self, topic: str, key: str, last_event_id: int = 0) -> str:
        if len(self._streams) >= self._max:
            raise TooManyRuns("Too many event streams")
        sid = uuid.uuid4().hex
        pump = {"run": self._pump_run, "mission": self._pump_mission,
                "execution": self._pump_execution}[topic]
        task = asyncio.get_running_loop().create_task(self._guard(sid, pump, key, last_event_id),
                                                      name=f"stream-{topic}-{sid[:8]}")
        self._streams[sid] = task
        task.add_done_callback(lambda _t, s=sid: self._streams.pop(s, None))
        return sid

    async def close(self, stream_id: str) -> bool:
        task = self._streams.get(stream_id)
        if task is None:
            return False
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        return True

    async def close_all(self) -> None:
        tasks = list(self._streams.values())
        for t in tasks:
            t.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _guard(self, sid: str, pump, key: str, last: int) -> None:
        reason = "complete"
        try:
            await pump(sid, key, last)
        except asyncio.CancelledError:
            reason = "closed"
            raise
        except Exception:
            logger.exception("stream %s failed", sid)
            reason = "error"
        finally:
            try:
                await asyncio.shield(self._emit({"type": "stream_end", "stream_id": sid, "reason": reason}))
            except Exception:
                pass

    def _msg(self, sid: str, event: str, data: Any, id_: Optional[int] = None) -> Dict[str, Any]:
        return {"type": "stream", "stream_id": sid, "event": event, "id": id_, "data": data}

    async def _pump_run(self, sid: str, run_id: str, last: int) -> None:
        try:
            async for ev in self._ctx.runs.iter_events(run_id, last):
                if ev is None:
                    continue  # heartbeat: not needed on a pipe
                await self._emit(self._msg(sid, ev["type"], ev["data"], ev["id"]))
        except (RunNotFound, TooManyRuns):
            return

    async def _pump_mission(self, sid: str, mission_id: str, last: int) -> None:
        from .core import missions as mission_repo

        db = self._ctx.db
        q = event_hub.subscribe(mission_id)
        try:
            current = mission_repo.get(db, mission_id)
            if not current:
                return
            await self._emit(self._msg(sid, "mission.status.changed",
                                       {"missionId": mission_id, "status": current["status"]}))
            for t in mission_repo.tasks_for(db, mission_id):
                await self._emit(self._msg(sid, "task.status.changed", {
                    "missionId": mission_id, "taskKey": t["task_key"], "status": t["status"]}))
            while True:
                ev = await q.get()
                if ev["event"] == "stream.overflow":
                    return
                await self._emit(self._msg(sid, ev["event"], ev["data"]))
        finally:
            event_hub.unsubscribe(mission_id, q)

    async def _pump_execution(self, sid: str, execution_id: str, last: int) -> None:
        from .core import executions as ex

        db = self._ctx.db
        q = event_hub.subscribe(execution_id)  # subscribe BEFORE the DB read: no lost gap
        try:
            for e in ex.execution_events(db, execution_id):
                try:
                    data = json.loads(e["event_json"])
                except Exception:
                    data = {"raw": e["event_json"]}
                await self._emit(self._msg(sid, e["event_type"], data))
            while True:
                ev = await q.get()
                if ev["event"] == "stream.overflow":
                    return
                await self._emit(self._msg(sid, ev["event"], ev["data"]))
                if ev["event"] in EXECUTION_TERMINAL:
                    return
        finally:
            event_hub.unsubscribe(execution_id, q)
