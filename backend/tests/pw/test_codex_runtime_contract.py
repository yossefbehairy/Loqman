"""
Isolated contract tests for CodexCliRuntime against a FAKE codex CLI.
No real `codex` binary, network, login, or model is ever used.
"""

import asyncio
import json
import sys
import textwrap

import pytest

from backend.pyworker.runtime.base import ExecuteAgentTask
from backend.pyworker.runtime import codex_runtime as cx
from backend.pyworker.runtime.codex_runtime import CodexCliRuntime, RuntimeUnsupported
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
        print("codex-cli 0.167.0"); sys.exit(0)
    if args == ["exec", "--help"]:
        h = ("--json --ephemeral --sandbox --skip-git-repo-check --ignore-user-config "
             "--ignore-rules --cd --model")
        if mode == "old_help": h = "--json"
        print(h); sys.exit(0)
    if args == ["login", "status"]:
        print("Not logged in" if mode == "logged_out" else "Logged in using ChatGPT"); sys.exit(0)
    if args == ["debug", "models"]:
        print(json.dumps({"models": [
            {"slug": "m-a", "display_name": "Model A", "visibility": "list",
             "supported_reasoning_levels": [{"effort": "low"}, {"effort": "high"}, {"effort": "ultra"}]},
            {"slug": "hidden", "display_name": "H", "visibility": "hide", "supported_reasoning_levels": []},
        ]})); sys.exit(0)
    stdin = sys.stdin.read()
    if log:
        with open(log, "w", encoding="utf-8") as f:
            json.dump({"args": args, "stdin": stdin, "cwd": os.getcwd(),
                       "has_key": "OPENAI_API_KEY" in os.environ}, f)
    def ev(o): print(json.dumps(o), flush=True)
    if mode == "sleep":
        time.sleep(60)
    if mode == "auth_err":
        sys.stderr.write("Not logged in - run codex login"); sys.exit(1)
    if mode == "rate":
        ev({"type": "turn.failed", "error": {"message": "429 usage limit reached"}}); sys.exit(1)
    if mode == "crash":
        sys.stdout.write('{"type": "item.comp'); sys.exit(3)
    if mode == "no_message":
        ev({"type": "thread.started", "thread_id": "t"}); sys.exit(0)
    if mode == "error_event":
        ev({"type": "error", "message": "model not found"}); sys.exit(0)
    print("banner line, not json")
    ev({"type": "thread.started", "thread_id": "native-1"})
    ev({"type": "item.completed", "item": {"type": "reasoning", "text": "thinking"}})
    ev({"type": "item.completed", "item": {"type": "agent_message", "text": "hello " + stdin.strip()}})
    ev({"type": "turn.completed", "usage": {"input_tokens": 3, "cached_input_tokens": 1,
                                             "output_tokens": 5, "junk": "x"}})
''')


@pytest.fixture
def fake(tmp_path, monkeypatch):
    script = tmp_path / "fake_codex.py"
    script.write_text(FAKE, encoding="utf-8")
    log = tmp_path / "log.json"
    monkeypatch.setenv("FAKE_LOG", str(log))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-should-be-stripped")

    def make(mode="ok", **kw):
        monkeypatch.setenv("FAKE_MODE", mode)
        return CodexCliRuntime(cli_cmd=[sys.executable, str(script)], **kw)

    make.log = lambda: json.loads(log.read_text(encoding="utf-8"))
    return make


def task(**kw):
    base = dict(prompt="ping", model_id="m-a", provider_id="openai", session_title="t1", timeout=30)
    base.update(kw)
    return ExecuteAgentTask(**base)


def run(coro):
    return asyncio.run(coro)


def test_happy_path_and_command_shape(fake):
    rt = fake()
    res = run(rt.execute_task(task(variant="high")))
    assert res.text == "hello ping"
    assert res.session_id is None and res.cost is None
    assert res.variant_id == "high" and res.model_id == "m-a"
    assert res.tokens == {"input_tokens": 3, "cached_input_tokens": 1, "output_tokens": 5}
    rec = fake.log()
    a = rec["args"]
    assert a[0] == "exec" and "--json" in a and "--ephemeral" in a
    assert a[a.index("--sandbox") + 1] == "read-only"
    assert "approval_policy=\"never\"" in a and "model_reasoning_effort=\"high\"" in a
    assert "--ignore-user-config" in a and "--ignore-rules" not in a
    assert a[-1] == "-" and a[a.index("--model") + 1] == "m-a"
    assert not any(x.startswith("--dangerously") or x in ("--full-auto", "--add-dir", "--oss",
                                                          "--worktree", "ping") for x in a)
    assert rec["stdin"] == "ping"                       # prompt via stdin only
    assert "agentos-codex-" in rec["cwd"] and a[a.index("--cd") + 1] == rec["cwd"]
    assert rec["has_key"] is False                      # no silent API-key billing


def test_api_key_env_only_when_opted_in(fake):
    run(fake(allow_api_key_env=True).execute_task(task()))
    assert fake.log()["has_key"] is True


@pytest.mark.parametrize("mode,exc", [
    ("auth_err", RuntimeAuthError), ("rate", RuntimeRateLimit),
    ("crash", RuntimeErrorBase), ("no_message", RuntimeErrorBase),
    ("error_event", RuntimeModelUnavailable),
])
def test_failures_map(fake, mode, exc):
    with pytest.raises(exc) as ei:
        run(fake(mode).execute_task(task()))
    assert "sk-" not in str(ei.value)


def test_timeout_kills_process(fake, monkeypatch):
    monkeypatch.setattr(cx, "MIN_TASK_TIMEOUT", 1)
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
        await rt.abort_task("unknown")
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
        assert rt._inflight == {}
    run(scenario())


def test_missing_flags_fail_closed(fake):
    with pytest.raises(RuntimeUnsupported):
        run(fake("old_help").execute_task(task()))


def test_variant_and_bad_input_rejected(fake):
    rt = fake()
    with pytest.raises(RuntimeUnsupported):
        run(rt.execute_task(task(variant="ultra")))       # sub-agent delegation
    with pytest.raises(RuntimeUnsupported):
        run(rt.execute_task(task(variant='high"; x="')))
    with pytest.raises(RuntimeModelUnavailable):
        run(rt.execute_task(task(model_id="x; rm -rf /")))
    with pytest.raises(RuntimeErrorBase):
        run(rt.execute_task(task(prompt="   ")))


def test_cli_missing(monkeypatch):
    monkeypatch.setattr(cx.shutil, "which", lambda _n: None)
    rt = CodexCliRuntime()
    with pytest.raises(RuntimeOffline):
        run(rt.execute_task(task()))
    assert run(rt.check_health()).status == "offline"


def test_health_states(fake):
    h = run(fake().check_health())
    assert h.status == "ok" and h.default_model_status == "unverified"
    assert h.free_models_count == 0 and h.models_count == 1
    assert run(fake("logged_out").check_health()).status == "degraded"
    assert run(fake("old_help").check_health()).status == "degraded"


def test_models_never_free_hidden_and_ultra_excluded(fake):
    rt = fake()
    assert run(rt.list_available_models(free_only=True)) == []
    ms = run(rt.list_available_models(free_only=False))
    assert [m.id for m in ms] == ["m-a"]                  # hidden model dropped
    assert ms[0].variants == ["low", "high"]              # ultra dropped
    assert not ms[0].is_free and ms[0].status == "unverified"


def test_models_empty_when_cli_absent():
    rt = CodexCliRuntime(cli_cmd=["nonexistent-codex-binary"])
    assert run(rt.list_available_models(free_only=False)) == []


def test_factory_selection_and_default(monkeypatch):
    import backend.pyworker.runtime as rtpkg
    from backend.pyworker.runtime.opencode_runtime import RealOpenCodeRuntime
    monkeypatch.setattr(rtpkg, "_runtime_instance", None)
    monkeypatch.setattr(rtpkg.settings, "RUNTIME_MODE", "real")
    assert isinstance(rtpkg.get_runtime(), RealOpenCodeRuntime)   # default untouched
    monkeypatch.setattr(rtpkg, "_engine_instances", {})
    assert isinstance(rtpkg.get_runtime("codex"), CodexCliRuntime)   # chosen by engine id, not by RUNTIME_MODE
    monkeypatch.setattr(rtpkg, "_runtime_instance", None)
