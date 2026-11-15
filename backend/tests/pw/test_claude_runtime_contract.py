"""
Isolated contract tests for ClaudeCodeRuntime against a FAKE claude CLI.
No real `claude` binary, network, login, or model is ever used.
"""

import asyncio
import json
import sys
import textwrap

import pytest

from backend.pyworker.runtime.base import ExecuteAgentTask
from backend.pyworker.runtime import claude_runtime as cr
from backend.pyworker.runtime.claude_runtime import ClaudeCodeRuntime, RuntimeUnsupported
from backend.pyworker.runtime.errors import (
    RuntimeAuthError, RuntimeCancelled, RuntimeErrorBase, RuntimeModelUnavailable,
    RuntimeOffline, RuntimeRateLimit, RuntimeTimeout,
)

FAKE = textwrap.dedent('''
    import json, os, sys, time
    mode = os.environ.get("FAKE_MODE", "ok")
    args = sys.argv[1:]
    log = os.environ.get("FAKE_LOG")
    if args == ["--version"]:
        print("2.1.0 (Claude Code)"); sys.exit(0)
    if args == ["--help"]:
        h = "--print --output-format --tools --strict-mcp-config --no-session-persistence --model"
        if mode == "old_help": h = "--print --output-format"
        print(h); sys.exit(0)
    if args == ["auth", "status"]:
        print(json.dumps({"loggedIn": mode != "logged_out"})); sys.exit(0)
    stdin = sys.stdin.read()
    if log:
        with open(log, "w", encoding="utf-8") as f:
            json.dump({"args": args, "stdin": stdin, "cwd": os.getcwd(),
                       "has_key": "ANTHROPIC_API_KEY" in os.environ}, f)
    if mode == "sleep":
        time.sleep(60)
    if mode == "auth_err":
        sys.stderr.write("Not logged in - run /login"); sys.exit(1)
    if mode == "rate":
        sys.stderr.write("429 rate limit"); sys.exit(1)
    if mode == "crash":
        sys.stdout.write('{"type": "resu'); sys.exit(3)
    if mode == "bad_json":
        print("not json at all"); sys.exit(0)
    if mode == "is_error":
        print(json.dumps({"type": "result", "is_error": True, "result": "model not found"})); sys.exit(0)
    if mode == "empty":
        print(json.dumps({"type": "result", "is_error": False, "result": "  "})); sys.exit(0)
    if mode == "array":
        print(json.dumps([{"type": "system"}, {"type": "result", "is_error": False, "result": "from-array"}])); sys.exit(0)
    print(json.dumps({"type": "result", "subtype": "success", "is_error": False,
                      "result": "hello " + stdin.strip(), "session_id": "native-1",
                      "total_cost_usd": 0.0123,
                      "usage": {"input_tokens": 3, "output_tokens": 5, "junk": "x"},
                      "modelUsage": {"claude-sonnet-x": {}}}))
''')


@pytest.fixture
def fake(tmp_path, monkeypatch):
    script = tmp_path / "fake_claude.py"
    script.write_text(FAKE, encoding="utf-8")
    log = tmp_path / "log.json"
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-should-be-stripped")

    def make(mode="ok", **kw):
        # S5: the child environment is an allowlist, so the fake's control variables are passed explicitly.
        return ClaudeCodeRuntime(cli_cmd=[sys.executable, str(script)],
                                 extra_env={"FAKE_MODE": mode, "FAKE_LOG": str(log)}, **kw)

    make.log = lambda: json.loads(log.read_text(encoding="utf-8"))
    return make


def task(**kw):
    base = dict(prompt="ping", model_id="sonnet", provider_id="anthropic", session_title="t1", timeout=30)
    base.update(kw)
    return ExecuteAgentTask(**base)


def run(coro):
    return asyncio.run(coro)


def test_happy_path_and_command_shape(fake):
    rt = fake()
    res = run(rt.execute_task(task()))
    assert res.text == "hello ping"
    assert res.session_id is None and res.cost is None
    assert res.model_id == "claude-sonnet-x"
    assert res.tokens == {"input_tokens": 3, "output_tokens": 5, "reported_cost_usd": 0.0123}
    rec = fake.log()
    a = rec["args"]
    assert a[:1] == ["-p"] and "--no-session-persistence" in a and "--strict-mcp-config" in a
    assert a[a.index("--tools") + 1] == ""          # no tools, not blanket access
    assert a[a.index("--model") + 1] == "sonnet"
    assert not any(x in a for x in ("--dangerously-skip-permissions", "--allowedTools",
                                    "--permission-mode", "ping"))  # prompt via stdin only
    assert rec["stdin"] == "ping"
    assert "agentos-claude-" in rec["cwd"]          # isolated temp cwd
    assert rec["has_key"] is False                  # no silent API-key billing


def test_api_key_env_only_when_opted_in(fake):
    run(fake(allow_api_key_env=True).execute_task(task()))
    assert fake.log()["has_key"] is True


def test_array_output_shape(fake):
    assert run(fake("array").execute_task(task())).text == "from-array"


@pytest.mark.parametrize("mode,exc", [
    ("auth_err", RuntimeAuthError), ("rate", RuntimeRateLimit),
    ("crash", RuntimeErrorBase), ("bad_json", RuntimeErrorBase),
    ("is_error", RuntimeModelUnavailable), ("empty", RuntimeErrorBase),
])
def test_failures_map(fake, mode, exc):
    with pytest.raises(exc) as ei:
        run(fake(mode).execute_task(task()))
    assert "sk-" not in str(ei.value)


def test_timeout_kills_process(fake, monkeypatch):
    monkeypatch.setattr(cr, "MIN_TASK_TIMEOUT", 1)
    rt = fake("sleep")
    with pytest.raises(RuntimeTimeout):
        run(rt.execute_task(task(timeout=1)))
    assert rt._inflight == {}


def test_abort_in_flight_and_noop_otherwise(fake):
    rt = fake("sleep")

    async def scenario():
        t = asyncio.create_task(rt.execute_task(task(session_title="abort-me", timeout=60)))
        for _ in range(100):
            if "abort-me" in rt._inflight:
                break
            await asyncio.sleep(0.05)
        await rt.abort_task("abort-me")
        await rt.abort_task("unknown")      # no-op, must not raise
        with pytest.raises(RuntimeCancelled):
            await t
    run(scenario())
    assert rt._inflight == {}


def test_asyncio_cancel_kills_child(fake):
    rt = fake("sleep")

    async def scenario():
        t = asyncio.create_task(rt.execute_task(task(session_title="c1", timeout=60)))
        for _ in range(100):
            if "c1" in rt._inflight:
                break
            await asyncio.sleep(0.05)
        proc = rt._inflight["c1"]
        t.cancel()
        with pytest.raises(asyncio.CancelledError):
            await t
        assert proc.returncode is not None
    run(scenario())


def test_missing_flags_fail_closed(fake):
    with pytest.raises(RuntimeUnsupported):
        run(fake("old_help").execute_task(task()))


def test_variant_and_bad_model_rejected(fake):
    rt = fake()
    with pytest.raises(RuntimeUnsupported):
        run(rt.execute_task(task(variant="high")))
    with pytest.raises(RuntimeModelUnavailable):
        run(rt.execute_task(task(model_id="x; rm -rf /")))
    with pytest.raises(RuntimeErrorBase):
        run(rt.execute_task(task(prompt="   ")))


def test_cli_missing(monkeypatch):
    monkeypatch.setattr(cr.shutil, "which", lambda _n: None)
    rt = ClaudeCodeRuntime()
    with pytest.raises(RuntimeOffline):
        run(rt.execute_task(task()))
    h = run(rt.check_health())
    assert h.status == "offline"


def test_health_states(fake):
    h = run(fake().check_health())
    assert h.status == "ok" and h.default_model_status == "unverified"
    assert h.free_models_count == 0
    assert run(fake("logged_out").check_health()).status == "degraded"
    assert run(fake("old_help").check_health()).status == "degraded"


def test_models_never_free_or_verified():
    rt = ClaudeCodeRuntime(cli_cmd=["nonexistent"])
    assert run(rt.list_available_models(free_only=True)) == []
    ms = run(rt.list_available_models(free_only=False))
    assert {m.id for m in ms} == {"sonnet", "opus", "haiku"}
    assert all(not m.is_free and m.status == "unverified" for m in ms)


def test_factory_selection_and_default(monkeypatch):
    import backend.pyworker.runtime as rtpkg
    from backend.pyworker.runtime.opencode_runtime import RealOpenCodeRuntime
    monkeypatch.setattr(rtpkg, "_runtime_instance", None)
    monkeypatch.setattr(rtpkg.settings, "RUNTIME_MODE", "real")
    assert isinstance(rtpkg.get_runtime(), RealOpenCodeRuntime)   # default untouched
    monkeypatch.setattr(rtpkg, "_engine_instances", {})
    assert isinstance(rtpkg.get_runtime("claude-code"), ClaudeCodeRuntime)   # chosen by engine id, not by RUNTIME_MODE
    monkeypatch.setattr(rtpkg, "_runtime_instance", None)
