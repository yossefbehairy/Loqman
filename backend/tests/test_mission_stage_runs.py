"""Mission planning + synthesis model calls are persisted runs (fake runtimes, test DB only)."""
import asyncio
import json

import pytest

from backend.app.config import settings
from backend.app.core.conversation_runs import ConversationRunManager
from backend.app.db.models import AgentProfile, ConversationRun, ConversationRunEvent, Mission, UsageEvent
from backend.app.db.repository import AgentProfileRepository, MissionRepository
from backend.app.orchestration.ceo import cancel_mission, cancel_mission_rows, run_mission_background
from backend.app.runtime.base import AgentResult
from backend.tests.conftest import TestingSessionLocal

PLAN = json.dumps({"objective": "x", "tasks": [
    {"key": "a", "employee_role": "researcher", "title": "A", "instructions": "do a"}]})


class Rt:
    def __init__(self, plan=PLAN, plan_gate=None, syn_gate=None):
        self.plan, self.plan_gate, self.syn_gate = plan, plan_gate, syn_gate
        self.started = {"plan": asyncio.Event(), "syn": asyncio.Event()}
        self.aborted = []

    async def list_available_models(self, free_only=True):
        return []

    async def execute_task(self, task):
        p = task.prompt.lower()
        if "you are the '" in p:
            out = json.dumps({"ok": True})
        elif "available employee roles" in p:
            self.started["plan"].set()
            if self.plan_gate:
                await self.plan_gate.wait()
            out = self.plan
        else:
            self.started["syn"].set()
            if self.syn_gate:
                await self.syn_gate.wait()
            out = json.dumps({"summary": "s"})
        return AgentResult(text=out, model_id=task.model_id, session_id="s")

    async def abort_task(self, session_id):
        self.aborted.append(session_id)


def _setup(db):
    db.query(AgentProfile).delete()
    db.commit()
    AgentProfileRepository(db).create({"id": "pa", "name": "A", "role_type": "researcher",
                                       "instructions": "i", "default_model_id": "m-a"})


def _runs(mid):
    s = TestingSessionLocal()
    try:
        return s.query(ConversationRun).filter_by(mission_id=mid).order_by(ConversationRun.created_at).all()
    finally:
        s.close()


def _events(rid):
    s = TestingSessionLocal()
    try:
        return [e.type for e in s.query(ConversationRunEvent).filter_by(run_id=rid).order_by(ConversationRunEvent.id)]
    finally:
        s.close()


def _manager():
    return ConversationRunManager(session_factory=TestingSessionLocal)


def _by_kind(mid):
    return {r.kind: r for r in _runs(mid) if r.kind != "mission_task"}


@pytest.mark.asyncio
async def test_planner_and_synthesis_are_persisted_runs(db_session, monkeypatch):
    monkeypatch.setattr(settings, "MISSION_MAX_RETRIES", 0)
    _setup(db_session)
    m = MissionRepository(db_session).create_mission("stages")
    res = await run_mission_background(m.id, db_factory=TestingSessionLocal, runtime=Rt(), run_manager=_manager())
    assert res["status"] == "succeeded"
    runs = _by_kind(m.id)
    assert set(runs) == {"mission_planner", "mission_synthesis"}
    for r in runs.values():
        assert (r.status, r.attempt, r.mission_task_id, r.conversation_id) == ("succeeded", 1, None, None)
        assert r.engine in ("mock", "opencode") and r.model_id and r.result_text
        assert _events(r.id) == ["queued", "running", "succeeded"]
        assert db_session.query(UsageEvent).filter_by(run_id=r.id, kind=r.kind).count() == 1
    from backend.app.api.routes.missions import mission_runs
    api = {row["kind"]: row for row in mission_runs(m.id, db_session)}
    assert api["mission_planner"]["assistant_content"] == runs["mission_planner"].result_text
    assert json.loads(api["mission_synthesis"]["assistant_content"]) == {"summary": "s"}
    assert len(api) == 3  # planner + 1 worker + synthesis


@pytest.mark.asyncio
async def test_invalid_plan_fails_run_and_mission(db_session):
    _setup(db_session)
    m = MissionRepository(db_session).create_mission("bad plan")
    res = await run_mission_background(m.id, db_factory=TestingSessionLocal, runtime=Rt(plan="not a plan"),
                                       run_manager=_manager())
    assert res["status"] == "failed" and "Planning failed" in res["error"]
    (run,) = _runs(m.id)
    assert (run.kind, run.status, run.result_text) == ("mission_planner", "failed", None)
    assert run.error
    assert _events(run.id) == ["queued", "running", "failed"]
    db_session.expire_all()
    assert db_session.get(Mission, m.id).status == "failed"


def test_stage_run_cannot_transition_after_mission_is_terminal(db_session):
    _setup(db_session)
    mission = MissionRepository(db_session).create_mission("already done")
    mission.status = "succeeded"
    run = ConversationRun(kind="mission_planner", mission_id=mission.id, conversation_id=None,
                          mission_task_id=None, attempt=1, engine="opencode", model_id="m",
                          status="running")
    db_session.add(run)
    db_session.commit()

    applied = _manager()._mission_guarded_db(
        run.id, "running", "succeeded", result_text='{"late": true}'
    )

    assert applied is None
    db_session.refresh(run)
    assert run.status == "running" and run.result_text is None


async def _cancel_during(db_session, stage):
    _setup(db_session)
    gate = asyncio.Event()
    rt = Rt(plan_gate=gate if stage == "plan" else None, syn_gate=gate if stage == "syn" else None)
    m = MissionRepository(db_session).create_mission("cancel " + stage)
    job = asyncio.create_task(run_mission_background(m.id, db_factory=TestingSessionLocal, runtime=rt,
                                                     run_manager=_manager()))
    await asyncio.wait_for(rt.started[stage].wait(), 10)
    s = TestingSessionLocal()
    try:
        assert cancel_mission_rows(s, m.id)
    finally:
        s.close()
    cancel_mission(m.id)
    gate.set()  # provider answers AFTER the cancel landed
    res = await asyncio.wait_for(job, 15)
    await asyncio.sleep(0.2)
    assert res["status"] == "cancelled"
    return m.id


@pytest.mark.asyncio
@pytest.mark.parametrize("stage,kind", [("plan", "mission_planner"), ("syn", "mission_synthesis")])
async def test_cancel_during_stage_prevents_late_success(db_session, monkeypatch, stage, kind):
    monkeypatch.setattr(settings, "MISSION_MAX_RETRIES", 0)
    mid = await _cancel_during(db_session, stage)
    run = _by_kind(mid)[kind]
    assert run.status == "cancelled" and run.result_text is None
    assert "succeeded" not in _events(run.id) and _events(run.id)[-1] == "cancelled"
    db_session.expire_all()
    mission = db_session.get(Mission, mid)
    assert mission.status == "cancelled" and not mission.final_summary
    if stage == "plan":
        assert not mission.plan_json
