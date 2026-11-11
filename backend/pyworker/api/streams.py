"""stream.open / stream.close (see streams.py)."""

from __future__ import annotations

from backend.pyworker.core.conversation_runs import RunNotFound, TooManyRuns
from backend.pyworker.core import executions as ex
from backend.pyworker.core import missions as mission_repo
from backend.pyworker.errors import api_error
from backend.pyworker.validate import check_keys, opt_int, req_enum, req_str

from .registry import method


@method("stream.open")
async def stream_open(ctx, params):
    check_keys(params, {"topic", "id"}, ("last_event_id",))
    if ctx.streams is None:
        raise api_error(503, "Event streams are not available in this context.")
    topic = req_enum(params, "topic", ("run", "mission", "execution"))
    key = req_str(params, "id", 1, 64)
    last = opt_int(params, "last_event_id", 0, 0, 2**63 - 1)
    if topic == "run":
        try:
            ctx.runs.get_run(key)
        except RunNotFound:
            raise api_error(404, "Run not found")
    elif topic == "mission":
        if not mission_repo.get(ctx.db, key):
            raise api_error(404, "Mission not found")
    elif not ex.execution_get(ctx.db, key):
        raise api_error(404, "Execution not found")
    try:
        stream_id = ctx.streams.open(topic, key, last)
    except TooManyRuns:
        raise api_error(429, "Too many event streams")
    return {"stream_id": stream_id, "topic": topic, "id": key}


@method("stream.close")
async def stream_close(ctx, params):
    check_keys(params, {"stream_id"})
    if ctx.streams is None:
        raise api_error(503, "Event streams are not available in this context.")
    closed = await ctx.streams.close(req_str(params, "stream_id", 1, 64))
    return {"closed": closed}
