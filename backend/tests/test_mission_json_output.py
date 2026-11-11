import json

import pytest

from backend.app.config import settings
from backend.app.db.models import MissionTask
from backend.app.db.repository import AgentProfileRepository, MissionRepository
from backend.app.orchestration.ceo import run_mission_background
from backend.tests.conftest import TestingSessionLocal
from backend.tests.test_mission_cancel_race import GatedRuntime, PLAN


@pytest.mark.asyncio
@pytest.mark.parametrize("output", ["not json", '{"incomplete":', '{"value": NaN}'])
async def test_invalid_task_json_is_failed_not_saved_as_success(db_session, monkeypatch, output):
    monkeypatch.setattr(settings, "MISSION_MAX_RETRIES", 0)
    AgentProfileRepository(db_session).seed_defaults()
    mission = MissionRepository(db_session).create_mission("structured output")
    runtime = GatedRuntime([PLAN, output, json.dumps({"summary": "done"})], gate_at=-1)

    result = await run_mission_background(mission.id, db_factory=TestingSessionLocal, runtime=runtime)
    db_session.expire_all()
    task = db_session.query(MissionTask).filter_by(mission_id=mission.id).one()
    assert result["status"] == "failed"
    assert task.status == "failed"
    assert task.output_json is None
    assert "invalid JSON" in task.error_message
    assert task.attempt_count == 1
