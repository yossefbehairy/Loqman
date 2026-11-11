import pytest

from backend.app.db.repository import MissionRepository
from backend.app.orchestration import ceo


@pytest.mark.asyncio
@pytest.mark.parametrize("task_status", ["running", "succeeded", "failed"])
async def test_lost_transition_does_not_invent_cancellation(db_session, monkeypatch, task_status):
    repo = MissionRepository(db_session)
    mission = repo.create_mission("state reporting")
    mission.status = "running"
    task = repo.create_task(mission.id, "a", "reviewer", "A", "Review")
    task.status = task_status
    db_session.commit()
    events = []

    async def broadcast(mid, kind, data):
        events.append(data)

    monkeypatch.setattr(ceo, "_broadcast", broadcast)
    await ceo._finalize_task_cancelled(db_session, mission.id, task)
    db_session.refresh(task)
    assert task.status == task_status
    assert events[-1]["status"] == task_status
