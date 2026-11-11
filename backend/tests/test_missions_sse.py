"""
Missions SSE (PR4): GET /api/missions/{id}/events is the streaming contract.

- Replays the current mission status + each task status once on connect,
  then stays open with live event_hub broadcasts (ping keepalive).
- The renderer depends on this stream (no more polling): the replay MUST be
  the first thing a client receives so a reconnect never misses state.

The infinite live stream is intentionally NOT read to completion in tests
(TestClient deadlocks on never-ending StreamingResponses); the replay snapshot
is asserted directly and the stream lifecycle is covered by Playwright E2E.
"""
from datetime import datetime, timezone
import asyncio
import pytest

from backend.app.api.routes.missions import _mission_replay_events, mission_events
from backend.app.db.models import Mission, MissionTask
from backend.app.engine.events import event_hub


def _mission(status: str = "running") -> Mission:
    return Mission(
        id="m-sse-1", objective="obj", status=status, plan_json="{}",
        created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc),
    )


def _task(key: str, status: str) -> MissionTask:
    return MissionTask(
        id=f"t-{key}", mission_id="m-sse-1", task_key=key, title=key,
        employee_role="planner", status=status, attempt_count=1,
    )


def test_replay_starts_with_mission_status_then_each_task():
    m = _mission("running")
    tasks = [_task("plan", "succeeded"), _task("review", "running")]

    events = _mission_replay_events(m, tasks)

    assert events[0]["event"] == "mission.status.changed"
    assert events[0]["data"] == {"missionId": "m-sse-1", "status": "running"}
    assert [e["data"]["taskKey"] for e in events[1:]] == ["plan", "review"]
    assert [e["event"] for e in events[1:]] == ["task.status.changed", "task.status.changed"]


def test_replay_status_matches_current_mission_status():
    """A reconnecting client must catch up on the CURRENT status, not the origin."""
    m = _mission("failed")
    events = _mission_replay_events(m, [])
    assert events[0]["data"]["status"] == "failed"


def test_mission_events_unknown_mission_404(client):
    r = client.get("/api/missions/no-such-mission/events")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_live_event_during_snapshot_is_buffered(db_session):
    mission = _mission()
    task = _task("plan", "running")
    task.instructions = "work"
    db_session.add_all([mission, task])
    db_session.commit()
    response = await mission_events(mission.id, db_session)
    stream = response.body_iterator
    try:
        first = await anext(stream)
        assert "mission.status.changed" in first
        await event_hub.broadcast(mission.id, "task.status.changed", {"status": "succeeded"})
        assert "task.status.changed" in await anext(stream)  # snapshot
        live = await asyncio.wait_for(anext(stream), timeout=1)
        assert '"succeeded"' in live
    finally:
        await stream.aclose()
