"""Provider-aware model preflight on POST /api/missions and /api/missions/delegate.

Fake runtimes only; the preflight lists catalogs and never calls a model. Bad input
must be rejected with a 4xx before any mission/task row exists."""
import pytest

from backend.app.api.routes.missions import get_engine_runtime_factory
from backend.app.config import settings
from backend.app.db.models import AgentProfile, Mission, MissionTask
from backend.app.db.repository import AgentProfileRepository
from backend.app.main import app
from backend.app.runtime import set_runtime
from backend.app.runtime.base import AgentResult, ModelCatalogEntry, ModelCost


def _entry(mid, provider, free, variants=()):
    return ModelCatalogEntry(id=mid, name=mid, provider_id=provider, cost=ModelCost(), variants=list(variants),
                             is_free=free, status="active" if free else "unverified")


class CatRt:
    """free_only=True -> only free entries; free_only=False -> the whole account catalog."""

    def __init__(self, provider, entries):
        self.provider, self.entries, self.listed, self.tasks = provider, entries, [], []

    async def list_available_models(self, free_only=True):
        self.listed.append(free_only)
        return [e for e in self.entries if e.is_free or not free_only]

    async def execute_task(self, task):
        self.tasks.append(task)
        return AgentResult(text="done", model_id=task.model_id, session_id=f"{self.provider}-s")

    async def abort_task(self, session_id):
        return None


def _profile(db, engine):
    db.query(AgentProfile).delete()
    db.commit()
    AgentProfileRepository(db).create({"id": "pd", "name": "D", "role_type": "coder", "instructions": "i",
                                       "default_model_id": "m", "engine": engine})


def _delegate(client, model, variant=None):
    return client.post("/api/missions/delegate", json={
        "conversation_id": "c", "employee_role": "coder", "agent_profile_id": "pd", "title": "T",
        "instructions": "do it", "model_id": model, "variant": variant})


def _count(db):
    db.expire_all()
    return db.query(Mission).count(), db.query(MissionTask).count()


@pytest.fixture
def engines():
    made = {}
    app.dependency_overrides[get_engine_runtime_factory] = lambda: (lambda eid: made[eid])
    return made


def test_delegate_accepts_claude_account_model_absent_from_default_opencode_catalog(client, db_session, engines):
    _profile(db_session, "claude-code")
    default = CatRt("opencode", [_entry("free-model", "opencode", True)])
    claude = engines["claude-code"] = CatRt("anthropic", [_entry("claude-opus-x", "anthropic", False, ["high"])])
    set_runtime(default)
    r = _delegate(client, "claude-opus-x", "high")
    assert r.status_code == 201, r.text
    assert default.listed == [] and claude.listed == [False]  # own catalog, free_only=False, no default probe
    mission = client.get(f"/api/missions/{r.json()['id']}").json()
    assert mission["status"] == "succeeded", mission
    assert default.tasks == [] and [t.provider_id for t in claude.tasks] == ["anthropic"]
    assert claude.listed == [False]  # background reused the resolved adapter: no second probe
    runs = client.get(f"/api/missions/{r.json()['id']}/runs").json()
    assert [(x["engine"], x["status"]) for x in runs] == [("claude-code", "succeeded")]


@pytest.mark.parametrize("engine,model,variant,fragment", [
    ("claude-code", "nope", None, "not offered by engine 'claude-code'"),
    ("codex", "gpt-x", "turbo", "Variant 'turbo' is not supported"),  # model has no variants exposed
    ("antigravity", "nope", None, "not offered by engine 'antigravity'"),
    ("bogus", "gpt-x", None, "Unsupported engine"),
    ("mock", "gpt-x", None, "only available in explicit mock mode"),
])
def test_delegate_bad_selection_is_4xx_and_creates_no_rows(client, db_session, engines, monkeypatch,
                                                           engine, model, variant, fragment):
    monkeypatch.setattr(settings, "RUNTIME_MODE", "real")
    _profile(db_session, engine)
    default = CatRt("opencode", [_entry("gpt-x", "opencode", True)])  # default catalog must NOT rescue it
    for eid, prov in (("claude-code", "anthropic"), ("codex", "openai"), ("antigravity", "antigravity")):
        engines[eid] = CatRt(prov, [_entry("gpt-x", prov, False)])
    set_runtime(default)
    r = _delegate(client, model, variant)
    assert r.status_code == 400 and fragment in r.json()["detail"], r.text
    assert _count(db_session) == (0, 0)
    assert default.tasks == [] and all(not rt.tasks for rt in engines.values())


def test_delegate_explicit_opencode_is_free_only_and_default_engine_follows_runtime_mode(
        client, db_session, engines, monkeypatch):
    monkeypatch.setattr(settings, "RUNTIME_MODE", "real")
    entries = [_entry("free-m", "opencode", True), _entry("paid-m", "opencode", False)]
    engines["opencode"] = CatRt("opencode", entries)
    _profile(db_session, "opencode")
    assert _delegate(client, "paid-m").status_code == 400
    assert "not verified as free" in _delegate(client, "paid-m").json()["detail"]
    assert _count(db_session) == (0, 0)
    assert _delegate(client, "free-m").status_code == 201
    # No profile engine + real mode: the default runtime's free-only catalog still applies.
    _profile(db_session, None)
    set_runtime(CatRt("opencode", entries))
    assert _delegate(client, "paid-m").status_code == 400
    assert _delegate(client, "free-m").status_code == 201


def test_delegate_no_engine_in_claude_mode_uses_account_catalog(client, db_session, monkeypatch):
    monkeypatch.setattr(settings, "RUNTIME_MODE", "claude")
    _profile(db_session, None)
    rt = CatRt("anthropic", [_entry("claude-paid", "anthropic", False)])
    set_runtime(rt)
    assert _delegate(client, "claude-paid").status_code == 201
    assert rt.listed[0] is False
    assert _delegate(client, "unknown").status_code == 400


@pytest.mark.parametrize("mode,accepted", [("real", False), ("claude", True), ("codex", True), ("agy", True)])
def test_post_missions_catalog_policy_follows_configured_runtime_mode(client, db_session, monkeypatch, mode, accepted):
    monkeypatch.setattr(settings, "RUNTIME_MODE", mode)
    rt = CatRt("p", [_entry("free-m", "p", True), _entry("account-m", "p", False)])
    set_runtime(rt)
    before = db_session.query(Mission).count()
    r = client.post("/api/missions", json={"objective": "Build a thing", "model_id": "account-m"})
    if accepted:
        assert r.status_code == 201, r.text
        assert rt.listed[0] is False
    else:
        assert r.status_code == 400 and "not verified as free" in r.json()["detail"]
        assert db_session.query(Mission).count() == before  # nothing persisted
    assert client.post("/api/missions", json={"objective": "Build a thing", "model_id": "free-m"}).status_code == 201
    r = client.post("/api/missions", json={"objective": "Build it", "model_id": "ghost"})
    assert r.status_code == 400
