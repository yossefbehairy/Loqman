"""Mission worker attempts run through the persisted run manager.

Isolated: fake runtimes, test DB only, no CLI, no model call.
"""
import asyncio
import json

import pytest

from backend.app.config import settings
from backend.app.core.conversation_runs import ConversationRunManager
from backend.app.db.models import (
    AgentProfile, ConversationRun, ConversationRunEvent, Mission, MissionTask, UsageEvent,
)
from backend.app.db.repository import AgentProfileRepository, MissionRepository
from backend.app.orchestration.ceo import cancel_mission, run_mission_background
from backend.app.runtime.base import AgentResult, ModelCatalogEntry, ModelCost
from backend.tests.conftest import TestingSessionLocal

PLAN = json.dumps({"objective": "x", "tasks": [
    {"key": "a", "employee_role": "researcher", "title": "A", "instructions": "do a"},
    {"key": "b", "employee_role": "coder", "title": "B", "instructions": "do b", "depends_on": ["a"]},
]})


class Rt:
    def __init__(self, name="default", models=(), worker=None):
        self.name, self.tasks, self.aborted = name, [], []
        self.worker = worker or (lambda task, n: json.dumps({"ok": True}))
        self.models = [ModelCatalogEntry(id=m, name=m, provider_id=name, cost=ModelCost(), variants=[],
                                         is_free=False, status="unverified") for m in models]

    async def list_available_models(self, free_only=True):
        return [] if free_only else list(self.models)

    async def execute_task(self, task):
        self.tasks.append(task)
        p = task.prompt.lower()
        if "you are the '" in p:
            out = self.worker(task, len(self.tasks))
            if asyncio.iscoroutine(out):
                out = await out
        elif "available employee roles" in p:
            out = PLAN
        else:
            out = json.dumps({"summary": "s"})
        return AgentResult(text=out, model_id=task.model_id, session_id=f"{self.name}-s")

    async def abort_task(self, session_id):
        self.aborted.append(session_id)


def _profiles(db, ea=None, eb=None):
    db.query(AgentProfile).delete()
    db.commit()
    repo = AgentProfileRepository(db)
    repo.create({"id": "pa", "name": "A", "role_type": "researcher", "instructions": "i",
                 "default_model_id": "m-a", "engine": ea})
    repo.create({"id": "pb", "name": "B", "role_type": "coder", "instructions": "i",
                 "default_model_id": "m-b", "engine": eb})


def _runs(mission_id):
    s = TestingSessionLocal()
    try:
        return s.query(ConversationRun).filter_by(mission_id=mission_id).order_by(ConversationRun.created_at).all()
    finally:
        s.close()


def _events(run_id):
    s = TestingSessionLocal()
    try:
        return [e.type for e in s.query(ConversationRunEvent).filter_by(run_id=run_id).order_by(ConversationRunEvent.id)]
    finally:
        s.close()


def _manager():
    return ConversationRunManager(session_factory=TestingSessionLocal)


@pytest.mark.asyncio
async def test_mission_attempts_share_global_run_concurrency_limit(db_session):
    repo = MissionRepository(db_session)
    mission = repo.create_mission("bounded")
    mission.status = "running"
    tasks = [repo.create_task(mission.id, key, "reviewer", key, "Review") for key in ("one", "two")]
    for task in tasks:
        task.status = "running"
    db_session.commit()
    gate = asyncio.Event()
    started = asyncio.Event()
    calls = []

    class Runtime:
        async def execute_task(self, task):
            calls.append(task.session_title)
            started.set()
            await gate.wait()
            return AgentResult(text="{}", model_id=task.model_id)

        async def abort_task(self, key):
            pass

    manager = ConversationRunManager(session_factory=TestingSessionLocal, max_concurrent=1)

    async def run(task):
        return await manager.run_mission_task(runtime=Runtime(), mission_id=mission.id, task_id=task.id,
            attempt=1, engine="opencode", provider_id="opencode", model_id="m", variant=None,
            prompt="review", session_title=task.task_key, timeout=5)

    jobs = [asyncio.create_task(run(task)) for task in tasks]
    try:
        await asyncio.wait_for(started.wait(), 3)
        await asyncio.sleep(.05)
        assert len(calls) == 1
        gate.set()
        await asyncio.wait_for(asyncio.gather(*jobs), 5)
        assert len(calls) == 2
    finally:
        gate.set()
        await manager.shutdown()


async def _mission(db, **kw):
    m = MissionRepository(db).create_mission("persisted runs")
    res = await run_mission_background(m.id, db_factory=TestingSessionLocal, **kw)
    return m.id, res


@pytest.mark.asyncio
async def test_worker_attempts_are_persisted_runs_with_events_and_selected_engine(db_session, monkeypatch):
    monkeypatch.setattr(settings, "MISSION_MAX_RETRIES", 0)
    _profiles(db_session, "claude-code", "codex")
    claude, codex = Rt("claude", ["m-a"]), Rt("codex", ["m-b"])
    made = {"claude-code": claude, "codex": codex}
    mid, res = await _mission(db_session, runtime=Rt("default"), engine_runtime_factory=made.__getitem__,
                              run_manager=_manager())
    assert res["status"] == "succeeded"
    all_runs = _runs(mid)
    assert {r.kind for r in all_runs} == {"mission_task", "mission_planner", "mission_synthesis"}
    runs = {r.mission_task_id: r for r in all_runs if r.kind == "mission_task"}
    tasks = {t.task_key: t for t in db_session.query(MissionTask).filter_by(mission_id=mid)}
    assert len(runs) == 2
    ra, rb = runs[tasks["a"].id], runs[tasks["b"].id]
    assert (ra.kind, ra.engine, ra.status, ra.attempt, ra.conversation_id) == ("mission_task", "claude-code", "succeeded", 1, None)
    assert (rb.engine, rb.status) == ("codex", "succeeded")
    assert json.loads(ra.result_text) == {"ok": True}
    assert _events(ra.id) == ["queued", "running", "succeeded"]
    # The run id is the one recorded by the usage ledger for the actual engine call.
    assert db_session.query(UsageEvent).filter_by(run_id=ra.id, provider_id="anthropic").count() == 1
    assert [t.provider_id for t in claude.tasks] == ["anthropic"] and [t.provider_id for t in codex.tasks] == ["openai"]
    from backend.app.api.routes.missions import mission_runs
    assert {row["id"] for row in mission_runs(mid, db_session)} == {r.id for r in all_runs}


@pytest.mark.asyncio
async def test_default_runtime_and_retries_each_attempt_is_its_own_run(db_session, monkeypatch):
    monkeypatch.setattr(settings, "MISSION_MAX_RETRIES", 1)
    _profiles(db_session)
    calls = {"n": 0}

    def flaky(task, n):
        calls["n"] += 1
        return "not json" if calls["n"] == 1 else json.dumps({"ok": 1})

    mid, res = await _mission(db_session, runtime=Rt("default", worker=flaky), run_manager=_manager())
    assert res["status"] == "succeeded"
    runs = _runs(mid)
    ta = db_session.query(MissionTask).filter_by(mission_id=mid, task_key="a").one()
    a_runs = sorted((r for r in runs if r.mission_task_id == ta.id), key=lambda r: r.attempt)
    assert [(r.attempt, r.status) for r in a_runs] == [(1, "failed"), (2, "succeeded")]
    assert "invalid JSON" in a_runs[0].error  # invalid output is a failed run, not a success
    assert all(r.engine == "mock" or r.engine == "opencode" for r in runs)  # default runtime label
    assert _events(a_runs[0].id) == ["queued", "running", "failed"]


@pytest.mark.asyncio
async def test_dependency_skip_creates_no_run(db_session, monkeypatch):
    monkeypatch.setattr(settings, "MISSION_MAX_RETRIES", 0)
    _profiles(db_session)
    rt = Rt("default", worker=lambda task, n: "garbage")
    mid, res = await _mission(db_session, runtime=rt, run_manager=_manager())
    tasks = {t.task_key: t for t in db_session.query(MissionTask).filter_by(mission_id=mid)}
    assert tasks["a"].status == "failed" and tasks["b"].status == "skipped"
    assert {r.mission_task_id for r in _runs(mid) if r.kind == "mission_task"} == {tasks["a"].id}  # b never ran


@pytest.mark.asyncio
async def test_late_cancel_cannot_produce_succeeded_run_or_task(db_session, monkeypatch):
    monkeypatch.setattr(settings, "MISSION_MAX_RETRIES", 0)
    _profiles(db_session)
    started, release = asyncio.Event(), asyncio.Event()

    async def slow(task, n):
        started.set()
        await release.wait()  # provider answers AFTER the cancel landed
        return json.dumps({"late": True})

    class Uncancellable(Rt):
        async def abort_task(self, session_id):  # engine ignores abort: response still arrives
            self.aborted.append(session_id)

    rt = Uncancellable("default", worker=slow)
    manager = _manager()
    m = MissionRepository(db_session).create_mission("late cancel")
    job = asyncio.create_task(run_mission_background(m.id, db_factory=TestingSessionLocal, runtime=rt,
                                                     run_manager=manager))
    await asyncio.wait_for(started.wait(), 10)
    from backend.app.orchestration.ceo import cancel_mission_rows
    s = TestingSessionLocal()
    try:
        assert cancel_mission_rows(s, m.id)
    finally:
        s.close()
    cancel_mission(m.id)  # the endpoint's second step: cancels + aborts the persisted run
    release.set()
    res = await asyncio.wait_for(job, 15)
    assert res["status"] == "cancelled"
    await asyncio.sleep(0.2)
    all_runs = _runs(m.id)
    runs = [r for r in all_runs if r.kind == "mission_task"]
    assert runs and all(r.status == "cancelled" for r in runs)
    # The planner run finished before the cancel; no synthesis was ever started.
    assert [r.kind for r in all_runs if r.kind != "mission_task"] == ["mission_planner"]
    assert not any(r.status == "succeeded" or r.result_text for r in runs)
    assert "cancelled" in _events(runs[0].id)
    assert "succeeded" not in _events(runs[0].id)
    db_session.expire_all()
    assert {t.status for t in db_session.query(MissionTask).filter_by(mission_id=m.id)} == {"cancelled"}
    assert db_session.get(Mission, m.id).status == "cancelled"


@pytest.mark.asyncio
async def test_db_guard_refuses_success_after_mission_cancel_without_runtime_abort(db_session):
    """Even if no abort/task-cancel ever reaches the run, the conditional UPDATE refuses success."""
    m = MissionRepository(db_session).create_mission("guard")
    t = MissionRepository(db_session).create_task(mission_id=m.id, task_key="k", employee_role="coder",
                                                  title="t", instructions="i", input_context="c", depends_on=[])
    release = asyncio.Event()

    async def slow(task, n):
        await release.wait()
        return json.dumps({"ok": 1})

    rt = Rt("default", worker=slow)
    manager = _manager()
    call = asyncio.create_task(manager.run_mission_task(
        runtime=rt, mission_id=m.id, task_id=t.id, attempt=1, engine="opencode", provider_id="opencode",
        model_id="m", variant=None, prompt="You are the 'coder' employee.", session_title="s", timeout=30))
    for _ in range(100):
        await asyncio.sleep(0.05)
        if _runs(m.id) and _runs(m.id)[0].status == "running":
            break
    s = TestingSessionLocal()
    from backend.app.orchestration.ceo import cancel_mission_rows
    try:
        cancel_mission_rows(s, m.id)  # row-level cancel only: no manager.cancel
    finally:
        s.close()
    release.set()
    from backend.app.core.conversation_runs import MissionRunCancelled
    with pytest.raises(MissionRunCancelled):
        await asyncio.wait_for(call, 10)
    (run,) = _runs(m.id)
    assert run.status == "cancelled" and run.result_text is None
    assert _events(run.id)[-1] == "cancelled"


@pytest.mark.asyncio
async def test_mission_runs_are_readable_through_run_api_and_recovered_when_interrupted(db_session):
    m = MissionRepository(db_session).create_mission("recover")
    t = MissionRepository(db_session).create_task(mission_id=m.id, task_key="k", employee_role="coder",
                                                  title="t", instructions="i", input_context="c", depends_on=[])
    manager = _manager()
    release = asyncio.Event()

    async def slow(task, n):
        await release.wait()
        return "{}"

    call = asyncio.create_task(manager.run_mission_task(
        runtime=Rt("default", worker=slow), mission_id=m.id, task_id=t.id, attempt=1, engine="opencode",
        provider_id="opencode", model_id="m", variant=None, prompt="You are the 'coder' employee.",
        session_title="s", timeout=30))
    for _ in range(100):
        await asyncio.sleep(0.05)
        if _runs(m.id) and _runs(m.id)[0].status == "running":
            break
    run_id = _runs(m.id)[0].id
    snap = manager.get_run(run_id)
    assert snap["kind"] == "mission_task" and snap["conversation_id"] is None and snap["mission_task_id"] == t.id
    # Restart simulation: a fresh manager marks the orphaned active run interrupted, re-executes nothing.
    assert _manager().recover_interrupted() == 1
    assert manager.get_run(run_id)["status"] == "interrupted"
    call.cancel()
    await asyncio.gather(call, return_exceptions=True)
    await manager.shutdown()
