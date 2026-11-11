"""
Isolated contract tests for AgyAcpRuntime against a FAKE `agy acp` server.
No real `agy` binary, network, login, credentials or model is ever used.
"""

import asyncio
import json
import os
import subprocess
import sys
import textwrap
import time

import pytest

from backend.app.runtime import agy_acp_runtime as ac
from backend.app.runtime.agy_acp_runtime import AgyAcpRuntime, RuntimeUnsupported
from backend.app.runtime.base import ExecuteAgentTask
from backend.app.runtime.errors import (
    RuntimeAuthError, RuntimeCancelled, RuntimeErrorBase, RuntimeModelUnavailable,
    RuntimeOffline, RuntimeTimeout,
)

FAKE = textwrap.dedent('''
    import json, os, sys, time
    mode = os.environ.get("FAKE_MODE", "ok")
    log = os.environ["FAKE_LOG"]
    out = sys.stdout.buffer

    def record(kind, obj):
        with open(log, "a", encoding="utf-8") as f:
            f.write(json.dumps({"k": kind, "v": obj}) + "\\n")

    def send(obj, fragment=False):
        data = (json.dumps(obj) + "\\n").encode()
        if fragment:
            mid = len(data) // 2
            out.write(data[:mid]); out.flush(); time.sleep(0.05)
            out.write(data[mid:]); out.flush()
        else:
            out.write(data); out.flush()

    def recv():
        line = sys.stdin.buffer.readline()
        if not line:
            sys.exit(0)
        msg = json.loads(line)
        record("in", msg)
        return msg

    def ok(mid, result): send({"jsonrpc": "2.0", "id": mid, "result": result})
    def err(mid, code, message): send({"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}})
    def update(sid, upd, fragment=False):
        send({"jsonrpc": "2.0", "method": "session/update",
              "params": {"sessionId": sid, "update": upd}}, fragment)
    def chunk(sid, text, kind="agent_message_chunk", fragment=False):
        update(sid, {"sessionUpdate": kind, "content": {"type": "text", "text": text}}, fragment)

    record("boot", {"argv": sys.argv[1:], "cwd": os.getcwd(), "pid": os.getpid(),
                    "has_key": "GEMINI_API_KEY" in os.environ,
                    "has_token": "AGENTOS_LOCAL_TOKEN" in os.environ})

    SID = "sess-1"
    MODELS = [{"modelId": "claude-sonnet-4-6", "name": "Claude Sonnet 4.6"},
              {"modelId": "gem-x", "name": "Gem X"}]
    MODES = [{"id": "default", "name": "Default"}, {"id": "accept-edits", "name": "Accept edits"},
             {"id": "plan", "name": "Plan"}]
    if mode == "no_plan":
        MODES = MODES[:2]
    CONFIG = []
    if mode in ("model_via_config", "config_keeps_old"):
        CONFIG = [{"id": "model", "name": "Model", "category": "model", "type": "select",
                   "currentValue": "gem-x",
                   "options": [{"value": "claude-sonnet-4-6", "name": "S"}, {"value": "gem-x", "name": "G"}]}]

    while True:
        msg = recv()
        m = msg.get("method")
        mid = msg.get("id")
        if m == "initialize":
            if mode == "bad_init":
                err(mid, -32603, "boom"); continue
            if mode == "old_protocol":
                ok(mid, {"protocolVersion": 0}); continue
            ok(mid, {"protocolVersion": 1, "agentCapabilities": {}, "authMethods": []})
        elif m == "session/new":
            if mode == "auth_required":
                err(mid, -32000, "Authentication required"); continue
            ok(mid, {"sessionId": SID,
                     "models": {"availableModels": MODELS, "currentModelId": "gem-x"},
                     "modes": {"availableModes": MODES, "currentModeId": "default"},
                     "configOptions": CONFIG})
        elif m == "session/set_mode":
            if mode == "set_mode_err":
                err(mid, -32602, "invalid mode"); continue
            ok(mid, {})
        elif m == "session/set_model":
            if mode == "set_model_err":
                err(mid, -32602, "invalid model"); continue
            if mode in ("model_via_config", "config_keeps_old", "no_model_api"):
                err(mid, -32601, "Method not found"); continue
            ok(mid, {})
        elif m == "session/set_config_option":
            if mode == "config_keeps_old":
                ok(mid, {"configOptions": [{"id": "model", "currentValue": "gem-x"}]}); continue
            ok(mid, {"configOptions": [{"id": "model", "currentValue": msg["params"]["value"]}]})
        elif m == "session/cancel":
            if mode == "cancel_ack":
                ok(PROMPT_ID, {"stopReason": "cancelled"})
            # hang_ignore: do nothing
        elif m == "session/prompt":
            PROMPT_ID = mid
            if mode in ("hang", "hang_ignore", "cancel_ack"):
                record("prompting", True)
                continue                      # never answers; keeps reading (sees cancel)
            if mode == "malformed":
                out.write(b"this is not json\\n"); out.flush(); continue
            if mode == "oversize":
                out.write(b"x" * 20000 + b"\\n"); out.flush(); continue
            if mode == "not_rpc":
                send({"hello": "world"}); continue
            if mode == "bad_id":
                ok(999, {"stopReason": "end_turn"}); continue
            if mode == "exit_early":
                sys.exit(3)
            if mode == "mode_switch":
                update(SID, {"sessionUpdate": "current_mode_update", "currentModeId": "accept-edits"})
                ok(mid, {"stopReason": "end_turn"}); continue
            if mode == "refusal":
                ok(mid, {"stopReason": "refusal"}); continue
            if mode == "empty":
                ok(mid, {"stopReason": "end_turn"}); continue
            if mode == "max_tokens":
                chunk(SID, "incomplete")
                ok(mid, {"stopReason": "max_tokens"}); continue
            if mode == "prompt_rate":
                err(mid, -32603, "429 quota exceeded"); continue
            if mode == "flood":
                for _ in range(50):
                    chunk(SID, "A" * 100)
                ok(mid, {"stopReason": "end_turn"}); continue
            # ok: fragmented JSON, interleaved noise and every kind of client request
            chunk("other-session", "LEAK")
            chunk(SID, "Hel", fragment=True)
            chunk(SID, "plan thinking", kind="agent_thought_chunk")
            update(SID, {"sessionUpdate": "tool_call", "toolCallId": "t1", "title": "x"})
            send({"jsonrpc": "2.0", "id": "perm-1", "method": "session/request_permission",
                  "params": {"sessionId": SID, "toolCall": {"toolCallId": "t1"},
                             "options": [{"optionId": "allow", "name": "Allow", "kind": "allow_once"},
                                         {"optionId": "always", "name": "Always", "kind": "allow_always"}]}})
            recv()
            send({"jsonrpc": "2.0", "id": 77, "method": "fs/read_text_file",
                  "params": {"sessionId": SID, "path": "/etc/passwd"}})
            recv()
            send({"jsonrpc": "2.0", "id": "t-1", "method": "terminal/create",
                  "params": {"sessionId": SID, "command": "rm"}})
            recv()
            send({"jsonrpc": "2.0", "id": "u-1", "method": "something/unknown"})
            recv()
            send({"jsonrpc": "2.0", "method": "unknown/notification", "params": {}})
            chunk(SID, "lo ", fragment=True)
            chunk(SID, "world")
            ok(mid, {"stopReason": "end_turn"})
        else:
            if mid is not None:
                err(mid, -32601, "Method not found")
''')


@pytest.fixture
def fake(tmp_path, monkeypatch):
    monkeypatch.setenv("PYTHONUTF8", "1")
    script = tmp_path / "fake_agy_acp.py"
    script.write_text(FAKE, encoding="utf-8")
    log = tmp_path / "log.jsonl"
    monkeypatch.setenv("FAKE_LOG", str(log))
    monkeypatch.setenv("GEMINI_API_KEY", "key-should-be-stripped")
    monkeypatch.setenv("AGENTOS_LOCAL_TOKEN", "tok-should-be-stripped")
    monkeypatch.setattr(ac, "MIN_TASK_TIMEOUT", 1)
    monkeypatch.setattr(ac, "CANCEL_GRACE_SECONDS", 0.5)

    def make(mode="ok", **kw):
        monkeypatch.setenv("FAKE_MODE", mode)
        return AgyAcpRuntime(cli_cmd=[sys.executable, str(script)], **kw)

    def records():
        if not log.exists():
            return []
        return [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines() if x.strip()]

    make.records = records
    make.methods = lambda: [r["v"].get("method") for r in records() if r["k"] == "in" and "method" in r["v"]]
    make.boot = lambda: [r["v"] for r in records() if r["k"] == "boot"][0]
    return make


def task(**kw):
    base = dict(prompt="ping", model_id="claude-sonnet-4-6", session_title="t1", timeout=30)
    base.update(kw)
    return ExecuteAgentTask(**base)


def run(coro):
    return asyncio.run(coro)


def pid_alive(pid: int) -> bool:
    if sys.platform == "win32":
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True).stdout
        return str(pid) in out
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def assert_process_gone(fake):
    pid = fake.boot()["pid"]
    for _ in range(40):
        if not pid_alive(pid):
            return
        time.sleep(0.1)
    raise AssertionError("fake agy acp process is still alive")


# --------------------------------------------------------------------------- happy path

def test_happy_path_protocol_sequence_and_deny_only(fake):
    rt = fake()
    res = run(rt.execute_task(task()))
    assert res.text == "Hello world"            # fragmented frames, other session ignored
    assert res.reasoning == "plan thinking"
    assert res.session_id is None and res.cost is None and res.tokens == {} and res.variant_id is None
    assert res.model_id == "claude-sonnet-4-6"

    boot = fake.boot()
    assert boot["argv"] == ["acp", "--stdio", "--sandbox", "--include-default-customizations=false"]
    assert "agentos-agy-acp-" in boot["cwd"] and not os.path.exists(boot["cwd"])
    assert boot["has_key"] is False and boot["has_token"] is False

    methods = fake.methods()
    assert methods == ["initialize", "session/new", "session/set_mode", "session/set_model", "session/prompt"]
    ins = [r["v"] for r in fake.records() if r["k"] == "in"]
    init = ins[0]["params"]
    assert init["protocolVersion"] == 1 and init["clientInfo"]["name"] == "agentos"
    assert init["clientCapabilities"] == {"fs": {"readTextFile": False, "writeTextFile": False},
                                          "terminal": False}
    new = ins[1]["params"]
    assert new["mcpServers"] == [] and new["cwd"] == boot["cwd"]
    assert ins[2]["params"] == {"sessionId": "sess-1", "modeId": "plan"}
    assert ins[3]["params"] == {"sessionId": "sess-1", "modelId": "claude-sonnet-4-6"}
    assert ins[4]["params"] == {"sessionId": "sess-1", "prompt": [{"type": "text", "text": "ping"}]}

    replies = {r["id"]: r for r in ins if "method" not in r}
    assert replies["perm-1"]["result"] == {"outcome": {"outcome": "cancelled"}}   # denied, never selected
    assert "optionId" not in json.dumps(replies["perm-1"])
    for rid in (77, "t-1", "u-1"):
        assert replies[rid]["error"]["code"] == -32601
    assert_process_gone(fake)


def test_model_via_set_config_option_fallback(fake):
    run(fake("model_via_config").execute_task(task()))
    methods = fake.methods()
    assert "session/set_config_option" in methods
    ins = [r["v"] for r in fake.records() if r["k"] == "in" and r["v"].get("method") == "session/set_config_option"]
    assert ins[0]["params"] == {"sessionId": "sess-1", "configId": "model", "value": "claude-sonnet-4-6"}
    assert methods.index("session/set_config_option") < methods.index("session/prompt")


def test_output_is_bounded(fake, monkeypatch):
    monkeypatch.setattr(ac, "MAX_OUTPUT_CHARS", 250)
    with pytest.raises(RuntimeErrorBase, match="output limit"):
        run(fake("flood").execute_task(task()))


def test_token_limit_does_not_report_partial_success(fake):
    with pytest.raises(RuntimeErrorBase, match="stopped abnormally"):
        run(fake("max_tokens").execute_task(task()))


# --------------------------------------------------------------------------- fail-closed setup

def test_plan_mode_rejected_fails_closed_before_prompt(fake):
    with pytest.raises(RuntimeUnsupported):
        run(fake("set_mode_err").execute_task(task()))
    assert "session/prompt" not in fake.methods()
    assert_process_gone(fake)


def test_plan_mode_not_offered_fails_closed(fake):
    with pytest.raises(RuntimeUnsupported):
        run(fake("no_plan").execute_task(task()))
    assert "session/set_mode" not in fake.methods() and "session/prompt" not in fake.methods()


def test_model_not_offered(fake):
    with pytest.raises(RuntimeModelUnavailable):
        run(fake().execute_task(task(model_id="nope-model")))
    assert "session/prompt" not in fake.methods()


def test_set_model_rejected_fails_closed(fake):
    with pytest.raises(RuntimeModelUnavailable):
        run(fake("set_model_err").execute_task(task()))
    assert "session/prompt" not in fake.methods()


def test_no_model_api_and_no_config_option_fails_closed(fake):
    with pytest.raises(RuntimeModelUnavailable):
        run(fake("no_model_api").execute_task(task()))
    assert "session/prompt" not in fake.methods()


def test_config_option_that_keeps_old_model_fails_closed(fake):
    with pytest.raises(RuntimeModelUnavailable):
        run(fake("config_keeps_old").execute_task(task()))
    assert "session/prompt" not in fake.methods()


def test_variant_unsupported(fake):
    with pytest.raises(RuntimeUnsupported):
        run(fake().execute_task(task(variant="high")))
    assert fake.records() == []     # no process was even started


def test_bad_initialize_and_old_protocol_and_auth(fake):
    with pytest.raises(RuntimeErrorBase):
        run(fake("bad_init").execute_task(task()))
    with pytest.raises(RuntimeUnsupported):
        run(fake("old_protocol").execute_task(task()))
    with pytest.raises(RuntimeAuthError):
        run(fake("auth_required").execute_task(task()))


def test_prompt_rpc_error_is_classified_without_echo(fake):
    from backend.app.runtime.errors import RuntimeRateLimit
    with pytest.raises(RuntimeRateLimit) as ei:
        run(fake("prompt_rate").execute_task(task()))
    assert "429" not in str(ei.value) and "exceeded" not in str(ei.value)


def test_missing_binary_and_empty_prompt(tmp_path):
    with pytest.raises(RuntimeOffline):
        run(AgyAcpRuntime(cli_cmd=[str(tmp_path / "does-not-exist")]).execute_task(task()))
    with pytest.raises(RuntimeErrorBase):
        run(AgyAcpRuntime(cli_cmd=["x"]).execute_task(task(prompt="  ")))


# --------------------------------------------------------------------------- hostile / broken agent

@pytest.mark.parametrize("mode", ["malformed", "not_rpc", "bad_id", "exit_early"])
def test_protocol_violations_abort(fake, mode):
    with pytest.raises(RuntimeErrorBase) as ei:
        run(fake(mode).execute_task(task()))
    assert "this is not json" not in str(ei.value)
    assert_process_gone(fake)


def test_oversize_line_aborts(fake, monkeypatch):
    monkeypatch.setattr(ac, "MAX_LINE_BYTES", 4096)
    with pytest.raises(RuntimeErrorBase) as ei:
        run(fake("oversize").execute_task(task()))
    assert "oversize" in str(ei.value)
    assert_process_gone(fake)


def test_total_output_budget_aborts(fake, monkeypatch):
    monkeypatch.setattr(ac, "MAX_TOTAL_BYTES", 3000)
    with pytest.raises(RuntimeErrorBase) as ei:
        run(fake("flood").execute_task(task()))
    assert "budget" in str(ei.value)


def test_mode_switch_away_from_plan_aborts(fake):
    with pytest.raises(RuntimeUnsupported):
        run(fake("mode_switch").execute_task(task()))


def test_refusal_and_empty_reply(fake):
    with pytest.raises(RuntimeErrorBase) as ei:
        run(fake("refusal").execute_task(task()))
    assert "refusal" in str(ei.value)
    with pytest.raises(RuntimeErrorBase) as ei2:
        run(fake("empty").execute_task(task()))
    assert "no text" in str(ei2.value)


# --------------------------------------------------------------------------- timeout / cancel

def test_timeout_sends_cancel_and_kills(fake):
    t0 = time.time()
    with pytest.raises(RuntimeTimeout):
        run(fake("hang_ignore").execute_task(task(timeout=1)))
    assert time.time() - t0 < 15
    assert "session/cancel" in fake.methods()
    assert_process_gone(fake)


def _cancel_scenario(fake, mode):
    rt = fake(mode)

    async def scenario():
        t = asyncio.ensure_future(rt.execute_task(task(session_title="job-1")))
        for _ in range(200):
            if any(r["k"] == "prompting" for r in fake.records()):
                break
            await asyncio.sleep(0.05)
        else:
            raise AssertionError("fake never reached the prompt")
        await rt.abort_task("job-1")
        with pytest.raises(RuntimeCancelled):
            await t
        assert rt._inflight == {}
        await rt.abort_task("job-1")        # no-op once finished

    run(scenario())
    assert "session/cancel" in fake.methods()
    assert_process_gone(fake)


def test_abort_task_cancels_and_kills_unresponsive_agent(fake):
    _cancel_scenario(fake, "hang_ignore")


def test_abort_task_with_cooperative_agent(fake):
    _cancel_scenario(fake, "cancel_ack")


def test_caller_cancellation_cleans_up(fake):
    rt = fake("hang_ignore")

    async def scenario():
        t = asyncio.ensure_future(rt.execute_task(task()))
        for _ in range(200):
            if any(r["k"] == "prompting" for r in fake.records()):
                break
            await asyncio.sleep(0.05)
        t.cancel()
        with pytest.raises(asyncio.CancelledError):
            await t

    run(scenario())
    assert "session/cancel" in fake.methods()
    assert_process_gone(fake)


def test_duplicate_session_title_rejected(fake):
    rt = fake("hang_ignore")

    async def scenario():
        t = asyncio.ensure_future(rt.execute_task(task(session_title="dup")))
        await asyncio.sleep(0.2)
        with pytest.raises(RuntimeErrorBase):
            await rt.execute_task(task(session_title="dup"))
        await rt.abort_task("dup")
        with pytest.raises(RuntimeCancelled):
            await t

    run(scenario())


# --------------------------------------------------------------------------- health / models

def test_health_and_models_use_handshake_only(fake):
    rt = fake()
    h = run(rt.check_health())
    assert h.status == "ok" and h.models_count == 2 and h.free_models_count == 0
    assert h.default_model_status == "unverified"
    assert "session/prompt" not in fake.methods()
    assert run(rt.list_available_models(free_only=True)) == []
    models = run(rt.list_available_models(free_only=False))
    assert [m.id for m in models] == ["claude-sonnet-4-6", "gem-x"]
    assert all(m.status == "unverified" and not m.is_free for m in models)
    assert fake.methods().count("session/new") == 1       # second call served from cache


def test_health_degraded_without_plan_and_offline_without_binary(fake, tmp_path):
    assert run(fake("no_plan").check_health()).status == "degraded"
    assert run(fake("bad_init").check_health()).status == "degraded"
    assert run(AgyAcpRuntime(cli_cmd=[str(tmp_path / "nope")]).check_health()).status == "offline"


def test_api_key_env_only_when_opted_in(fake):
    run(fake(allow_api_key_env=True).execute_task(task()))
    b = fake.boot()
    assert b["has_key"] is True and b["has_token"] is False
