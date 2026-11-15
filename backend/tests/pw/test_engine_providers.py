"""Provider catalog, engines.status/setup/test/open_setup and usage.by_engine. No real CLI or model is ever run."""

import asyncio

import pytest

from backend.pyworker.api import system
from backend.pyworker.runtime import engine_discovery as ed
from backend.pyworker.runtime.base import AgentResult
from backend.pyworker.runtime.engine_registry import ENGINE_IDS
from backend.pyworker.runtime.errors import RuntimeAuthError, RuntimeErrorBase
from backend.pyworker.runtime.mock_runtime import MockRuntime
from backend.pyworker.runtime.providers import PROVIDERS, get_provider

from .helpers import Api


def st(engine_id, install="installed", auth="unknown"):
    return ed.EngineStatus(engine_id=engine_id, display_name=engine_id, install_state=install, auth_state=auth,
                           version=None, executable=None, adapter_supported=True, detail="d")


def fake_discovery(monkeypatch, install="installed", auth="unknown"):
    calls = []

    def discover():
        calls.append(1)
        return [st(p.engine_id, install, auth) for p in PROVIDERS]

    monkeypatch.setattr(ed, "discover_engines", discover)
    return calls


# -- catalog ----------------------------------------------------------------------------

def test_catalog_is_the_source_of_truth():
    assert ENGINE_IDS == ("opencode", "claude-code", "codex", "antigravity")
    assert [s.engine_id for s in ed.ENGINE_SPECS if s.engine_id not in ENGINE_IDS] == []
    c = {p.engine_id: p for p in PROVIDERS}
    assert c["claude-code"].install_command == ("npm", "install", "-g", "@anthropic-ai/claude-code")
    assert c["codex"].login_command == ("codex", "login")
    assert c["opencode"].login_command == ("opencode", "auth", "login")
    assert c["antigravity"].install_command is None
    assert c["antigravity"].install_url == "https://antigravity.google/download"
    assert c["antigravity"].login_command == ("agy", "acp", "--login")
    assert get_provider("nope") is None


@pytest.mark.asyncio
async def test_capabilities_unchanged_including_mock(api):
    caps = await api("engines.capabilities")
    assert list(caps) == ["opencode", "claude-code", "codex", "antigravity", "mock"]
    base = {"conversation_runs": True, "streaming_text": False, "tool_approvals": False,
            "cancellation": "best_effort_remote_unconfirmed", "workspace_restriction": False}
    assert caps["opencode"] == {**base, "model_policy": "verified_free_only"}
    assert caps["codex"] == {**base, "model_policy": "subscription_or_account"}
    assert caps["claude-code"] == {
        **base, "model_policy": "subscription_or_account", "tool_approvals": True,
        "workspace_restriction": "scoped_mcp_tools", "workspace_tools": ["workspace_read", "workspace_write"],
        "browser_tools": True, "computer_tools": "selected_windows_window",
        "write_policy": "new_workspace_root_reports_only"}
    assert caps["antigravity"] == {**base, "model_policy": "subscription_or_account",
                                   "permission_policy": "plan_mode_and_deny_all_requests"}
    assert caps["mock"]["model_policy"] == "simulated" and caps["mock"]["cancellation"] == "local"


# -- engines.status ---------------------------------------------------------------------

@pytest.mark.parametrize("install,auth,state", [
    ("not_installed", "unknown", "not_installed"), ("unknown", "unknown", "error"),
    ("installed", "authenticated", "ready"), ("installed", "unauthenticated", "needs_login"),
    ("installed", "unknown", "unverified")])
def test_state_mapping(install, auth, state):
    assert ed.engine_state(install, auth) == state


@pytest.mark.asyncio
async def test_status_shape_cache_and_force(api, monkeypatch):
    calls = fake_discovery(monkeypatch, "installed", "authenticated")
    body = await api("engines.status")
    codex = next(e for e in body["engines"] if e["engine_id"] == "codex")
    assert codex["state"] == "ready" and codex["title"] == "ChatGPT (Codex)" and codex["vendor"] == "OpenAI"
    assert codex["checked_at"].endswith("+00:00") and codex["install_state"] == "installed"
    assert {"display_name", "auth_state", "version", "executable", "adapter_supported", "detail"} <= set(codex)
    await api("engines.status")
    assert len(calls) == 1  # served from the cache
    await api("engines.status", force=False)
    assert len(calls) == 1
    again = await api("engines.status", force=True)
    assert len(calls) == 2 and again["engines"][0]["checked_at"] >= codex["checked_at"]
    api.app.ctx.engine_cache["at"] -= system.ENGINE_STATUS_TTL_SECONDS + 1  # expired
    await api("engines.status")
    assert len(calls) == 3
    assert (await api.fail("engines.status", force="yes")).status == 422


@pytest.mark.asyncio
async def test_cache_is_per_app_instance(make_app, monkeypatch):
    calls = fake_discovery(monkeypatch)
    a, b = Api(make_app()), Api(make_app())
    await a("engines.status")
    await b("engines.status")
    assert len(calls) == 2


def test_detail_is_user_facing(monkeypatch):
    from .test_engine_discovery import cp, install
    install(monkeypatch, {"codex": "/c"},
            {("codex", "--version"): cp(0, "0.1.0"), ("codex", "login"): cp(0, "Logged in")})
    s = {x.engine_id: x for x in ed.discover_engines()}
    assert s["codex"].detail == "Installed and signed in." and "Discovery" not in s["codex"].detail
    assert s["opencode"].detail == "Not installed on this computer."


# -- engines.setup ----------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("install,auth,state,needle", [
    ("not_installed", "unknown", "not_installed", "Install the"),
    ("installed", "unauthenticated", "needs_login", "sign-in command"),
    ("installed", "authenticated", "ready", "No setup is needed"),
    ("installed", "unknown", "unverified", "could not be verified"),
    ("unknown", "unknown", "error", "could not be checked")])
async def test_setup_follows_state(api, monkeypatch, install, auth, state, needle):
    fake_discovery(monkeypatch, install, auth)
    out = await api("engines.setup", engine_id="claude-code")
    assert out["state"] == state and needle in out["instructions"]
    assert out["command"] == ["claude", "auth", "login"] and out["login"] == {"command": ["claude", "auth", "login"]}
    assert out["install"] == {"command": ["npm", "install", "-g", "@anthropic-ai/claude-code"], "url": None}
    assert out["login_method"] == "external_cli" and out["credentials_handled_by"] == "official_cli"
    assert out["docs_url"] and out["engine_id"] == "claude-code"


@pytest.mark.asyncio
async def test_setup_antigravity_and_cache_reuse(api, monkeypatch):
    calls = fake_discovery(monkeypatch, "not_installed", "unknown")
    out = await api("engines.setup", engine_id="antigravity")
    assert out["install"] == {"command": None, "url": "https://antigravity.google/download"}
    assert out["login"]["command"] == ["agy", "acp", "--login"] and out["docs_url"] is None
    await api("engines.setup", engine_id="codex")
    await api("engines.status")
    assert len(calls) == 1


# -- clean model names ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_model_names_are_clean(make_app, monkeypatch):
    from backend.pyworker.runtime.claude_runtime import ClaudeCodeRuntime
    from backend.pyworker.runtime.codex_runtime import CodexCliRuntime

    async def cat(self):
        return [("gpt-5", "GPT-5", ["low", "high"])]

    monkeypatch.setattr(CodexCliRuntime, "_catalog", cat)
    api = Api(make_app(runtime_factory=lambda e: ClaudeCodeRuntime() if e == "claude-code" else CodexCliRuntime(),
                       mode="real"))
    claude = (await api("engines.models", engine_id="claude-code"))["models"]
    assert [m["name"] for m in claude] == ["Claude Sonnet", "Claude Opus", "Claude Haiku"]
    assert all(m["status"] == "unverified" for m in claude)
    codex = (await api("engines.models", engine_id="codex"))["models"]
    assert [m["name"] for m in codex] == ["GPT-5"] and codex[0]["status"] == "unverified"


# -- engines.test -----------------------------------------------------------------------

class Fixed(MockRuntime):
    def __init__(self, text="OK", exc=None, delay=0):
        super().__init__()
        self.text, self.exc, self.delay, self.tasks = text, exc, delay, []

    async def list_available_models(self, free_only=True):
        return await super().list_available_models(free_only=False)

    async def execute_task(self, task):
        self.tasks.append(task)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.exc:
            raise self.exc
        return AgentResult(text=self.text, model_id=task.model_id)


@pytest.mark.asyncio
async def test_engine_test_success_uses_fixed_prompt_and_fallback_model(make_app):
    rt = Fixed("  OK\n" + "x" * 200)
    out = await Api(make_app(runtime_factory=lambda e: rt, mode="real"))("engines.test", engine_id="codex")
    first = (await rt.list_available_models(free_only=False))[-1]
    assert out["ok"] is True and out["model_id"] == first.id and out["engine_id"] == "codex"
    assert "\n" not in out["reply_preview"] and len(out["reply_preview"]) == 80
    assert out["error_code"] is None and out["error_message"] is None and out["duration_ms"] >= 0
    assert rt.tasks[0].prompt == system.ENGINE_TEST_PROMPT == "Reply with exactly: OK"
    assert rt.tasks[0].timeout == 60 and rt.tasks[0].model_id == first.id


@pytest.mark.asyncio
async def test_engine_test_explicit_model_and_extra_prompt_rejected(make_app):
    rt = Fixed()
    api = Api(make_app(runtime_factory=lambda e: rt, mode="real"))
    out = await api("engines.test", engine_id="codex", model_id="custom-model")
    assert out["ok"] and out["model_id"] == "custom-model" and rt.tasks[0].model_id == "custom-model"
    err = await api.fail("engines.test", engine_id="codex", prompt="say anything")
    assert err.status == 422 and len(rt.tasks) == 1  # unknown parameter, nothing executed


@pytest.mark.asyncio
async def test_engine_test_runtime_errors_become_results(make_app):
    api = Api(make_app(runtime_factory=lambda e: Fixed(exc=RuntimeAuthError("secret sk-123 token")), mode="real"))
    out = await api("engines.test", engine_id="claude-code")
    assert out["ok"] is False and out["error_code"] == "auth" and "sk-123" not in str(out)
    assert out["error_message"] == "Sign-in is required or has expired." and out["reply_preview"] == ""
    out = await Api(make_app(runtime_factory=lambda e: Fixed(exc=RuntimeErrorBase("x")), mode="real"))(
        "engines.test", engine_id="codex")
    assert out["ok"] is False and out["error_code"] == "runtime_error"
    out = await Api(make_app(runtime_factory=lambda e: Fixed(exc=ValueError("boom")), mode="real"))(
        "engines.test", engine_id="codex")
    assert out["ok"] is False and out["error_code"] == "test_failed"
    out = await Api(make_app(runtime_factory=lambda e: Fixed(text="  "), mode="real"))(
        "engines.test", engine_id="codex")
    assert out["ok"] is False and out["error_code"] == "empty_reply"


@pytest.mark.asyncio
async def test_engine_test_timeout(make_app, monkeypatch):
    monkeypatch.setattr(system, "ENGINE_TEST_TIMEOUT_SECONDS", 0.05)
    out = await Api(make_app(runtime_factory=lambda e: Fixed(delay=5), mode="real"))(
        "engines.test", engine_id="codex")
    assert out["ok"] is False and out["error_code"] == "timeout" and out["error_message"]


@pytest.mark.asyncio
async def test_engine_test_unknown_engine_and_no_models(make_app):
    api = Api(make_app(runtime_factory=lambda e: Fixed(), mode="real"))
    assert (await api.fail("engines.test", engine_id="gemini")).status == 404

    class Empty(Fixed):
        async def list_available_models(self, free_only=True):
            return []

    out = await Api(make_app(runtime_factory=lambda e: Empty(), mode="real"))("engines.test", engine_id="codex")
    assert out["ok"] is False and out["error_code"] == "no_models"


# -- engines.open_setup -----------------------------------------------------------------

@pytest.fixture
def spawned(monkeypatch):
    calls = []
    monkeypatch.setattr(system, "_spawn_terminal", lambda argv: calls.append(list(argv)))
    monkeypatch.setattr(system, "_is_windows", lambda: True)
    return calls


@pytest.mark.asyncio
async def test_open_setup_launches_catalog_argv_only(make_app, spawned):
    api = Api(make_app(mode="real"))
    assert await api("engines.open_setup", engine_id="codex", step="login") == {"launched": True, "reason": None}
    assert spawned[-1] == ["codex", "login"]
    await api("engines.open_setup", engine_id="claude-code", step="install")
    assert spawned[-1] == ["npm", "install", "-g", "@anthropic-ai/claude-code"]
    assert (await api.fail("engines.open_setup", engine_id="codex", step="rm -rf")).status == 422
    assert (await api.fail("engines.open_setup", engine_id="codex", step="login", command="calc")).status == 422
    assert (await api.fail("engines.open_setup", engine_id="gemini", step="login")).status == 404
    assert len(spawned) == 2


@pytest.mark.asyncio
async def test_open_setup_guards_spawn_nothing(make_app, spawned, monkeypatch):
    api = Api(make_app(mode="real"))
    out = await api("engines.open_setup", engine_id="antigravity", step="install")
    assert out["launched"] is False and out["reason"]
    monkeypatch.setattr(system, "_is_windows", lambda: False)
    out = await api("engines.open_setup", engine_id="codex", step="login")
    assert out["launched"] is False and "Windows" in out["reason"]
    mock_api = Api(make_app(mode="mock"))
    monkeypatch.setattr(system, "_is_windows", lambda: True)
    out = await mock_api("engines.open_setup", engine_id="codex", step="login")
    assert out["launched"] is False and "mock" in out["reason"].lower()
    assert spawned == []


@pytest.mark.asyncio
async def test_open_setup_spawn_failure_is_reported(make_app, monkeypatch):
    def boom(argv):
        raise OSError("no")

    monkeypatch.setattr(system, "_spawn_terminal", boom)
    monkeypatch.setattr(system, "_is_windows", lambda: True)
    out = await Api(make_app(mode="real"))("engines.open_setup", engine_id="codex", step="login")
    assert out["launched"] is False and out["reason"]


# -- usage.by_engine --------------------------------------------------------------------

@pytest.mark.asyncio
async def test_usage_by_engine_groups_via_catalog(api):
    base = {"agent_id": "a", "kind": "chat_consult", "model_id": "m", "created_at": "2026-01-01 00:00:00.000000"}
    rows = [("1", "anthropic", 1, 10), ("2", "openai", 1, 20), ("3", "openai-codex", 0, 30),
            ("4", "mystery", 1, 5), ("5", "nvidia", 1, 5), ("6", "opencode", 1, 7), ("7", "", 1, 1)]
    for eid, prov, ok, dur in rows:
        api.db.insert("usage_events", {**base, "id": eid, "provider_id": prov, "success": ok,
                                       "duration_ms": dur, "tokens_in": 1, "tokens_out": 1, "tokens_cache_read": 0})
    s = await api("usage.summary")
    by = {g["key"]: g for g in s["by_engine"]}
    assert by["codex"]["events"] == 2 and by["codex"]["failed"] == 1 and by["codex"]["total_duration_ms"] == 50
    assert by["claude-code"]["events"] == 1 and by["opencode"]["events"] == 1
    assert by["other"]["events"] == 3 and set(by["codex"]) == set(s["by_model"][0])
    assert "antigravity" not in by and s["by_model"] and s["by_kind"]


# -- verified_free_only engines (OpenCode): models and the automatic test model follow the policy --------

def _entry(mid, free):
    from backend.pyworker.runtime.base import ModelCatalogEntry, ModelCost
    return ModelCatalogEntry(id=mid, name=mid.upper(), provider_id="opencode", cost=ModelCost(0.0 if free else 1.0, 0.0),
                             is_free=free, status="active")


class Catalog(Fixed):
    """Paid model listed first, as the unrestricted OpenCode catalog does."""

    def __init__(self):
        super().__init__()
        self.asked = []

    async def list_available_models(self, free_only=True):
        self.asked.append(free_only)
        entries = [_entry("paid-1", False), _entry("free-1", True), _entry("free-2", True)]
        return [m for m in entries if m.is_free] if free_only else entries


@pytest.mark.asyncio
async def test_verified_free_only_engine_lists_only_free_models(make_app):
    rt = Catalog()
    api = Api(make_app(runtime_factory=lambda e: rt, mode="real"))
    assert [m["id"] for m in (await api("engines.models", engine_id="opencode"))["models"]] == ["free-1", "free-2"]
    # Engines with another policy keep the full catalog.
    assert [m["id"] for m in (await api("engines.models", engine_id="codex"))["models"]] == ["paid-1", "free-1", "free-2"]


@pytest.mark.asyncio
async def test_verified_free_only_engine_test_never_picks_or_accepts_a_paid_model(make_app):
    rt = Catalog()
    api = Api(make_app(runtime_factory=lambda e: rt, mode="real"))
    out = await api("engines.test", engine_id="opencode")
    # Without a model the test picks a free one (which free one is a cost preference, not a rule).
    assert out["ok"] and out["model_id"] in {"free-1", "free-2"} and rt.tasks[0].model_id == out["model_id"]
    bad = await api("engines.test", engine_id="opencode", model_id="paid-1")
    assert bad["ok"] is False and bad["error_code"] == "model_unavailable" and len(rt.tasks) == 1
    ok = await api("engines.test", engine_id="opencode", model_id="free-2")
    assert ok["ok"] and rt.tasks[-1].model_id == "free-2"


# -- discovery cache: concurrent callers share one probe, an older run never overwrites a newer one ------

def _timed_discovery(monkeypatch, delays, installs):
    """The n-th probe sleeps ``delays[n]`` seconds, then reports ``installs[n]`` for every engine."""
    import time
    calls = []

    def discover():
        n = len(calls)
        calls.append(n)
        time.sleep(delays[n])
        return [st(p.engine_id, installs[n], "unknown") for p in PROVIDERS]

    monkeypatch.setattr(ed, "discover_engines", discover)
    return calls


@pytest.mark.asyncio
async def test_concurrent_forced_status_calls_share_one_probe(api, monkeypatch):
    calls = _timed_discovery(monkeypatch, [0.2, 0.0, 0.0], ["installed", "not_installed", "not_installed"])
    results = await asyncio.gather(*[api("engines.status", force=True) for _ in range(4)])
    assert len(calls) == 1
    assert len({r["engines"][0]["checked_at"] for r in results}) == 1


@pytest.mark.asyncio
async def test_an_older_slow_probe_cannot_replace_a_newer_forced_result(api, monkeypatch):
    calls = _timed_discovery(monkeypatch, [0.4, 0.0], ["not_installed", "installed"])  # 0 = old and slow, 1 = new
    slow = asyncio.create_task(api("engines.status"))
    await asyncio.sleep(0.1)
    forced = await api("engines.status", force=True)
    await slow
    assert forced["engines"][0]["install_state"] == "installed"
    later = await api("engines.status")  # served from the cache
    assert later["engines"][0]["install_state"] == "installed" and len(calls) == 2


# -- P2: one engine table, one factory, retired runtime modes ---------------------------------------

def test_ledger_provider_table_is_derived_from_the_catalog():
    from backend.pyworker.runtime.providers import ENGINE_LEDGER_PROVIDERS, PROVIDERS, ledger_provider

    assert ENGINE_LEDGER_PROVIDERS == {"opencode": "opencode", "claude-code": "anthropic", "codex": "openai",
                                       "antigravity": "antigravity", "mock": "mock"}
    assert all(ledger_provider(p.engine_id) == p.provider_ids[0] for p in PROVIDERS)
    assert ledger_provider("gemini") is None


def test_one_factory_builds_every_engine_runtime():
    from backend.pyworker.runtime.engine_registry import ENGINE_IDS, create_engine_runtime
    from backend.pyworker.runtime.mock_runtime import MockRuntime

    for engine in ENGINE_IDS:
        assert create_engine_runtime(engine) is not None
    assert isinstance(create_engine_runtime("mock"), MockRuntime)
    with pytest.raises(ValueError):
        create_engine_runtime("gemini")


@pytest.mark.parametrize("mode", ["claude", "codex", "agy"])
def test_retired_runtime_modes_fail_with_a_clear_message(mode):
    from backend.pyworker.appsettings import load_settings

    with pytest.raises(ValueError) as err:
        load_settings(env={"RUNTIME_MODE": mode}, dotenv_path=None)
    assert f"RUNTIME_MODE={mode}" in str(err.value) and "default provider in Settings" in str(err.value)
    assert load_settings(env={"RUNTIME_MODE": "mock"}, dotenv_path=None).RUNTIME_MODE == "mock"


def test_retired_runtime_mode_exits_cleanly_without_a_traceback(tmp_path):
    import os
    import subprocess
    import sys

    env = {**os.environ, "RUNTIME_MODE": "claude"}
    proc = subprocess.run([sys.executable, "-m", "backend.pyworker", "--db", str(tmp_path / "a.db")],
                          input=b"", capture_output=True, env=env, timeout=60)
    err = proc.stderr.decode("utf-8", "replace")
    assert proc.returncode == 2 and "default provider in Settings" in err and "Traceback" not in err


def test_task_provider_id_and_free_only_have_no_silent_defaults():
    import inspect
    from backend.pyworker.runtime.base import AgentRuntime, ExecuteAgentTask

    with pytest.raises(TypeError):
        ExecuteAgentTask(prompt="x", model_id="m")
    assert inspect.signature(AgentRuntime.list_available_models).parameters["free_only"].default is inspect.Parameter.empty
