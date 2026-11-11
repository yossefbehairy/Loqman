"""POST /api/missions/delegate executes through the persisted run manager (fake runtimes, test DB)."""
import asyncio
import json

import pytest

from backend.app.api.routes.missions import _run_explicit_delegation, cancel_mission_endpoint, mission_runs
from backend.app.config import settings
from backend.app.core.conversation_runs import ConversationRunManager
from backend.app.db.models import AgentProfile, ConversationRun, ConversationRunEvent, Mission, MissionTask, UsageEvent
from backend.app.db.repository import AgentProfileRepository, MissionRepository
from backend.app.runtime.base import AgentResult, ModelCatalogEntry, ModelCost
from backend.tests.conftest import TestingSessionLocal


class Rt:
    def __init__(self, name="default", models=(), text="plain deliverable", gate=None):
        self.name, self.text, self.gate, self.tasks, self.aborted = name, text, gate, [], []
        self.entered = asyncio.Event()
        self.models = [ModelCatalogEntry(id=m, name=m, provider_id=name, cost=ModelCost(), variants=[],
                                         is_free=False, status="unverified") for m in models]

    async def list_available_models(self, free_only=True):
        return [] if free_only else list(self.models)

    async def execute_task(self, task):
        self.tasks.append(task)
        self.entered.set()
        if self.gate:
            await self.gate.wait()
        return AgentResult(text=self.text, model_id=task.model_id, session_id=f"{self.name}-s")

    async def abort_task(self, session_id):
        self.aborted.append(session_id)


def _setup(db, engine=None):
    db.query(AgentProfile).delete()
    db.commit()
    p = AgentProfileRepository(db).create({"id": "pd", "name": "D", "role_type": "coder", "instructions": "i",
                                           "default_model_id": "m-x", "engine": engine})
    m = MissionRepository(db).create_mission("deliver")
    m.status = "queued"
    t = MissionRepository(db).create_task(m.id, "explicit_assignment", "coder", "T", "do", model_id="m-x",
                                          agent_profile_id="pd")
    db.commit()
    return m.id, t.id


def _runs(mid):
    s = TestingSessionLocal()
    try:
        return s.query(ConversationRun).filter_by(mission_id=mid).all()
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


@pytest.mark.asyncio
async def test_default_engine_delegation_is_persisted_plain_text_run(db_session):
    mid, tid = _setup(db_session)
    rt = Rt()
    await _run_explicit_delegation(mid, tid, "pd", session_factory=TestingSessionLocal, runtime=rt,
                                   run_manager=_manager())
    db_session.expire_all()
    mission, task = db_session.get(Mission, mid), db_session.get(MissionTask, tid)
    assert mission.status == "succeeded" and task.status == "succeeded"
    assert json.loads(mission.final_summary) == {"result": "plain deliverable"}  # plain text, not JSON-validated
    assert json.loads(task.output_json)["result"] == "plain deliverable"
    (run,) = _runs(mid)
    expected_engine = "mock" if settings.RUNTIME_MODE == "mock" else "opencode"
    assert (run.kind, run.status, run.attempt, run.mission_task_id, run.engine) == (
        "mission_task", "succeeded", 1, tid, expected_engine)
    assert run.result_text == "plain deliverable"
    assert _events(run.id) == ["queued", "running", "succeeded"]
    assert db_session.query(UsageEvent).filter_by(run_id=run.id).count() == 1
    api = mission_runs(mid, db_session)
    assert [(r["id"], r["assistant_content"]) for r in api] == [(run.id, "plain deliverable")]


@pytest.mark.asyncio
async def test_explicit_engine_uses_its_adapter_and_provider(db_session):
    mid, tid = _setup(db_session, "claude-code")
    default, claude = Rt("default"), Rt("claude", ["m-x"], text="from claude")
    await _run_explicit_delegation(mid, tid, "pd", session_factory=TestingSessionLocal, runtime=default,
                                   engine_runtime_factory=lambda e: claude, run_manager=_manager())
    assert default.tasks == [] and [t.provider_id for t in claude.tasks] == ["anthropic"]
    (run,) = _runs(mid)
    assert (run.engine, run.status) == ("claude-code", "succeeded")
    assert db_session.query(UsageEvent).filter_by(run_id=run.id, provider_id="anthropic").count() == 1
    db_session.expire_all()
    assert db_session.get(Mission, mid).status == "succeeded"


@pytest.mark.asyncio
async def test_explicit_engine_without_model_fails_without_fallback_or_run(db_session):
    mid, tid = _setup(db_session, "codex")
    default, codex = Rt("default"), Rt("codex", ["other-model"])
    await _run_explicit_delegation(mid, tid, "pd", session_factory=TestingSessionLocal, runtime=default,
                                   engine_runtime_factory=lambda e: codex, run_manager=_manager())
    assert default.tasks == [] and codex.tasks == [] and _runs(mid) == []
    db_session.expire_all()
    mission, task = db_session.get(Mission, mid), db_session.get(MissionTask, tid)
    assert (mission.status, task.status) == ("failed", "failed")
    assert "not offered by engine 'codex'" in task.error_message


@pytest.mark.asyncio
async def test_cancel_during_delegation_persists_cancel_aborts_and_ignores_late_output(db_session):
    mid, tid = _setup(db_session)
    gate = asyncio.Event()
    rt = Rt(gate=gate, text="LATE")
    job = asyncio.create_task(_run_explicit_delegation(
        mid, tid, "pd", session_factory=TestingSessionLocal, runtime=rt, run_manager=_manager()))
    await asyncio.wait_for(rt.entered.wait(), 5)
    s = TestingSessionLocal()
    try:
        cancel_mission_endpoint(mid, db=s)
    finally:
        s.close()
    for _ in range(100):  # cancel_mission() schedules persist-cancel + adapter abort on the loop
        if rt.aborted:
            break
        await asyncio.sleep(0.05)
    gate.set()  # provider answers after the cancel landed
    await asyncio.wait_for(job, 10)
    await asyncio.sleep(0.2)
    (run,) = _runs(mid)
    assert run.status == "cancelled" and run.result_text is None
    assert "succeeded" not in _events(run.id) and _events(run.id)[-1] == "cancelled"
    assert rt.aborted  # best-effort adapter abort reached the runtime
    db_session.expire_all()
    mission, task = db_session.get(Mission, mid), db_session.get(MissionTask, tid)
    assert (mission.status, task.status, mission.final_summary, task.output_json) == (
        "cancelled", "cancelled", None, None)


# ------------------------------------------------------------ HTTP preflight

class CatalogRt(Rt):
    """free_only=True lists only free entries; free_only=False lists the account catalog."""

    def __init__(self, name, free=(), account=(), **kw):
        super().__init__(name, **kw)
        mk = lambda m, f: ModelCatalogEntry(id=m, name=m, provider_id=name, cost=ModelCost(), variants=[],
                                            is_free=f, status="active" if f else "unverified")
        self._free = [mk(m, True) for m in free]
        self._all = self._free + [mk(m, False) for m in account]

    async def list_available_models(self, free_only=True):
        return list(self._free if free_only else self._all)


def _app_overrides(default, engines=None):
    from backend.app.main import app
    from backend.app.api.routes.missions import get_engine_runtime_factory
    from backend.app.runtime import get_runtime
    app.dependency_overrides[get_runtime] = lambda: default
    app.dependency_overrides[get_engine_runtime_factory] = lambda: (lambda e: (engines or {})[e])


def _delegate(client, model="claude-sonnet", **kw):
    return client.post("/api/missions/delegate", json={
        "conversation_id": "c1", "employee_role": "coder", "agent_profile_id": "pd", "title": "T",
        "instructions": "do it", "model_id": model, **kw})


def test_delegate_accepts_claude_account_model_absent_from_default_free_catalog(client, db_session):
    _setup(db_session, "claude-code")
    db_session.query(Mission).delete()
    db_session.commit()
    default = CatalogRt("default", free=["free-m"])
    claude = CatalogRt("claude", account=["claude-sonnet"], text="claude answer")
    _app_overrides(default, {"claude-code": claude})
    r = _delegate(client)
    assert r.status_code == 201, r.text
    mid = r.json()["id"]
    assert default.tasks == [] and [t.provider_id for t in claude.tasks] == ["anthropic"]
    runs = client.get(f"/api/missions/{mid}/runs").json()
    assert [(x["engine"], x["status"], x["assistant_content"]) for x in runs] == [
        ("claude-code", "succeeded", "claude answer")]


def test_delegate_rejects_invalid_engine_model_variant_without_creating_rows(client, db_session):
    _setup(db_session, "claude-code")
    db_session.query(MissionTask).delete()
    db_session.query(Mission).delete()
    db_session.commit()
    claude = CatalogRt("claude", account=["claude-sonnet"])
    # The default catalog HAS the model: it must not be used for an explicit engine.
    default = CatalogRt("default", free=["bogus-model"])
    _app_overrides(default, {"claude-code": claude})
    assert _delegate(client, "bogus-model").status_code == 400
    assert _delegate(client, "claude-sonnet", variant="high").status_code == 400  # no variants exposed
    db_session.query(AgentProfile).filter_by(id="pd").update({"engine": "nonsense"})
    db_session.commit()
    r = _delegate(client)
    assert r.status_code == 400 and "Unsupported engine" in r.json()["detail"]
    db_session.expire_all()
    assert db_session.query(Mission).count() == 0 and db_session.query(MissionTask).count() == 0
    assert default.tasks == [] and claude.tasks == []  # preflight lists only, never calls a model


def test_delegate_default_engine_keeps_opencode_free_only(client, db_session, monkeypatch):
    monkeypatch.setattr(settings, "RUNTIME_MODE", "real")
    _setup(db_session, None)
    default = CatalogRt("default", free=["free-m"], account=["paid-m"])
    _app_overrides(default)
    assert _delegate(client, "paid-m").status_code == 400
    assert _delegate(client, "free-m").status_code == 201


@pytest.mark.parametrize("mode,model,ok", [
    ("real", "free-m", True), ("real", "acct-m", False),
    ("claude", "acct-m", True), ("codex", "acct-m", True), ("agy", "acct-m", True),
    ("claude", "missing", False),
])
def test_create_mission_validates_against_configured_runtime_catalog(client, db_session, monkeypatch,
                                                                     mode, model, ok):
    monkeypatch.setattr(settings, "RUNTIME_MODE", mode)
    _setup(db_session, None)
    db_session.query(Mission).delete()
    db_session.commit()
    _app_overrides(CatalogRt("default", free=["free-m"], account=["acct-m"], text="not a plan"))
    r = client.post("/api/missions", json={"objective": "build a thing", "model_id": model})
    assert (r.status_code == 201) is ok, r.text
    db_session.expire_all()
    assert db_session.query(Mission).count() == (1 if ok else 0)
