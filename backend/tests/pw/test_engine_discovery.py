import pytest
"""Engine discovery tests — all subprocess/PATH access is mocked; no real CLI runs."""

import subprocess
from types import SimpleNamespace

import pytest
from backend.pyworker.runtime import engine_discovery as ed

SECRET = "sk-SECRET-TOKEN-123"


def cp(code=0, out="", err=""):
    return SimpleNamespace(returncode=code, stdout=out, stderr=err)


def install(monkeypatch, paths, responses):
    """paths: binary -> path|None. responses: (binary, first_arg) -> result|Exception."""
    monkeypatch.setattr(ed.shutil, "which", lambda n: paths.get(n))
    calls = []

    def fake_run(cmd, **kw):
        calls.append((cmd, kw))
        exe = cmd[0]
        name = next(k for k, v in paths.items() if v == exe)
        r = responses[(name, cmd[1])]
        if isinstance(r, Exception):
            raise r
        return r

    monkeypatch.setattr(ed.subprocess, "run", fake_run)
    return calls


def by_id(statuses):
    return {s.engine_id: s for s in statuses}


def test_all_missing(monkeypatch):
    install(monkeypatch, {}, {})
    res = ed.discover_engines()
    assert [s.engine_id for s in res] == ["opencode", "codex", "claude-code", "antigravity"]
    for s in res:
        assert s.install_state == "not_installed"
        assert s.auth_state == "unknown"
        assert s.executable is None
        assert s.adapter_supported is True


def test_discovery_does_not_pass_local_token_to_cli(monkeypatch):
    monkeypatch.setenv("AGENTOS_LOCAL_TOKEN", "secret")
    calls = install(monkeypatch, {"agy": "/agy"}, {("agy", "--version"): cp(0, "1.4.0")})
    ed.discover_engines()
    assert "AGENTOS_LOCAL_TOKEN" not in calls[0][1]["env"]


def test_mixed_states_windows_paths(monkeypatch):
    paths = {
        "opencode": r"C:\Users\u\AppData\Roaming\npm\opencode.CMD",
        "codex": r"C:\Users\u\AppData\Roaming\npm\codex.CMD",
        "claude": r"C:\Users\u\.local\bin\claude.exe",
        "agy": r"C:\Users\u\AppData\Local\agy\agy.exe",
    }
    responses = {
        ("opencode", "--version"): cp(0, "1.2.3\n"),
        ("opencode", "auth"): cp(0, f"Credentials ~\\.local\\share\\opencode\\auth.json\n2 credentials {SECRET}"),
        ("codex", "--version"): cp(0, "codex-cli 0.50.1\n"),
        ("codex", "login"): cp(1, "Not logged in\n"),
        ("claude", "--version"): cp(0, "2.0.5 (Claude Code)\n"),
        ("claude", "auth"): cp(0, '{"loggedIn": true, "token": "%s"}' % SECRET),
        ("agy", "--version"): cp(0, "agy 0.9.0\n"),
    }
    calls = install(monkeypatch, paths, responses)
    s = by_id(ed.discover_engines())
    assert (s["opencode"].install_state, s["opencode"].auth_state, s["opencode"].version) == ("installed", "authenticated", "1.2.3")
    assert (s["codex"].auth_state, s["codex"].version) == ("unauthenticated", "0.50.1")
    assert s["claude-code"].auth_state == "authenticated"
    assert s["antigravity"].install_state == "installed"
    assert s["antigravity"].auth_state == "unknown"
    assert s["codex"].adapter_supported and s["claude-code"].adapter_supported
    assert s["antigravity"].adapter_supported
    # no model-invoking subcommands, no shell, stdin closed
    for cmd, kw in calls:
        assert cmd[1:] in (["--version"], ["auth", "list"], ["login", "status"], ["auth", "status"])
        assert kw.get("shell") is False and kw.get("stdin") == subprocess.DEVNULL
        assert kw["timeout"] == ed.PROBE_TIMEOUT_SECONDS
    # antigravity got only the version probe
    assert [c[0][1:] for c in calls if c[0][0].endswith("agy.exe")] == [["--version"]]
    assert SECRET not in repr(s)


def test_timeout_on_version_is_unknown_install(monkeypatch):
    install(monkeypatch, {"codex": "/bin/codex"},
            {("codex", "--version"): subprocess.TimeoutExpired("codex", 5)})
    s = by_id(ed.discover_engines())["codex"]
    assert s.install_state == "unknown" and s.auth_state == "unknown"
    assert s.executable == "/bin/codex"


def test_timeout_on_auth_keeps_installed_auth_unknown(monkeypatch):
    install(monkeypatch, {"claude": "/bin/claude"}, {
        ("claude", "--version"): cp(0, "2.0.5"),
        ("claude", "auth"): subprocess.TimeoutExpired("claude", 5),
    })
    s = by_id(ed.discover_engines())["claude-code"]
    assert s.install_state == "installed" and s.auth_state == "unknown"


def test_oserror_on_launch(monkeypatch):
    install(monkeypatch, {"opencode": "/bin/opencode"},
            {("opencode", "--version"): OSError("WinError 193")})
    s = by_id(ed.discover_engines())["opencode"]
    assert s.install_state == "unknown"


def test_unrecognized_auth_output_stays_unknown(monkeypatch):
    install(monkeypatch, {"claude": "/c", "codex": "/x"}, {
        ("claude", "--version"): cp(0, "2.0.5"), ("claude", "auth"): cp(0, "garbage"),
        ("codex", "--version"): cp(0, "0.1.0"), ("codex", "login"): cp(2, "boom"),
    })
    s = by_id(ed.discover_engines())
    assert s["claude-code"].auth_state == "unknown"
    assert s["codex"].auth_state == "unknown"


def test_version_failure_without_version_is_unknown(monkeypatch):
    install(monkeypatch, {"agy": "/agy"}, {("agy", "--version"): cp(1, "", "error")})
    assert by_id(ed.discover_engines())["antigravity"].install_state == "unknown"


def test_no_gemini():
    assert all("gemini" not in s.engine_id for s in ed.ENGINE_SPECS)


@pytest.mark.asyncio
async def test_capabilities_do_not_claim_unenforced_permissions(api):
    caps = await api("engines.capabilities")
    assert caps["claude-code"]["conversation_runs"] is True
    assert caps["antigravity"]["conversation_runs"] is True
    assert caps["antigravity"]["permission_policy"] == "plan_mode_and_deny_all_requests"
    assert caps["claude-code"]["tool_approvals"] is True
    assert caps["claude-code"]["workspace_restriction"] == "scoped_mcp_tools"
    assert all(not c["tool_approvals"] and not c["workspace_restriction"]
               for name, c in caps.items() if name != "claude-code")


@pytest.mark.asyncio
async def test_endpoint_typed_and_leak_free(monkeypatch, api):
    install(monkeypatch, {"codex": "/bin/codex"}, {
        ("codex", "--version"): cp(0, "codex-cli 0.50.1"),
        ("codex", "login"): cp(0, f"Logged in using ChatGPT {SECRET}"),
    })
    import json
    body = await api("engines.status")
    assert SECRET not in json.dumps(body)
    assert len(body["engines"]) == 4
    codex = next(e for e in body["engines"] if e["engine_id"] == "codex")
    assert codex["install_state"] == "installed" and codex["auth_state"] == "authenticated"
    assert codex["adapter_supported"] is True
    assert next(e for e in body["engines"] if e["engine_id"] == "opencode")["install_state"] == "not_installed"


@pytest.mark.asyncio
async def test_direct_engine_tasks_are_not_exposed(api):
    err = await api.fail("engines.tasks", engine_id="antigravity", prompt="hello", model_id="x")
    assert err.code == "method_not_found"


def test_opencode_v2_credential_rows_count_as_signed_in(monkeypatch):
    rows = "OpenAI       API key                     stored\nNvidia       NVIDIA_API_KEY              environment\n"
    install(monkeypatch, {"opencode": "/bin/opencode"},
            {("opencode", "--version"): cp(0, "2.0.29\n"), ("opencode", "auth"): cp(0, rows)})
    assert by_id(ed.discover_engines())["opencode"].auth_state == "authenticated"
    install(monkeypatch, {"opencode": "/bin/opencode"},
            {("opencode", "--version"): cp(0, "2.0.29\n"), ("opencode", "auth"): cp(0, "something unrecognized\n")})
    assert by_id(ed.discover_engines())["opencode"].auth_state == "unknown"
