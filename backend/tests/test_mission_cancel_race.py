"""Regression: a late runtime response must never turn a cancelled
mission/task into success (docs/AUDIT-2026-11-05-AR.md, finding 1)."""
import asyncio
import json

import pytest

from backend.app.api.routes.missions import _run_explicit_delegation, cancel_mission_endpoint
from backend.app.db.models import Mission, MissionTask
from backend.app.db.repository import AgentProfileRepository, MissionRepository
from backend.app.orchestration.ceo import run_mission_background
from backend.app.runtime.base import AgentResult
from backend.tests.conftest import TestingSessionLocal

PLAN = json.dumps({
    "objective": "x",
    "tasks": [{"key": "t1", "employee_role": "analyst", "title": "T", "instructions": "do it"}],
})


class GatedRuntime:
    """Replies from `replies`; the call at `gate_at` blocks until released."""

    def __init__(self, replies, gate_at):
        self.replies = replies
        self.gate_at = gate_at
        self.calls = 0
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def execute_task(self, task):
        idx = self.calls
        self.calls += 1
        if idx == self.gate_at:
            self.entered.set()
            await self.release.wait()
        return AgentResult(text=self.replies[min(idx, len(self.replies) - 1)],
                           model_id=task.model_id, session_id=f"s{idx}")

    async def abort_task(self, session_id):
        return None


@pytest.fixture
def seeded(db_session):
    AgentProfileRepository(db_session).seed_defaults()
    profile = AgentProfileRepository(db_session).list_all()[0]
    return db_session, profile


def _cancel_via_endpoint(mission_id):
    s = TestingSessionLocal()
    try:
        cancel_mission_endpoint(mission_id, db=s)
    finally:
        s.close()


def _fresh(mission_id):
    s = TestingSessionLocal()
    try:
        m = s.query(Mission).filter_by(id=mission_id).one()
        tasks = s.query(MissionTask).filter_by(mission_id=mission_id).all()
        return m.status, m.final_summary, [(t.status, t.output_json) for t in tasks]
    finally:
        s.close()


async def _run_gated(rt, runner, cancel):
    job = asyncio.create_task(runner())
    await asyncio.wait_for(rt.entered.wait(), 5)
    cancel()
    rt.release.set()
    return await asyncio.wait_for(job, 10)


@pytest.mark.asyncio
async def test_delegation_late_response_keeps_cancelled(seeded):
    db, profile = seeded
    m = MissionRepository(db).create_mission("deliver")
    t = MissionRepository(db).create_task(m.id, "d", profile.role_type, "T", "do")
    rt = GatedRuntime(["LATE SUCCESS TEXT"], gate_at=0)
    await _run_gated(
        rt,
        lambda: _run_explicit_delegation(m.id, t.id, profile.id,
                                         session_factory=TestingSessionLocal, runtime=rt),
        lambda: _cancel_via_endpoint(m.id),
    )
    status, summary, tasks = _fresh(m.id)
    assert status == "cancelled"
    assert summary is None
    assert tasks == [("cancelled", None)]


@pytest.mark.asyncio
async def test_delegation_cancel_persisted_in_db_only(seeded):
    """Cancel recorded by another process (no in-memory flag) is still honoured."""
    db, profile = seeded
    m = MissionRepository(db).create_mission("deliver")
    t = MissionRepository(db).create_task(m.id, "d", profile.role_type, "T", "do")
    mid = m.id

    def db_only_cancel():
        s = TestingSessionLocal()
        s.query(Mission).filter_by(id=mid).update({"status": "cancelled"})
        s.query(MissionTask).filter_by(mission_id=mid).update({"status": "cancelled"})
        s.commit()
        s.close()

    rt = GatedRuntime(["LATE"], gate_at=0)
    await _run_gated(
        rt,
        lambda: _run_explicit_delegation(mid, t.id, profile.id,
                                         session_factory=TestingSessionLocal, runtime=rt),
        db_only_cancel,
    )
    status, summary, tasks = _fresh(mid)
    assert (status, summary, tasks) == ("cancelled", None, [("cancelled", None)])


@pytest.mark.asyncio
async def test_delegation_not_cancelled_still_succeeds(seeded):
    db, profile = seeded
    m = MissionRepository(db).create_mission("deliver")
    t = MissionRepository(db).create_task(m.id, "d", profile.role_type, "T", "do")
    rt = GatedRuntime(["real answer"], gate_at=-1)
    await _run_explicit_delegation(m.id, t.id, profile.id,
                                   session_factory=TestingSessionLocal, runtime=rt)
    status, summary, tasks = _fresh(m.id)
    assert status == "succeeded" and tasks[0][0] == "succeeded"
    assert "real answer" in summary


@pytest.mark.asyncio
@pytest.mark.parametrize("gate_at,stage", [(0, "planning"), (1, "execution"), (2, "synthesis")])
async def test_pipeline_cancel_during_stage_stays_cancelled(seeded, gate_at, stage):
    db, _ = seeded
    m = MissionRepository(db).create_mission("pipeline objective")
    rt = GatedRuntime([PLAN, '{"ok": true}', '{"summary": "LATE SUMMARY"}'], gate_at=gate_at)
    result = await _run_gated(
        rt,
        lambda: run_mission_background(m.id, db_factory=TestingSessionLocal, runtime=rt),
        lambda: _cancel_via_endpoint(m.id),
    )
    assert result["status"] == "cancelled", stage
    status, summary, tasks = _fresh(m.id)
    assert status == "cancelled"
    assert summary is None
    if stage == "synthesis":
        # Work finished before the cancel; only the late summary is discarded.
        assert [s for s, _ in tasks] == ["succeeded"]
    else:
        assert all(s == "cancelled" and out is None for s, out in tasks)
