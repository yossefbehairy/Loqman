"""P1 routing: one worker-side default engine/model drives Loqman, the agents it creates, missions, delegation
and planning; "free only" is the OpenCode policy only. Fake runtimes only: no CLI or model is ever run."""

import asyncio
import json
import os

import pytest

from backend.pyworker.appsettings import settings
from backend.pyworker.core import preferences as prefs
from backend.pyworker.orchestration.ceo import _default_runtime_identity
from backend.pyworker.runtime.base import ModelCatalogEntry, ModelCost
from backend.pyworker.runtime.mock_runtime import MockRuntime

from .helpers import Api
from .test_missions import wait_mission

CLAUDE_MODELS = ("claude-sonnet-x", "claude-opus-x")
CODEX_MODELS = ("gpt-x", "gpt-y")


def entries(ids, provider, free, variants=()):
    return [ModelCatalogEntry(id=i, name=i, provider_id=provider, cost=ModelCost(), variants=list(variants),
                              is_free=free, status="active" if free else "unverified") for i in ids]


class FakeEngine(MockRuntime):
    """Scripted runtime for one engine id: its own catalog and policy, records every task."""

    def __init__(self, engine):
        super().__init__()
        self.engine, self.tasks, self.catalog_calls = engine, [], []
        self.scripted = []

    async def list_available_models(self, free_only=True):
        self.catalog_calls.append(free_only)
        if self.engine == "opencode":
            return entries(("oc-free-1", "oc-free-2"), "opencode", True, ("low", "high")) if free_only else []
        if free_only:
            return []  # account engines have no verified-free models
        ids = CLAUDE_MODELS if self.engine == "claude-code" else CODEX_MODELS
        return entries(ids, "anthropic" if self.engine == "claude-code" else "openai", False)

    async def execute_task(self, task):
        self.tasks.append(task)
        if self.scripted:
            from backend.pyworker.runtime.base import AgentResult
            return AgentResult(text=self.scripted.pop(0), model_id=task.model_id)
        return await super().execute_task(task)


class Fakes:
    def __init__(self):
        self.by_engine = {}

    def __call__(self, engine):
        if engine not in self.by_engine:
            self.by_engine[engine] = FakeEngine(engine)
        return self.by_engine[engine]


@pytest.fixture
def real(make_app):
    """A real-mode worker whose engines are fakes. Returns (api, fakes)."""
    fakes = Fakes()
    app = make_app(mode="real", runtime_factory=fakes)
    return Api(app), fakes


@pytest.fixture(autouse=True)
def _unbind_preferences():
    yield
    prefs.bind(None)


# -- 1. the preference file -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_preference_is_saved_next_to_the_database_and_survives_restart(real, make_app):
    api, fakes = real
    empty = await api("preferences.get")
    assert empty["engine"] is None and empty["model_id"] is None
    assert empty["effective_engine"] == "opencode" and empty["effective_model_id"] == settings.DEFAULT_MODEL_ID
    out = await api("preferences.set_default", engine="claude-code", model_id="claude-opus-x")
    assert out["engine"] == "claude-code" and out["effective_engine"] == "claude-code"
    path = os.path.join(os.path.dirname(api.app.ctx.db.path), "preferences.json")
    assert json.load(open(path, encoding="utf-8")) == {"engine": "claude-code", "model_id": "claude-opus-x"}
    assert [f for f in os.listdir(os.path.dirname(path)) if f.endswith(".tmp")] == []  # atomic write leaves no temp
    assert not any(fake.catalog_calls for fake in fakes.by_engine.values())  # saving never needs a CLI call
    # a fresh Application on the same data directory restores it
    again = prefs.Preferences(path)
    assert again.get_default() == ("claude-code", "claude-opus-x")


@pytest.mark.asyncio
async def test_missing_or_corrupt_file_means_nothing_chosen(tmp_path):
    path = str(tmp_path / "preferences.json")
    p = prefs.Preferences(path)
    assert p.get_default() == (None, None)
    for junk in ("{not json", "[]", '{"engine": 3, "model_id": "x"}', '{"engine": "mock", "model_id": "x"}',
                 '{"engine": "codex", "model_id": "bad id!"}'):
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(junk)
        assert p.get_default() == (None, None), junk
    p.set_default("codex", "gpt-x")  # a corrupt file is simply replaced
    assert p.get_default() == ("codex", "gpt-x")


@pytest.mark.asyncio
@pytest.mark.parametrize("engine,model", [("nope", "m"), ("mock", "m"), ("codex", "has space"), ("codex", "-lead"),
                                          ("codex", "x" * 129)])
async def test_set_default_validates_engine_and_model_format(real, engine, model):
    api, _ = real
    err = await api.fail("preferences.set_default", engine=engine, model_id=model)
    assert err.status in (400, 422)
    assert (await api("preferences.get"))["engine"] is None


# -- 2. the default runtime follows it --------------------------------------------------------------------

@pytest.mark.asyncio
async def test_real_mode_default_runtime_follows_the_preference(real):
    api, fakes = real
    ctx = api.app.ctx
    assert ctx.get_runtime() is fakes("opencode") and _default_runtime_identity() == ("opencode", "opencode")
    await api("preferences.set_default", engine="claude-code", model_id="claude-opus-x")
    assert ctx.get_runtime() is fakes("claude-code") and ctx.get_runtime() is ctx.get_runtime()  # cached per engine
    assert _default_runtime_identity() == ("claude-code", "anthropic")
    await api("preferences.set_default", engine="codex", model_id="gpt-x")
    assert _default_runtime_identity() == ("codex", "openai")
    assert prefs.default_model_id() == "gpt-x"


@pytest.mark.asyncio
async def test_mock_mode_stays_mock_whatever_is_saved(make_app):
    rt = MockRuntime()
    app = make_app(mode="mock", runtime=rt)
    api = Api(app)
    await api("preferences.set_default", engine="claude-code", model_id="claude-opus-x")
    assert app.ctx.get_runtime() is rt
    assert _default_runtime_identity() == ("mock", "mock")
    assert prefs.default_engine_id() == "mock" and prefs.default_model_id() == settings.DEFAULT_MODEL_ID
    # without an injected runtime the package default is the mock too
    app2 = make_app(mode="mock", runtime_factory=lambda e: (_ for _ in ()).throw(AssertionError("real engine reached")))
    await Api(app2)("preferences.set_default", engine="codex", model_id="gpt-x")
    assert isinstance(app2.ctx.get_runtime(), MockRuntime)


# -- 3/4. ceo.ask ----------------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ceo_ask_runs_on_the_saved_engine_with_its_policy(real):
    api, fakes = real
    await api("preferences.set_default", engine="claude-code", model_id="claude-opus-x")
    out = await api("ceo.ask", question="hello")  # small talk: one plain call
    assert out["fast"] is True
    claude = fakes("claude-code")
    task = claude.tasks[0]
    assert task.model_id == "claude-opus-x" and task.provider_id == "anthropic"
    assert task.agent_id is None  # OpenCode's locked agent id never reaches an account engine
    assert task.variant is None
    assert task.session_title.startswith("ceo-fast-gm-") and len(task.session_title) > len("ceo-fast-gm-")
    assert claude.catalog_calls == [False]  # claude is not a free-only engine
    assert "opencode" not in fakes.by_engine
    row = api.db.one("SELECT provider_id, agent_id FROM usage_events ORDER BY created_at DESC LIMIT 1")
    assert row["provider_id"] == "anthropic" and row["agent_id"] == "unassigned"


@pytest.mark.asyncio
async def test_each_ask_on_an_account_engine_gets_its_own_session_title(real):
    api, fakes = real
    await api("preferences.set_default", engine="codex", model_id="gpt-x")
    await api("ceo.ask", question="hello")
    await api("ceo.ask", question="hello")
    titles = [t.session_title for t in fakes("codex").tasks]
    assert len(set(titles)) == 2  # a repeated title is refused while one runs


@pytest.mark.asyncio
async def test_ceo_ask_deep_loop_on_a_non_opencode_default(real):
    api, fakes = real
    await api("preferences.set_default", engine="codex", model_id="gpt-x")
    fakes("codex").scripted = ['{"final": "from codex"}']
    out = await api("ceo.ask", question="show me the missions status")
    assert out["reply"] == "from codex" and out["fast"] is False
    assert fakes("codex").tasks[0].agent_id is None and fakes("codex").tasks[0].provider_id == "openai"


@pytest.mark.asyncio
async def test_opencode_keeps_its_free_only_policy_and_the_error_names_it(real):
    api, fakes = real
    err = await api.fail("ceo.ask", question="hello", model_id="not-free")
    assert err.status == 400 and "OpenCode" in err.message and "not verified as free" in err.message
    assert fakes("opencode").catalog_calls == [True]
    out = await api("ceo.ask", question="hello", model_id="oc-free-1")
    task = fakes("opencode").tasks[0]
    assert out["fast"] and task.agent_id == "agentos-chat" and task.session_title == "ceo-fast-gm"
    assert task.provider_id == "opencode" and task.variant == "low"


@pytest.mark.asyncio
async def test_ceo_ask_unset_default_is_opencode_with_the_setting_model(real, monkeypatch):
    api, fakes = real
    monkeypatch.setattr(settings, "DEFAULT_MODEL_ID", "oc-free-2")
    await api("ceo.ask", question="hello")
    assert fakes("opencode").tasks[0].model_id == "oc-free-2"


@pytest.mark.asyncio
async def test_explicit_engine_overrides_the_default(real):
    api, fakes = real
    await api("preferences.set_default", engine="claude-code", model_id="claude-opus-x")
    out = await api("ceo.ask", question="hello", engine="codex", model_id="gpt-y")
    assert out["fast"]
    assert fakes("codex").tasks[0].model_id == "gpt-y" and fakes("codex").tasks[0].provider_id == "openai"
    assert "claude-code" not in fakes.by_engine or not fakes("claude-code").tasks
    # validated against THAT engine's catalog: a claude model is not a codex model, and the error names codex
    err = await api.fail("ceo.ask", question="hello", engine="codex", model_id="claude-opus-x")
    assert err.status == 400 and "ChatGPT (Codex)" in err.message
    assert (await api.fail("ceo.ask", question="hello", engine="codex")).status == 400  # other engine needs a model
    assert (await api.fail("ceo.ask", question="hello", engine="bogus", model_id="m")).status == 400
    err = await api.fail("ceo.ask", question="hello", engine="mock", model_id="m")
    assert err.status == 400 and "mock mode" in err.message
    err = await api.fail("ceo.ask", question="hello", engine="codex", model_id="gpt-x", variant="max")
    assert err.status == 400 and "Variant" in err.message  # account engines expose no variants here


@pytest.mark.asyncio
async def test_mock_mode_ceo_ask_unchanged_and_accepts_the_mock_engine(make_app):
    app = make_app(mode="mock", runtime=MockRuntime())
    api = Api(app)
    out = await api("ceo.ask", question="hello", engine="mock", model_id="deepseek-v4.1-flash-free")
    assert out["fast"] is True
    assert (await api.fail("ceo.ask", question="hello", model_id="nope")).status == 400


# -- 5. agents and teams Loqman creates ------------------------------------------------------------------

def _new_profile_call(**extra):
    args = {"name": "Nova", "role": "researcher", "instructions": "i", **extra}
    return json.dumps({"tool_call": {"name": "propose_new_profile", "args": args}})


@pytest.mark.asyncio
async def test_agents_created_by_loqman_are_stamped_with_the_engine_and_model_in_effect(real):
    api, fakes = real
    await api("preferences.set_default", engine="claude-code", model_id="claude-opus-x")
    fakes("claude-code").scripted = [_new_profile_call(default_model_id="oc-free-1", default_variant_id="low"),
                                     '{"final": "done"}']
    out = await api("ceo.ask", question="make an agent for status")
    created = out["agents_created"]
    assert len(created) == 1
    prof = await api("profiles.get", profile_id=created[0]["id"])
    # the proposal's model is not offered by claude: replaced; its OpenCode variant dropped
    assert prof["engine"] == "claude-code" and prof["default_model_id"] == "claude-opus-x"
    assert prof["default_variant_id"] is None


@pytest.mark.asyncio
async def test_a_valid_model_named_by_the_proposal_is_kept(real):
    api, fakes = real
    await api("preferences.set_default", engine="claude-code", model_id="claude-opus-x")
    fakes("claude-code").scripted = [_new_profile_call(default_model_id="claude-sonnet-x"), '{"final": "done"}']
    out = await api("ceo.ask", question="make an agent for status")
    prof = await api("profiles.get", profile_id=out["agents_created"][0]["id"])
    assert prof["engine"] == "claude-code" and prof["default_model_id"] == "claude-sonnet-x"


@pytest.mark.asyncio
async def test_agents_follow_the_engine_of_the_ask_not_the_default(real):
    api, fakes = real
    await api("preferences.set_default", engine="claude-code", model_id="claude-opus-x")
    fakes("codex").scripted = [_new_profile_call(), '{"final": "done"}']
    out = await api("ceo.ask", question="make an agent for status", engine="codex", model_id="gpt-y")
    prof = await api("profiles.get", profile_id=out["agents_created"][0]["id"])
    assert prof["engine"] == "codex" and prof["default_model_id"] == "gpt-y"


@pytest.mark.asyncio
async def test_teams_created_by_loqman_are_stamped_too(real):
    api, fakes = real
    team = {"tool_call": {"name": "propose_new_team", "args": {"name": "Crew", "members": [
        {"name": "A", "role_type": "researcher", "instructions": "i"},
        {"name": "B", "role_type": "coder", "instructions": "i", "default_model_id": "gpt-x"}]}}}
    await api("preferences.set_default", engine="codex", model_id="gpt-y")
    fakes("codex").scripted = [json.dumps(team), '{"final": "done"}']
    out = await api("ceo.ask", question="build a team for status")
    profs = [await api("profiles.get", profile_id=p["id"]) for p in out["agents_created"]]
    assert {p["engine"] for p in profs} == {"codex"}
    assert sorted(p["default_model_id"] for p in profs) == ["gpt-x", "gpt-y"]


@pytest.mark.asyncio
async def test_mock_mode_agents_keep_their_proposed_model_and_no_engine(make_app):
    api = Api(make_app(mode="mock", runtime=MockRuntime()))
    out = await api("ceo.ask", question="make an agent for status")
    for made in out["agents_created"]:
        assert (await api("profiles.get", profile_id=made["id"]))["engine"] is None


# -- 6. missions, delegation, planning -------------------------------------------------------------------

@pytest.mark.asyncio
async def test_mission_runs_planning_tasks_and_synthesis_on_the_default_engine(real):
    api, fakes = real
    await api("preferences.set_default", engine="claude-code", model_id="claude-opus-x")
    m = await api("missions.create", objective="Plan and review the launch")
    done = await wait_mission(api, m["id"])
    assert done["status"] == "succeeded", done
    claude = fakes("claude-code")
    assert "opencode" not in fakes.by_engine
    # planner, two workers, synthesis: all on claude with a claude model, never a free OpenCode id
    assert len(claude.tasks) == 4 and {t.model_id for t in claude.tasks} == {"claude-opus-x"}
    assert {t.provider_id for t in claude.tasks} == {"anthropic"}
    runs = (await api("missions.runs", mission_id=m["id"]))["runs"]
    assert {r["engine"] for r in runs} == {"claude-code"}
    providers = {r["provider_id"] for r in api.db.all("SELECT provider_id FROM usage_events")}
    assert providers == {"anthropic"}


@pytest.mark.asyncio
async def test_mission_create_names_the_engine_when_the_model_is_not_offered(real):
    api, _ = real
    await api("preferences.set_default", engine="codex", model_id="gpt-x")
    err = await api.fail("missions.create", objective="x", model_id="oc-free-1")
    assert err.status == 400 and "ChatGPT (Codex)" in err.message and "OpenCode" not in err.message
    assert (await api("missions.list"))["missions"] == []


@pytest.mark.asyncio
async def test_a_profile_without_engine_uses_its_model_only_if_the_default_engine_offers_it(real):
    api, fakes = real
    await api("preferences.set_default", engine="codex", model_id="gpt-x")
    api.db.update("agent_profiles", {"default_model_id": "oc-free-1", "default_variant_id": "low"}, "id=?", ("planner",))
    api.db.update("agent_profiles", {"default_model_id": "gpt-y"}, "id=?", ("reviewer",))
    m = await api("missions.create", objective="Plan and review the launch")
    done = await wait_mission(api, m["id"])
    assert done["status"] == "succeeded", [(t["task_key"], t["error_message"]) for t in done["tasks"]]
    by_role = {}
    for t in fakes("codex").tasks:
        if "'planner' employee" in t.prompt:
            by_role["planner"] = t
        if "'reviewer' employee" in t.prompt:
            by_role["reviewer"] = t
    assert by_role["planner"].model_id == "gpt-x" and by_role["planner"].variant is None  # invalid -> default model
    assert by_role["reviewer"].model_id == "gpt-y"  # valid on codex -> kept


@pytest.mark.asyncio
async def test_a_profile_with_an_explicit_engine_is_unchanged(real):
    api, fakes = real
    await api("preferences.set_default", engine="claude-code", model_id="claude-opus-x")
    await api("profiles.update", profile_id="coder", engine="codex", default_model_id="gpt-y")
    m = await api("missions.delegate", conversation_id="c", employee_role="coder", title="t", instructions="do")
    done = await wait_mission(api, m["id"])
    assert done["status"] == "succeeded", done
    assert [t.model_id for t in fakes("codex").tasks] == ["gpt-y"]
    assert not fakes("claude-code").tasks


@pytest.mark.asyncio
async def test_delegation_resolves_the_model_in_the_worker(real):
    api, fakes = real
    await api("preferences.set_default", engine="claude-code", model_id="claude-opus-x")
    # no engine, stored model is an OpenCode id: the default engine's default model is used
    api.db.update("agent_profiles", {"default_model_id": "oc-free-1"}, "id=?", ("coder",))
    m = await api("missions.delegate", conversation_id="c", employee_role="coder", title="t", instructions="do")
    await wait_mission(api, m["id"])
    # stored model valid for the default engine: kept
    api.db.update("agent_profiles", {"default_model_id": "claude-sonnet-x"}, "id=?", ("coder",))
    m2 = await api("missions.delegate", conversation_id="c", employee_role="coder", title="t2", instructions="do")
    await wait_mission(api, m2["id"])
    assert [t.model_id for t in fakes("claude-code").tasks] == ["claude-opus-x", "claude-sonnet-x"]
    assert all(t.provider_id == "anthropic" for t in fakes("claude-code").tasks)
    # an explicit model is still validated on the default engine, naming it
    err = await api.fail("missions.delegate", conversation_id="c", employee_role="coder", title="t",
                         instructions="do", model_id="oc-free-1")
    assert err.status == 400 and "Claude" in err.message


@pytest.mark.asyncio
async def test_default_model_helper_falls_back_to_the_setting(real, monkeypatch):
    api, _ = real
    monkeypatch.setattr(settings, "DEFAULT_MODEL_ID", "setting-model")
    assert prefs.default_model_id() == "setting-model"
    await api("preferences.set_default", engine="codex", model_id="gpt-x")
    assert prefs.default_model_id() == "gpt-x"
    monkeypatch.setattr(settings, "RUNTIME_MODE", "mock")
    assert prefs.default_model_id() == "setting-model"
