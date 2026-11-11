"""Per-profile engine selection for legacy mission worker tasks.

Fake runtimes only: no CLI is spawned and no model is ever called.
"""
import json

import pytest

from backend.app.config import settings
from backend.app.db.models import AgentProfile, MissionTask
from backend.app.db.repository import AgentProfileRepository, MissionRepository
from backend.app.orchestration.ceo import _default_runtime_identity, run_mission_background
from backend.app.runtime.base import AgentResult, ModelCatalogEntry, ModelCost
from backend.tests.conftest import TestingSessionLocal

PLAN = json.dumps({
    "objective": "x",
    "tasks": [
        {"key": "a", "employee_role": "researcher", "title": "A", "instructions": "do a"},
        {"key": "b", "employee_role": "coder", "title": "B", "instructions": "do b"},
    ],
})
WORKER_OUT = json.dumps({"ok": True})


@pytest.mark.parametrize("mode,identity", [
    ("real", ("opencode", "opencode")),
    ("mock", ("mock", "mock")),
    ("claude", ("claude-code", "anthropic")),
    ("codex", ("codex", "openai")),
    ("agy", ("agy-legacy-cli", "antigravity")),
])
def test_default_engine_ledger_identity_matches_runtime_mode(monkeypatch, mode, identity):
    monkeypatch.setattr(settings, "RUNTIME_MODE", mode)
    assert _default_runtime_identity() == identity


class FakeRuntime:
    def __init__(self, name, models=()):
        self.name = name
        self.models = [ModelCatalogEntry(id=m, name=m, provider_id=name, cost=ModelCost(),
                                         variants=[], is_free=False, status="unverified")
                       for m in models]
        self.tasks = []

    async def list_available_models(self, free_only=True):
        return [] if free_only else list(self.models)

    async def execute_task(self, task):
        self.tasks.append(task)
        return AgentResult(text=WORKER_OUT, model_id=task.model_id, session_id=f"{self.name}-s")

    async def abort_task(self, session_id):
        return None


class DefaultRuntime(FakeRuntime):
    """Server default: serves planning, synthesis and engine-less workers."""

    async def execute_task(self, task):
        self.tasks.append(task)
        p = task.prompt.lower()
        if "you are the '" in p:
            text = WORKER_OUT
        elif "available employee roles" in p:
            text = PLAN
        else:
            text = json.dumps({"summary": "s"})
        return AgentResult(text=text, model_id=task.model_id, session_id="default-s")


def _profiles(db, engine_a, engine_b, model_a="m-a", model_b="m-b"):
    db.query(AgentProfile).delete()
    db.commit()
    repo = AgentProfileRepository(db)
    repo.create({"id": "pa", "name": "A", "role_type": "researcher", "instructions": "i",
                 "default_model_id": model_a, "engine": engine_a})
    repo.create({"id": "pb", "name": "B", "role_type": "coder", "instructions": "i",
                 "default_model_id": model_b, "engine": engine_b})


def _tasks(db, mid):
    db.expire_all()
    return {t.task_key: t for t in db.query(MissionTask).filter_by(mission_id=mid).all()}


async def _run(db, **kw):
    m = MissionRepository(db).create_mission("engine selection")
    res = await run_mission_background(m.id, db_factory=TestingSessionLocal, **kw)
    return m.id, res


@pytest.mark.asyncio
async def test_two_workers_choose_different_engines(db_session, monkeypatch):
    monkeypatch.setattr(settings, "MISSION_MAX_RETRIES", 0)
    _profiles(db_session, "claude-code", "codex", "sonnet", "gpt-5")
    default = DefaultRuntime("default")
    claude = FakeRuntime("claude", ["sonnet"])
    codex = FakeRuntime("codex", ["gpt-5"])
    made = {"claude-code": claude, "codex": codex}
    mid, res = await _run(db_session, runtime=default, engine_runtime_factory=made.__getitem__)

    assert res["status"] == "succeeded"
    assert [t.provider_id for t in claude.tasks] == ["anthropic"]
    assert [t.provider_id for t in codex.tasks] == ["openai"]
    assert claude.tasks[0].model_id == "sonnet" and codex.tasks[0].model_id == "gpt-5"
    # Default runtime saw only planner + synthesis, both still provider "opencode".
    assert len(default.tasks) == 2
    assert {t.provider_id for t in default.tasks} == {"opencode"}
    tasks = _tasks(db_session, mid)
    assert json.loads(tasks["a"].output_json)["engine"] == "claude-code"
    assert json.loads(tasks["b"].output_json)["engine"] == "codex"


@pytest.mark.asyncio
async def test_null_engine_preserves_default_behaviour(db_session, monkeypatch):
    monkeypatch.setattr(settings, "MISSION_MAX_RETRIES", 0)
    _profiles(db_session, None, None)

    def boom(_engine):
        raise AssertionError("factory must not be used without a profile engine")

    default = DefaultRuntime("default")
    mid, res = await _run(db_session, runtime=default, engine_runtime_factory=boom)
    assert res["status"] == "succeeded"
    assert len(default.tasks) == 4  # plan + 2 workers + synthesis
    assert {t.provider_id for t in default.tasks} == {"opencode"}
    for t in _tasks(db_session, mid).values():
        assert "engine" not in json.loads(t.output_json)


@pytest.mark.asyncio
async def test_unknown_model_for_engine_fails_honestly_without_call(db_session, monkeypatch):
    monkeypatch.setattr(settings, "MISSION_MAX_RETRIES", 2)
    _profiles(db_session, "claude-code", None, model_a="not-a-claude-model")
    claude = FakeRuntime("claude", ["sonnet"])
    mid, res = await _run(db_session, runtime=DefaultRuntime("default"),
                          engine_runtime_factory=lambda e: claude)
    tasks = _tasks(db_session, mid)
    assert claude.tasks == []
    assert tasks["a"].status == "failed"
    assert "Engine selection failed" in tasks["a"].error_message
    assert "not-a-claude-model" in tasks["a"].error_message
    assert tasks["a"].attempt_count in (0, None)  # never retried
    assert tasks["b"].status == "succeeded"
    assert res["status"] == "partially_succeeded"


@pytest.mark.asyncio
async def test_variant_rejected_when_engine_does_not_list_it(db_session, monkeypatch):
    monkeypatch.setattr(settings, "MISSION_MAX_RETRIES", 0)
    _profiles(db_session, "claude-code", None, model_a="sonnet")
    db_session.query(AgentProfile).filter_by(id="pa").update({"default_variant_id": "high"})
    db_session.commit()
    claude = FakeRuntime("claude", ["sonnet"])
    mid, _ = await _run(db_session, runtime=DefaultRuntime("default"),
                        engine_runtime_factory=lambda e: claude)
    t = _tasks(db_session, mid)["a"]
    assert t.status == "failed" and "Variant 'high'" in t.error_message
    assert claude.tasks == []


@pytest.mark.asyncio
async def test_unsupported_engine_fails_and_never_falls_back(db_session, monkeypatch):
    monkeypatch.setattr(settings, "MISSION_MAX_RETRIES", 0)
    _profiles(db_session, "gemini", None)  # bypasses API validation on purpose
    default = DefaultRuntime("default")
    mid, res = await _run(db_session, runtime=default)
    t = _tasks(db_session, mid)["a"]
    assert t.status == "failed" and "Unsupported engine 'gemini'" in t.error_message
    assert len(default.tasks) == 3  # plan + worker b + synthesis; worker a never ran


@pytest.mark.asyncio
async def test_mock_engine_only_in_mock_mode(db_session, monkeypatch):
    monkeypatch.setattr(settings, "MISSION_MAX_RETRIES", 0)
    monkeypatch.setattr(settings, "RUNTIME_MODE", "real")
    _profiles(db_session, "mock", None)
    mid, _ = await _run(db_session, runtime=DefaultRuntime("default"))
    t = _tasks(db_session, mid)["a"]
    assert t.status == "failed" and "mock mode" in t.error_message


@pytest.mark.asyncio
async def test_factory_failure_is_reported_not_swallowed(db_session, monkeypatch):
    monkeypatch.setattr(settings, "MISSION_MAX_RETRIES", 0)
    _profiles(db_session, "codex", None, model_a="gpt-5")

    def broken(engine):
        raise RuntimeError("codex CLI missing")

    mid, _ = await _run(db_session, runtime=DefaultRuntime("default"), engine_runtime_factory=broken)
    t = _tasks(db_session, mid)["a"]
    assert t.status == "failed" and "Engine 'codex' is unavailable" in t.error_message


@pytest.mark.asyncio
async def test_runtime_error_from_selected_engine_fails_task(db_session, monkeypatch):
    monkeypatch.setattr(settings, "MISSION_MAX_RETRIES", 0)
    _profiles(db_session, "codex", None, model_a="gpt-5")
    codex = FakeRuntime("codex", ["gpt-5"])

    async def fail(task):
        raise RuntimeError("auth required")

    codex.execute_task = fail
    mid, res = await _run(db_session, runtime=DefaultRuntime("default"),
                          engine_runtime_factory=lambda e: codex)
    t = _tasks(db_session, mid)["a"]
    assert t.status == "failed" and t.output_json is None
    assert res["status"] == "partially_succeeded"


# --- profile API surface ---

def test_profile_api_engine_roundtrip_and_validation(client):
    r = client.post("/api/agent-profiles", json={
        "name": "E", "role_type": "coder", "instructions": "i", "engine": "codex"})
    assert r.status_code == 201 and r.json()["engine"] == "codex"
    pid = r.json()["id"]
    assert client.get(f"/api/agent-profiles/{pid}").json()["engine"] == "codex"

    assert client.put(f"/api/agent-profiles/{pid}", json={"engine": "claude-code"}).json()["engine"] == "claude-code"
    # Omitting engine leaves it untouched.
    assert client.put(f"/api/agent-profiles/{pid}", json={"name": "E2"}).json()["engine"] == "claude-code"
    # Explicit null clears to the server default.
    assert client.put(f"/api/agent-profiles/{pid}", json={"engine": None}).json()["engine"] is None

    assert client.put(f"/api/agent-profiles/{pid}", json={"engine": "gemini"}).status_code == 422
    assert client.post("/api/agent-profiles", json={
        "name": "M", "role_type": "coder", "instructions": "i", "engine": "mock"}).status_code in (201, 422)


def test_profile_api_default_engine_is_null(client):
    assert all(p["engine"] is None for p in client.get("/api/agent-profiles").json())


def test_profile_api_mock_engine_rejected_outside_mock_mode(client, monkeypatch):
    monkeypatch.setattr(settings, "RUNTIME_MODE", "real")
    r = client.post("/api/agent-profiles", json={
        "name": "M", "role_type": "coder", "instructions": "i", "engine": "mock"})
    assert r.status_code == 422
    monkeypatch.setattr(settings, "RUNTIME_MODE", "mock")
    r = client.post("/api/agent-profiles", json={
        "name": "M", "role_type": "coder", "instructions": "i", "engine": "mock"})
    assert r.status_code == 201
