import pytest
from types import SimpleNamespace

from backend.app.engine.events import ExecutionEventHub, MAX_SUBSCRIBER_EVENTS, event_hub, stream_execution_events


@pytest.mark.asyncio
async def test_slow_subscriber_is_disconnected_at_bounded_queue_size():
    hub = ExecutionEventHub()
    queue = hub.subscribe("run")
    for index in range(MAX_SUBSCRIBER_EVENTS + 1):
        await hub.broadcast("run", "update", {"index": index})
    assert queue.qsize() == 1
    assert (await queue.get())["event"] == "stream.overflow"
    assert "run" not in hub._subscribers


@pytest.mark.asyncio
async def test_execution_subscribes_before_replay_is_sent():
    stream = stream_execution_events("stream-test", load_past=lambda: [
        SimpleNamespace(event_type="stored", event_json="{}"),
        SimpleNamespace(event_type="stored", event_json="{}"),
    ])
    try:
        assert (await anext(stream))["event"] == "stored"
        await event_hub.broadcast("stream-test", "live", {})
        assert (await anext(stream))["event"] == "stored"
        assert (await anext(stream))["event"] == "live"
    finally:
        await stream.aclose()
