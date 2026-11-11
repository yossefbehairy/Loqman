"""
Isolated contract tests for AgyCliRuntime against a FAKE agy CLI.
No real `agy` binary, network, login, or model is ever used.
"""

import asyncio
import json
import sys
import textwrap

import pytest

from backend.app.runtime.base import ExecuteAgentTask
from backend.app.runtime import agy_runtime as ag
from backend.app.runtime.agy_runtime import AgyCliRuntime, RuntimeUnsupported, parse_models
from backend.app.runtime.errors import (
    RuntimeAuthError, RuntimeCancelled, RuntimeErrorBase, RuntimeModelUnavailable,
    RuntimeOffline, RuntimeRateLimit, RuntimeTimeout,
)

FAKE = textwrap.dedent('''
    import json, os, sys, time
    mode = os.environ.get("FAKE_MODE", "ok")
    args = sys.argv[1:]
    log = os.environ.get("FAKE_LOG")
    if args == ["--version"]:
        print("1.4.0"); sys.exit(0)
    if args == ["--help"]:
        h = ("Usage of agy.exe: --print --sandbox --model --effort "
             "--disable-slash-commands --dangerously-skip-permissions")
        if mode == "old_help": h = "--print --model"
        print(h); sys.exit(0)
    if args == ["models"]:
        if mode == "no_models": sys.exit(1)
        print("Fetching available models...")
        print("m-a\\tModel A"); print("m-b\\tModel B"); print("bad id\\tNope"); print("m-a\\tDup")
        sys.exit(0)
    stdin_is_tty = sys.stdin.isatty()
    stdin_data = "" if stdin_is_tty else sys.stdin.read()
    if log:
        with open(log, "w", encoding="utf-8") as f:
            json.dump({"args": args, "stdin": stdin_data, "cwd": os.getcwd(),
                       "has_key": "GEMINI_API_KEY" in os.environ}, f)
    if mode == "sleep":
        time.sleep(60)
    if mode == "auth_err":
        sys.stderr.write('AGY_ERROR: {"status":"UNAUTHENTICATED","code":401}'); sys.exit(3)
    if mode == "rate":
        sys.stderr.write("Your AI credits balance is too low"); sys.exit(3)
    if mode == "model_err":
        sys.stderr.write("model not found"); sys.exit(3)
    if mode == "crash":
        sys.stderr.write("secret-internal-trace"); sys.exit(1)
    if mode == "empty":
        sys.exit(0)
    if mode == "err_exit0":
        sys.stdout.write("partial"); sys.stderr.write('AGY_ERROR: {"x":1}'); sys.exit(0)
    prompt = args[args.index("--print") + 1]
    sys.stdout.write("\\ufeff  hello " + prompt + "\\n")
''')


@pytest.fixture
def fake(tmp_path, monkeypatch):
    monkeypatch.setenv("PYTHONUTF8", "1")
    script = tmp_path / "fake_agy.py"
    script.write_text(FAKE, encoding="utf-8")
    log = tmp_path / "log.json"
    monkeypatch.setenv("FAKE_LOG", str(log))
    monkeypatch.setenv("GEMINI_API_KEY", "key-should-be-stripped")

    def make(mode="ok", **kw):
        monkeypatch.setenv("FAKE_MODE", mode)
        return AgyCliRuntime(cli_cmd=[sys.executable, str(script)], **kw)

    make.log = lambda: json.loads(log.read_text(encoding="utf-8"))
    return make


def task(**kw):
    base = dict(prompt="ping", model_id="m-a", session_title="t1", timeout=30)
    base.update(kw)
    return ExecuteAgentTask(**base)


def run(coro):
    return asyncio.run(coro)


def test_happy_path_and_command_shape(fake):
    rt = fake()
    res = run(rt.execute_task(task(variant="high")))
    assert res.text == "hello ping"
    assert res.session_id is None and res.cost is None and res.tokens == {}
    assert res.variant_id == "high" and res.model_id == "m-a"
    rec = fake.log()
    a = rec["args"]
    assert a[0] == "--print" and a[1] == "ping"
    assert "--sandbox" in a and "--disable-slash-commands" in a
    assert a[a.index("--model") + 1] == "m-a" and a[a.index("--effort") + 1] == "high"
    forbidden = ("--dangerously-skip-permissions", "--mode", "--add-dir", "--project",
                 "--new-project", "--agent", "--continue", "-c", "--conversation",
                 "--remote-control", "--output-format", "--input-format", "-i",
                 "--prompt-interactive")
    assert not any(x in forbidden for x in a)
    assert "agentos-agy-" in rec["cwd"]
    assert rec["stdin"] == ""                       # stdin closed: nobody can approve prompts
    assert rec["has_key"] is False                  # no silent API-key billing


def test_no_variant_omits_effort(fake):
    run(fake().execute_task(task()))
    assert "--effort" not in fake.log()["args"]


def test_api_key_env_only_when_opted_in(fake):
    run(fake(allow_api_key_env=True).execute_task(task()))
    assert fake.log()["has_key"] is True


def test_slash_prompt_passed_as_data_with_slash_commands_disabled(fake):
    res = run(fake().execute_task(task(prompt="/usage --dangerously-skip-permissions")))
    a = fake.log()["args"]
    assert res.text == "hello /usage --dangerously-skip-permissions"
    assert a[1] == "/usage --dangerously-skip-permissions" and "--disable-slash-commands" in a


@pytest.mark.parametrize("mode,exc", [
    ("auth_err", RuntimeAuthError),
    ("rate", RuntimeRateLimit),
    ("model_err", RuntimeModelUnavailable),
    ("crash", RuntimeErrorBase),
    ("empty", RuntimeErrorBase),
    ("err_exit0", RuntimeErrorBase),
    ("old_help", RuntimeUnsupported),
])
def test_failures_are_normalized_and_do_not_leak(fake, mode, exc):
    with pytest.raises(exc) as ei:
        run(fake(mode).execute_task(task()))
    assert "secret-internal-trace" not in str(ei.value)


def test_fails_closed_on_bad_inputs(fake):
    rt = fake()
    with pytest.raises(RuntimeUnsupported):
        run(rt.execute_task(task(variant="turbo")))
    with pytest.raises(RuntimeModelUnavailable):
        run(rt.execute_task(task(model_id="--dangerously-skip-permissions")))
    with pytest.raises(RuntimeModelUnavailable):
        run(rt.execute_task(task(model_id="a b")))
    with pytest.raises(RuntimeErrorBase):
        run(rt.execute_task(task(prompt="   ")))


def test_missing_binary_is_offline():
    rt = AgyCliRuntime(cli_cmd=[sys.executable + ".nope"])
    # flag probe fails first -> unsupported/closed; either way no execution
    with pytest.raises((RuntimeOffline, RuntimeUnsupported)):
        run(rt.execute_task(task()))
    rt2 = AgyCliRuntime(cli_cmd=None)
    rt2._cmd = None
    with pytest.raises(RuntimeOffline):
        run(rt2.execute_task(task()))
    assert run(rt2.check_health()).status == "offline"


def test_timeout_kills_child(fake, monkeypatch):
    monkeypatch.setattr(ag, "MIN_TASK_TIMEOUT", 1)
    with pytest.raises(RuntimeTimeout):
        run(fake("sleep").execute_task(task(timeout=1)))


def test_abort_inflight_task(fake, monkeypatch):
    rt = fake("sleep")

    async def scenario():
        t = asyncio.create_task(rt.execute_task(task(timeout=60)))
        for _ in range(100):
            if "t1" in rt._inflight:
                break
            await asyncio.sleep(0.1)
        await asyncio.sleep(0.5)
        await rt.abort_task("t1")
        with pytest.raises(RuntimeCancelled):
            await t
    run(scenario())


def test_abort_unknown_is_noop(fake):
    run(fake().abort_task("nothing"))


def test_health_and_models(fake):
    rt = fake()
    h = run(rt.check_health())
    assert h.status == "degraded" and h.models_count == 2 and h.free_models_count == 0
    assert h.default_model_status == "unverified"
    assert run(rt.list_available_models(free_only=True)) == []
    ms = run(rt.list_available_models(free_only=False))
    assert [m.id for m in ms] == ["m-a", "m-b"]
    assert all(not m.is_free and m.status == "unverified" for m in ms)


def test_health_degraded_without_models_or_flags(fake):
    assert run(fake("no_models").check_health()).status == "degraded"
    assert run(fake("old_help").check_health()).status == "degraded"


def test_parse_models_ignores_noise():
    assert parse_models("Fetching...\nid-1\tOne\n\tx\nid 2\tbad\n") == [{"id": "id-1", "name": "One"}]


def test_factory_selects_agy_only_when_configured(monkeypatch):
    from backend.app import runtime as rtpkg
    from backend.app.runtime.opencode_runtime import RealOpenCodeRuntime
    monkeypatch.setattr(rtpkg, "_runtime_instance", None)
    monkeypatch.setattr(rtpkg.settings, "RUNTIME_MODE", "agy")
    assert isinstance(rtpkg.get_runtime(), AgyCliRuntime)
    monkeypatch.setattr(rtpkg, "_runtime_instance", None)
    monkeypatch.setattr(rtpkg.settings, "RUNTIME_MODE", "real")
    assert isinstance(rtpkg.get_runtime(), RealOpenCodeRuntime)
    monkeypatch.setattr(rtpkg, "_runtime_instance", None)
