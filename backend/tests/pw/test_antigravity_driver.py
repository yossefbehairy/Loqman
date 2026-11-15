"""AntigravityAcpDriver against a FAKE `agy acp` (ACP over stdio). No real agy, network, login or model."""

import asyncio
import json
import os
import subprocess
import sys
import textwrap
import time
from dataclasses import replace

import pytest

from backend.pyworker.appsettings import settings
from backend.pyworker.db.core import Database
from backend.pyworker.db.migrate import init_db
from backend.pyworker.runtime import antigravity_driver as ad
from backend.pyworker.runtime import engine_registry
from backend.pyworker.runtime.antigravity_driver import AntigravityAcpDriver
from backend.pyworker.runtime.collector import collect_turn
from backend.pyworker.runtime.driver import DriverTurnError, StartTurn, ToolPolicy
from backend.pyworker.runtime.execute_driver import ExecuteTaskDriver
from backend.pyworker.runtime.mock_runtime import MockRuntime
from backend.pyworker.runtime.sessions import SessionRepository

from .helpers import Api
from .test_runs import new_conv, wait_status

FAKE = textwrap.dedent(r'''
    import json, os, sys, time, uuid
    HERE = os.path.dirname(os.path.abspath(__file__))
    MODE = os.environ.get("FAKE_MODE", "ok")
    CAPS = os.environ.get("FAKE_CAPS", "resume")      # resume | load | both | none
    args = sys.argv[1:]
    out = sys.stdout.buffer

    def log(**kw):
        with open(os.path.join(HERE, "log.jsonl"), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(kw) + "\n")

    def send(obj, fragment=False):
        data = (json.dumps(obj) + "\n").encode()
        if fragment:
            mid = len(data) // 2
            out.write(data[:mid]); out.flush(); time.sleep(0.05); out.write(data[mid:])
        else:
            out.write(data)
        out.flush()

    def raw(text):
        out.write(text if isinstance(text, bytes) else text.encode()); out.flush()

    def ok(mid, result): send({"jsonrpc": "2.0", "id": mid, "result": result})
    def err(mid, code, message, data=None):
        e = {"code": code, "message": message}
        if data is not None: e["data"] = data
        send({"jsonrpc": "2.0", "id": mid, "error": e})
    def update(sid, upd, replay=False, fragment=False):
        p = {"sessionId": sid, "update": upd}
        if replay: p["_meta"] = {"isReplay": True}
        send({"jsonrpc": "2.0", "method": "session/update", "params": p}, fragment)
    def chunk(sid, text, kind="agent_message_chunk", **kw): update(sid, {"sessionUpdate": kind, "content": {"type": "text", "text": text}}, **kw)

    def recv():
        line = sys.stdin.buffer.readline()
        if not line: sys.exit(0)
        msg = json.loads(line)
        log(event="in", msg=msg)
        return msg

    def recv_response(rid):
        while True:
            m = recv()
            if m.get("id") == rid and "method" not in m:
                log(event="client_response", id=rid, result=m.get("result"), error=m.get("error")); return m

    secrets = [k for k in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "AGENTOS_LOCAL_TOKEN", "FAKE_SECRET") if k in os.environ]
    log(event="launch", args=args, cwd=os.getcwd(), pid=os.getpid(), env=sorted(os.environ), secrets=secrets)
    store = os.path.join(HERE, "store"); os.makedirs(store, exist_ok=True)
    MODELS = [{"modelId": "claude-sonnet-4-6", "name": "Claude Sonnet 4.6"}, {"modelId": "gem-x", "name": "Gem X"}]
    MODES = [{"id": "default", "name": "Default"}, {"id": "accept-edits", "name": "Accept edits"}, {"id": "plan", "name": "Plan"}]
    if MODE == "no_plan": MODES = MODES[:2]
    CONFIG = []
    if MODE in ("model_via_config", "config_keeps_old"):
        CONFIG = [{"id": "model", "name": "Model", "category": "model", "type": "select", "currentValue": "gem-x",
                   "options": [{"value": "claude-sonnet-4-6", "name": "S"}, {"value": "gem-x", "name": "G"}]}]
    if MODE == "auth_link":
        raw("Open the following link to authenticate the ACP server: https://example.invalid/auth\n")
        time.sleep(30)
    if MODE in ("noise", "oversize_noise"):
        raw("[agy] booting 1.5.2\n42\n[1, 2]\n")
    sid_counter = [0]
    SESSION = {"id": None}

    def do_prompt(mid, sid, text):
        log(event="prompt", prompt=text, sid=sid)
        reply = "echo:" + text
        t = max(1, len(reply) // 3)
        parts = [reply[i:i + t] for i in range(0, len(reply), t)]
        parts = [parts[0], parts[1], "".join(parts[2:])]
        if MODE == "exit_early": sys.exit(3)
        if MODE == "refusal": ok(mid, {"stopReason": "refusal"}); return
        if MODE == "empty": ok(mid, {"stopReason": "end_turn"}); return
        if MODE == "max_tokens": chunk(sid, "incomplete"); ok(mid, {"stopReason": "max_tokens"}); return
        if MODE == "missing_stop": ok(mid, {}); return
        if MODE == "cancelled_stop": chunk(sid, "x"); ok(mid, {"stopReason": "cancelled"}); return
        if MODE == "prompt_429": err(mid, -32603, "429 Too Many Requests RAW-SECRET"); return
        if MODE == "prompt_quota": err(mid, -32603, "internal", {"error": {"message": "RESOURCE_EXHAUSTED: quota RAW-SECRET"}}); return
        if MODE == "prompt_model": err(mid, -32603, "failed", {"details": "Model gem-x not found RAW-SECRET"}); return
        if MODE == "prompt_auth": err(mid, -32000, "Authentication required RAW-SECRET"); return
        if MODE == "prompt_other": err(mid, -32603, "kaboom RAW-SECRET", {"errorName": "APIError"}); return
        if MODE == "bad_id": ok(999, {"stopReason": "end_turn"}); return
        if MODE == "malformed_rpc": send({"hello": "world"}); return
        if MODE == "oversize_json": raw("{" + "x" * 1_200_000 + "\n"); return
        if MODE == "mode_switch":
            update(sid, {"sessionUpdate": "current_mode_update", "currentModeId": "accept-edits"}); time.sleep(0.2)
            chunk(sid, "after"); ok(mid, {"stopReason": "end_turn"}); return
        if MODE == "mode_same":
            update(sid, {"sessionUpdate": "current_mode_update", "currentModeId": "plan"})
        if MODE in ("hang", "hang_ack", "hang_ignore"):
            chunk(sid, parts[0]); log(event="streaming")
            if MODE == "hang_ignore":
                time.sleep(120)
            while True:
                m = recv()
                if m.get("method") == "session/cancel":
                    log(event="cancel", params=m["params"])
                    if MODE == "hang_ack":
                        time.sleep(0.4); ok(mid, {"stopReason": "cancelled"}); time.sleep(0.2); sys.exit(0)
        if MODE == "tools":
            chunk(sid, "pre", fragment=True)
            update(sid, {"sessionUpdate": "tool_call", "toolCallId": "t1", "title": "read file", "status": "pending",
                         "rawInput": {"command": "ls", "api_key": "sekret"}})
            update(sid, {"sessionUpdate": "tool_call_update", "toolCallId": "t1", "status": "in_progress"})
            update(sid, {"sessionUpdate": "tool_call_update", "toolCallId": "t1", "status": "completed", "rawOutput": "a.txt"})
            update(sid, {"sessionUpdate": "tool_call", "toolCallId": "t2", "title": "done already", "status": "completed"})
            update(sid, {"sessionUpdate": "tool_call", "title": "no id", "status": "pending"})
            update(sid, {"sessionUpdate": "tool_call_update", "status": "failed"})
            update(sid, {"sessionUpdate": "tool_call_update", "toolCallId": "t3", "status": "completed", "title": "never started"})
            update(sid, {"sessionUpdate": "tool_call", "toolCallId": "t4", "title": "left running", "status": "in_progress"})
            update(sid, {"sessionUpdate": "tool_call", "toolCallId": "t2", "title": "duplicate", "status": "completed"})
            chunk(sid, "done:" + text); ok(mid, {"stopReason": "end_turn"}); return
        if MODE == "tool_only":
            update(sid, {"sessionUpdate": "tool_call", "toolCallId": "t1", "title": "x", "status": "completed"})
            ok(mid, {"stopReason": "end_turn"}); return
        if MODE in ("perm_continue", "perm_respected"):
            update(sid, {"sessionUpdate": "tool_call", "toolCallId": "t1", "title": "write", "status": "pending"})
            send({"jsonrpc": "2.0", "id": "perm-1", "method": "session/request_permission",
                  "params": {"sessionId": sid, "toolCall": {"toolCallId": "t1"},
                             "options": [{"optionId": "allow", "name": "Allow", "kind": "allow_once"}]}})
            recv_response("perm-1")
            update(sid, {"sessionUpdate": "tool_call_update", "toolCallId": "t1",
                         "status": "completed" if MODE == "perm_continue" else "failed"})
            chunk(sid, "finished"); ok(mid, {"stopReason": "end_turn"}); return
        # ok / noise: interleaved updates, replies, requests and notifications
        chunk("other-session", "LEAK")
        chunk(sid, parts[0], fragment=True)
        chunk(sid, "plan thinking", kind="agent_thought_chunk")
        if MODE == "noise":
            raw("[agy] progress 50%\n")
        if MODE == "oversize_noise":
            raw(b"y" * 1_200_000 + b"\n")
        send({"jsonrpc": "2.0", "id": "perm-1", "method": "session/request_permission",
              "params": {"sessionId": sid, "toolCall": {"toolCallId": "tp"},
                         "options": [{"optionId": "allow", "name": "Allow", "kind": "allow_once"},
                                     {"optionId": "always", "name": "Always", "kind": "allow_always"}]}})
        recv_response("perm-1")
        send({"jsonrpc": "2.0", "id": 77, "method": "fs/read_text_file", "params": {"sessionId": sid, "path": "x"}})
        recv_response(77)
        send({"jsonrpc": "2.0", "id": "t-1", "method": "terminal/create", "params": {"sessionId": sid}})
        recv_response("t-1")
        send({"jsonrpc": "2.0", "method": "unknown/notification", "params": {}})
        chunk(sid, parts[1], fragment=True)
        update(sid, {"sessionUpdate": "usage_update", "used": 10, "size": 1000})
        update(sid, {"sessionUpdate": "config_option_update", "configOptions": [{"id": "model", "category": "model", "currentValue": "gem-x"}]})
        update(sid, {"sessionUpdate": "plan", "entries": []})
        chunk(sid, parts[2])
        if MODE == "exit_mid": sys.exit(4)
        res = {"stopReason": "end_turn"}
        if MODE != "nousage":
            res["usage"] = {"inputTokens": 7, "outputTokens": 5, "thoughtTokens": 1, "cachedReadTokens": 2, "totalTokens": 12}
        ok(mid, res)

    def reopen(mid, method, params):
        want = {"resume": "session/resume", "load": "session/load"}
        advertised = {want[c] for c in (("resume", "load") if CAPS == "both" else (CAPS,)) if c in want}
        if method not in advertised:
            err(mid, -32601, "Method not found"); return
        if MODE == "resume_notfound":
            err(mid, -32602, "Invalid params", {"sessionId": params["sessionId"]}); return
        if MODE == "resume_msg":
            err(mid, -32603, "Session not found: " + params["sessionId"]); return
        if MODE == "resume_auth":
            err(mid, -32000, "Authentication required"); return
        if MODE == "resume_boom":
            err(mid, -32603, "kaboom RAW-SECRET"); return
        if MODE == "resume_unsupported":
            err(mid, -32601, "Method not found"); return
        if not os.path.exists(os.path.join(store, params["sessionId"])):
            err(mid, -32602, "Invalid params", {"sessionId": params["sessionId"]}); return
        sid = params["sessionId"]
        chunk(sid, "REPLAY-TEXT", replay=True)
        update(sid, {"sessionUpdate": "current_mode_update", "currentModeId": "accept-edits"}, replay=True)
        chunk(sid, "HISTORY-TEXT")                                   # un-flagged transcript before our prompt
        update(sid, {"sessionUpdate": "current_mode_update", "currentModeId": "default"})  # before plan is selected: harmless
        res = {}
        if MODE == "resume_lists":
            res = {"modes": {"availableModes": MODES[:2]}}
        ok(mid, res)

    while True:
        msg = recv()
        m, mid, params = msg.get("method"), msg.get("id"), msg.get("params") or {}
        if m == "initialize":
            if MODE == "slow_init": time.sleep(2.5)
            if MODE == "init_err": err(mid, -32603, "boom"); continue
            if MODE == "old_protocol": ok(mid, {"protocolVersion": 0}); continue
            caps = {}
            if CAPS in ("load", "both"): caps["loadSession"] = True
            if CAPS in ("resume", "both"): caps["sessionCapabilities"] = {"resume": {}}
            ok(mid, {"protocolVersion": 1, "agentCapabilities": caps, "authMethods": []})
        elif m == "session/new":
            if MODE == "new_auth": err(mid, -32000, "Authentication required"); continue
            sid_counter[0] += 1
            sid = "sess-" + uuid.uuid4().hex[:8]
            open(os.path.join(store, sid), "w").write("1")
            r = {"sessionId": sid, "models": {"availableModels": MODELS, "currentModelId": "gem-x"},
                 "modes": {"availableModes": MODES, "currentModeId": "default"}, "configOptions": CONFIG}
            if MODE == "no_session_id": r.pop("sessionId")
            ok(mid, r)
        elif m in ("session/resume", "session/load"):
            reopen(mid, m, params)
        elif m == "session/set_mode":
            if MODE == "set_mode_err": err(mid, -32602, "invalid mode"); continue
            ok(mid, {})
        elif m == "session/set_model":
            if MODE == "set_model_err": err(mid, -32602, "invalid model"); continue
            if MODE in ("model_via_config", "config_keeps_old", "no_model_api"): err(mid, -32601, "Method not found"); continue
            ok(mid, {})
        elif m == "session/set_config_option":
            if MODE == "config_keeps_old": ok(mid, {"configOptions": [{"id": "model", "currentValue": "gem-x"}]}); continue
            ok(mid, {"configOptions": [{"id": "model", "currentValue": params["value"]}]})
        elif m == "session/prompt":
            do_prompt(mid, params["sessionId"], params["prompt"][0]["text"])
        elif mid is not None:
            err(mid, -32601, "Method not found")
''')


def pid_alive(pid):
    if os.name == "nt":
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True).stdout
        return str(pid) in out
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


class Rig:
    def __init__(self, tmp_path):
        self.dir = tmp_path / "fake"
        self.dir.mkdir()
        self.script = self.dir / "fake_agy.py"
        self.script.write_text(FAKE, encoding="utf-8")
        self.cwd_root = str(tmp_path / "cwd")

    def log(self, event=None):
        path = self.dir / "log.jsonl"
        rows = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []
        return [r for r in rows if event is None or r["event"] == event]

    def methods(self):
        return [r["msg"]["method"] for r in self.log("in") if "method" in r["msg"]]

    def reqs(self, method):
        return [r["msg"]["params"] for r in self.log("in") if r["msg"].get("method") == method]

    def prompts(self):
        return [r["prompt"] for r in self.log("prompt")]

    def driver(self, mode="ok", caps="resume", db=None, **kw):
        sessions = SessionRepository(db) if db is not None else None
        kw.setdefault("exit_grace_s", 0.3)
        kw.setdefault("interrupt_grace_s", 1.0)
        kw.setdefault("cancel_grace_s", 0.3)
        return AntigravityAcpDriver([sys.executable, str(self.script)], sessions=sessions, cwd_root=self.cwd_root,
                                    extra_env={"FAKE_MODE": mode, "FAKE_CAPS": caps}, **kw)


@pytest.fixture
def rig(tmp_path):
    return Rig(tmp_path)


@pytest.fixture
def db(tmp_path):
    database = Database(str(tmp_path / "driver.db"))
    init_db(database)
    yield database
    database.close()


def turn(**kw):
    base = StartTurn("t1", "t1", "ceo", "scope", "hi", "gem-x", ".", ToolPolicy("none", "deny"), timeout_s=20)
    return replace(base, **kw)


async def run_turn(driver, **kw):
    events = []

    async def emit(e):
        events.append(e)

    try:
        result = await asyncio.wait_for(collect_turn(driver, turn(**kw), emit), 40)
        return result, events, None
    except DriverTurnError as exc:
        return None, events, exc


def types(events):
    return [e.type for e in events]


def conv(**kw):
    return dict(scope_kind="conversation", scope_id="c1", **kw)


def seed(db, rig, sid="gone", last="prev", **kw):
    SessionRepository(db).put(**{**dict(
        scope_kind="conversation", scope_id="c1", engine="antigravity", runtime_key="default", protocol="acp-antigravity",
        namespace="antigravity:default", native_session_id=sid, generation=1, state="ready", model_id="gem-x",
        cwd=os.path.join(rig.cwd_root, "conversation"), last_turn_id=last), **kw})


# -- handshake, argv, events ----------------------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_exact_argv_handshake_and_protocol_parameters(rig):
    result, events, err = await run_turn(rig.driver())
    assert err is None and result.text == "echo:hi"
    launch = rig.log("launch")[0]
    assert launch["args"] == ["acp", "--stdio", "--sandbox", "--include-default-customizations=false"]
    assert "agentos-agy-acp-" in launch["cwd"] and not os.path.exists(launch["cwd"])  # empty scratch dir, removed afterwards
    assert rig.methods() == ["initialize", "session/new", "session/set_mode", "session/set_model", "session/prompt"]
    init = rig.reqs("initialize")[0]
    assert init["protocolVersion"] == 1 and init["clientInfo"]["name"] == "agentos"
    assert init["clientCapabilities"] == {"fs": {"readTextFile": False, "writeTextFile": False}, "terminal": False}
    new = rig.reqs("session/new")[0]
    assert new == {"cwd": launch["cwd"], "mcpServers": []}
    sid = result.session_id
    assert rig.reqs("session/set_mode")[0] == {"sessionId": sid, "modeId": "plan"}
    assert rig.reqs("session/set_model")[0] == {"sessionId": sid, "modelId": "gem-x"}
    assert rig.reqs("session/prompt")[0] == {"sessionId": sid, "prompt": [{"type": "text", "text": "hi"}]}


@pytest.mark.asyncio
async def test_event_order_single_terminal_deltas_equal_text_usage_once_and_ignored_noise(rig):
    result, events, err = await run_turn(rig.driver())
    assert err is None
    assert [e.seq for e in events] == list(range(1, len(events) + 1))
    assert types(events)[:2] == ["turn.started", "session.started"]
    assert events[-1].type == "turn.completed" and types(events).count("turn.completed") == 1
    deltas = "".join(e.data["delta"] for e in events if e.type == "content.delta" and e.data["stream_kind"] == "assistant_text")
    assert deltas == "echo:hi" == result.text and "LEAK" not in deltas
    assert result.reasoning == "plan thinking"
    turn_usage = [e for e in events if e.type == "usage" and e.data["scope"] == "turn"]
    assert len(turn_usage) == 1 and turn_usage[0].data["final"]
    assert turn_usage[0].data["tokens"] == {"input_tokens": 7, "output_tokens": 5, "reasoning_output_tokens": 1, "cached_input_tokens": 2}
    context = [e for e in events if e.type == "usage" and e.data["scope"] == "context"]
    assert len(context) == 1 and context[0].data["context_size"] == 1000 and not context[0].data["final"]
    assert result.tokens["input_tokens"] == 7 and result.model_id == "gem-x" and result.session_id
    assert all(e.engine == "antigravity" for e in events) and not any(e.type == "runtime.error" for e in events)
    final = [e for e in events if e.type == "item.completed" and e.data.get("phase") == "final"]
    assert len(final) == 1 and final[0].data["text"] == "echo:hi"
    assert sum(1 for e in events if e.type == "runtime.notice" and e.data["code"] == "permission_denied") == 1


@pytest.mark.asyncio
async def test_no_usage_reported_means_no_turn_usage_event(rig):
    result, events, err = await run_turn(rig.driver("nousage"))
    assert err is None and not any(e.type == "usage" and e.data["scope"] == "turn" for e in events)


# -- version, mode, model -------------------------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_unsupported_protocol_version_fails_before_any_session(rig):
    result, events, err = await run_turn(rig.driver("old_protocol"))
    assert err.failure.category == "unsupported_capability" and err.failure.acceptance == "not-submitted"
    assert rig.methods() == ["initialize"]
    assert types(events).count("turn.completed") == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("mode,category", [("no_plan", "unsupported_capability"), ("set_mode_err", "unsupported_capability"),
                                          ("set_model_err", "model_unavailable"), ("no_model_api", "model_unavailable"),
                                          ("config_keeps_old", "model_unavailable")])
async def test_plan_mode_and_model_are_required_and_the_prompt_is_never_sent(rig, mode, category):
    extra = {"model_id": "claude-sonnet-4-6"} if mode == "config_keeps_old" else {}  # the agent's current model is gem-x
    result, events, err = await run_turn(rig.driver(mode), **extra)
    assert err.failure.category == category and err.failure.acceptance == "not-submitted"
    assert "session/prompt" not in rig.methods()


@pytest.mark.asyncio
async def test_model_not_offered_is_refused_and_config_fallback_works(rig):
    result, events, err = await run_turn(rig.driver(), model_id="missing-model")
    assert err.failure.category == "model_unavailable" and "session/set_model" not in rig.methods()
    result, events, err = await run_turn(rig.driver("model_via_config"), turn_id="t2", run_id="t2")
    assert err is None and result.text == "echo:hi"
    assert rig.reqs("session/set_config_option")[0]["configId"] == "model" and rig.reqs("session/set_config_option")[0]["value"] == "gem-x"


@pytest.mark.asyncio
async def test_plan_mode_violation_aborts_but_a_mode_update_to_plan_is_fine(rig):
    result, events, err = await run_turn(rig.driver("mode_switch"))
    assert err.failure.category == "unsupported_capability" and "plan mode" in err.failure.message
    assert types(events).count("turn.completed") == 1 and events[-1].data["status"] == "failed"
    assert not any(e.type == "content.delta" and e.data["delta"] == "after" for e in events)
    result, events, err = await run_turn(rig.driver("mode_same"), turn_id="t2", run_id="t2")
    assert err is None and result.text == "echo:hi"


# -- permissions --------------------------------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_every_permission_request_is_cancelled_and_other_requests_rejected(rig):
    result, events, err = await run_turn(rig.driver())
    assert err is None
    answers = {r["id"]: r for r in rig.log("client_response")}
    assert answers["perm-1"]["result"] == {"outcome": {"outcome": "cancelled"}}
    assert answers[77]["error"]["code"] == -32601 and answers["t-1"]["error"]["code"] == -32601


@pytest.mark.asyncio
async def test_agent_that_acts_after_a_denial_fails_the_turn(rig):
    result, events, err = await run_turn(rig.driver("perm_continue"))
    assert err.failure.category == "runtime_error" and "denied" in err.failure.message
    assert types(events).count("turn.completed") == 1 and events[-1].data["status"] == "failed"
    result, events, err = await run_turn(rig.driver("perm_respected"), turn_id="t2", run_id="t2")
    assert err is None and result.text == "finished"
    assert any(e.type == "item.completed" and e.data.get("status") == "failed" for e in events)


# -- tools ---------------------------------------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_tool_calls_partial_missing_ids_already_completed_and_unfinished(rig):
    result, events, err = await run_turn(rig.driver("tools"), prompt="go")
    assert err is None and result.text == "done:go"
    started = {e.item_id: e for e in events if e.type == "item.started"}
    done = {e.item_id: e for e in events if e.type == "item.completed" and e.data["item_type"] == "tool"}
    assert set(started) == {"t1", "t2", "t1:tool3", "t3", "t4"}
    assert done["t1"].data["ok"] and done["t1"].data["output"] == "a.txt"
    assert done["t2"].data["ok"]                                         # arrived already completed
    unkeyed = [i for i in started if i.startswith("t1:tool")]
    assert len(unkeyed) == 1 and not done[unkeyed[0]].data["ok"] and done[unkeyed[0]].data["status"] == "failed"
    assert started["t3"].data.get("synthetic") and done["t3"].data["ok"]  # update without a start: start synthesised
    assert done["t4"].data["status"] == "unknown" and not done["t4"].data["ok"]  # unfinished at the end: settled, not lost
    assert "sekret" not in json.dumps(started["t1"].data) and "[redacted]" in started["t1"].data["input"]
    assert sum(1 for e in events if e.type == "item.completed" and e.item_id == "t2") == 1  # duplicate ignored
    commentary = next(e for e in events if e.type == "item.completed" and e.data.get("phase") == "commentary")
    assert commentary.data["text"] == "pre"                              # text before the tools is not the reply
    assert any(e.type == "item.updated" and e.item_id == "t1" for e in events)
    order = types(events)
    assert order.index("item.started") < order.index("turn.completed") and order[-1] == "turn.completed"


@pytest.mark.asyncio
async def test_tool_only_turn_is_a_failure_with_a_distinct_message(rig):
    result, events, err = await run_turn(rig.driver("tool_only"))
    assert err.failure.category == "runtime_error" and "tool-only" in err.failure.message


# -- stop reasons and errors ------------------------------------------------------------------------------------------------
@pytest.mark.asyncio
@pytest.mark.parametrize("mode,category", [("refusal", "request_refused"), ("max_tokens", "runtime_error"),
                                          ("empty", "runtime_error"), ("missing_stop", "protocol_error")])
async def test_stop_reasons(rig, mode, category):
    result, events, err = await run_turn(rig.driver(mode))
    assert err.failure.category == category and types(events).count("turn.completed") == 1
    assert events[-1].data["status"] == "failed"
    if mode == "max_tokens":
        assert "max_tokens" in err.failure.message and not any(e.type == "item.completed" and e.data.get("phase") for e in events)


@pytest.mark.asyncio
async def test_stop_reason_cancelled_settles_as_cancelled(rig):
    result, events, err = await run_turn(rig.driver("cancelled_stop"))
    assert err.failure.category == "cancelled"
    assert events[-1].data["status"] == "cancelled" and types(events).count("turn.completed") == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("mode,category,retryable", [("prompt_429", "rate_limit", True), ("prompt_quota", "quota", False),
                                                    ("prompt_model", "model_unavailable", False), ("prompt_auth", "auth", False),
                                                    ("prompt_other", "runtime_error", False), ("new_auth", "auth", False)])
async def test_nested_rpc_errors_are_classified_and_never_echoed(rig, mode, category, retryable):
    result, events, err = await run_turn(rig.driver(mode))
    assert err.failure.category == category and err.failure.retryable is retryable
    assert "RAW-SECRET" not in json.dumps([e.data for e in events]) and "RAW-SECRET" not in err.failure.message
    assert types(events).count("turn.completed") == 1 and types(events).count("runtime.error") == 1
    if mode.startswith("prompt"):
        assert err.failure.acceptance in ("submitted", "output-seen")
    else:
        assert err.failure.acceptance == "not-submitted"
    if mode == "prompt_other":
        assert err.failure.native_code == -32603


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["init_err", "bad_id", "malformed_rpc", "oversize_json", "exit_early", "no_session_id"])
async def test_protocol_faults_fail_once_and_the_process_is_gone(rig, mode):
    result, events, err = await run_turn(rig.driver(mode))
    assert err.failure.category in ("runtime_error", "protocol_error")
    assert types(events).count("turn.completed") == 1 and events[-1].data["status"] == "failed"
    assert not pid_alive(rig.log("launch")[0]["pid"])


@pytest.mark.asyncio
async def test_diagnostic_noise_on_stdout_is_tolerated_even_oversize_noise(rig):
    for n, mode in enumerate(("noise", "oversize_noise")):
        result, events, err = await run_turn(rig.driver(mode), turn_id=f"n{n}", run_id=f"n{n}")
        assert err is None and result.text == "echo:hi", mode


@pytest.mark.asyncio
async def test_auth_link_on_stdout_means_not_logged_in(rig):
    started = time.monotonic()
    result, events, err = await run_turn(rig.driver("auth_link"))
    assert err.failure.category == "auth" and err.failure.acceptance == "not-submitted"
    assert time.monotonic() - started < 15 and rig.methods() == []  # the agent never even read our initialize
    assert not pid_alive(rig.log("launch")[0]["pid"])


@pytest.mark.asyncio
async def test_mid_stream_exit_never_replays_and_keeps_partial_text_events(rig, db):
    driver = rig.driver("exit_mid", db=db)
    result, events, err = await run_turn(driver, **conv())
    assert err.failure.category == "protocol_error" and err.failure.acceptance == "output-seen"
    assert len(rig.log("prompt")) == 1 and len(rig.log("launch")) == 1
    assert any(e.type == "content.delta" for e in events) and types(events).count("turn.completed") == 1


# -- sessions ------------------------------------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_conversation_session_is_stored_then_resumed_with_incremental_prompt(rig, db):
    driver = rig.driver(db=db, resume_prompt="INCREMENTAL")
    result, events, err = await run_turn(driver, prompt="FULL1", **conv())
    assert err is None
    row = SessionRepository(db).get("conversation", "c1", "antigravity")
    assert row["protocol"] == "acp-antigravity" and row["native_session_id"] == result.session_id
    assert row["last_turn_id"] == "t1" and row["state"] == "ready" and row["generation"] == 1
    assert "agentos-agy" not in row["cwd"] and os.path.isdir(row["cwd"])
    started = next(e for e in events if e.type == "session.started")
    assert started.data["resumed"] is False and started.data["protocol"] == "acp-antigravity"

    result2, events2, err = await run_turn(driver, turn_id="t2", run_id="t2", prompt="FULL2", **conv())
    assert err is None and result2.session_id == result.session_id
    assert rig.prompts() == ["FULL1", "INCREMENTAL"]
    assert rig.methods().count("session/new") == 1 and rig.methods().count("session/resume") == 1
    assert "session/load" not in rig.methods()
    resumed = rig.reqs("session/resume")[0]
    assert resumed == {"sessionId": result.session_id, "cwd": row["cwd"], "mcpServers": []}
    assert next(e for e in events2 if e.type == "session.started").data["resumed"] is True
    # plan mode and the model are re-applied and verified on the resumed session too
    resume_idx = rig.methods().index("session/resume")
    assert rig.methods()[resume_idx:resume_idx + 3] == ["session/resume", "session/set_mode", "session/set_model"]
    assert "echo:INCREMENTAL" == result2.text
    assert SessionRepository(db).get("conversation", "c1", "antigravity")["last_turn_id"] == "t2"


@pytest.mark.asyncio
async def test_load_is_used_only_when_advertised_and_replay_is_suppressed(rig, db):
    driver = rig.driver(caps="load", db=db, resume_prompt="INC")
    r1, _, err = await run_turn(driver, prompt="F1", **conv())
    assert err is None
    r2, events, err = await run_turn(driver, turn_id="t2", run_id="t2", prompt="F2", **conv())
    assert err is None and r2.text == "echo:INC"
    assert "session/load" in rig.methods() and "session/resume" not in rig.methods()
    assert "REPLAY-TEXT" not in r2.text and "HISTORY-TEXT" not in r2.text
    assert not any("TEXT" in str(e.data.get("delta", "")) for e in events)
    assert events[-1].data["status"] == "succeeded"  # replayed + early mode updates did not trip the plan-mode check


@pytest.mark.asyncio
async def test_resume_unsupported_means_a_new_session_with_the_full_prompt_and_no_attempt(rig, db):
    driver = rig.driver(caps="none", db=db, resume_prompt="INC")
    r1, _, err = await run_turn(driver, prompt="F1", **conv())
    r2, events, err = await run_turn(driver, turn_id="t2", run_id="t2", prompt="F2", **conv())
    assert err is None and r2.session_id != r1.session_id
    assert "session/resume" not in rig.methods() and "session/load" not in rig.methods()  # never assumed
    assert rig.prompts() == ["F1", "F2"]
    assert any(e.type == "runtime.notice" and e.data["code"] == "session_resume_unsupported" for e in events)
    started = next(e for e in events if e.type == "session.started")
    assert started.data["rebuilt"] is True and started.data["resumed"] is False and started.data["generation"] == 2
    assert SessionRepository(db).get("conversation", "c1", "antigravity")["generation"] == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["resume_notfound", "resume_msg"])
async def test_missing_session_is_rebuilt_once_before_submission(rig, db, mode):
    seed(db, rig)
    driver = rig.driver(mode, db=db, resume_prompt="INC")
    result, events, err = await run_turn(driver, prompt="FULL", **conv())
    assert err is None and result.text == "echo:FULL"
    assert rig.methods().count("session/resume") == 1 and rig.methods().count("session/new") == 1
    assert len(rig.log("launch")) == 1                   # same process: no relaunch, no second prompt
    assert rig.prompts() == ["FULL"]
    assert any(e.type == "runtime.notice" and e.data["code"] == "session_rebuilt" for e in events)
    assert next(e for e in events if e.type == "session.started").data["rebuilt"] is True
    row = SessionRepository(db).get("conversation", "c1", "antigravity")
    assert row["generation"] == 2 and row["native_session_id"] == result.session_id and row["state"] == "ready"


@pytest.mark.asyncio
@pytest.mark.parametrize("mode,category", [("resume_auth", "auth"), ("resume_boom", "runtime_error")])
async def test_resume_auth_or_other_errors_are_not_missing_session(rig, db, mode, category):
    seed(db, rig)
    result, events, err = await run_turn(rig.driver(mode, db=db, resume_prompt="INC"), prompt="FULL", **conv())
    assert err.failure.category == category and err.failure.phase == "resume" and err.failure.acceptance == "not-submitted"
    assert "session/new" not in rig.methods() and "session/prompt" not in rig.methods()
    assert not any(e.type == "runtime.notice" and e.data["code"] == "session_rebuilt" for e in events)
    assert "RAW-SECRET" not in json.dumps([e.data for e in events])
    assert SessionRepository(db).get("conversation", "c1", "antigravity")["state"] == "ready"  # untouched


@pytest.mark.asyncio
async def test_method_not_found_on_resume_falls_back_to_a_new_session(rig, db):
    seed(db, rig)
    result, events, err = await run_turn(rig.driver("resume_unsupported", db=db), prompt="FULL", **conv())
    assert err is None and rig.prompts() == ["FULL"]
    assert any(e.data.get("code") == "session_resume_unsupported" for e in events if e.type == "runtime.notice")


@pytest.mark.asyncio
async def test_resume_result_that_drops_plan_mode_is_refused_before_the_prompt(rig, db):
    r1, _, err = await run_turn(rig.driver(db=db, resume_prompt="INC"), prompt="F1", **conv())
    result, events, err = await run_turn(rig.driver("resume_lists", db=db, resume_prompt="INC"), turn_id="t2", run_id="t2",
                                         prompt="F2", **conv())
    assert err.failure.category == "unsupported_capability" and len(rig.prompts()) == 1


@pytest.mark.asyncio
async def test_failure_after_submission_is_never_replayed_and_next_turn_still_resumes(rig, db):
    ok1, _, _ = await run_turn(rig.driver(db=db, resume_prompt="INC"), prompt="F1", **conv())
    bad, events, err = await run_turn(rig.driver("prompt_other", db=db, resume_prompt="INC"), turn_id="t2", run_id="t2",
                                      prompt="F2", **conv())
    assert err is not None and rig.prompts() == ["F1", "INC"]       # exactly one submission, never replayed
    assert rig.methods().count("session/prompt") == 2
    assert SessionRepository(db).get("conversation", "c1", "antigravity")["last_turn_id"] == "t1"


@pytest.mark.asyncio
async def test_session_with_a_gap_or_other_scope_is_not_resumed(rig, db):
    driver = rig.driver(db=db, resume_prompt="INC", resume_check=lambda row: False)
    await run_turn(driver, prompt="F1", **conv())
    r2, events, err = await run_turn(driver, turn_id="t2", run_id="t2", prompt="F2", **conv())
    assert err is None and rig.prompts() == ["F1", "F2"] and "session/resume" not in rig.methods()
    assert SessionRepository(db).get("conversation", "c1", "antigravity")["generation"] == 2


@pytest.mark.asyncio
async def test_ceo_and_mission_scopes_stay_isolated(rig, db):
    driver = rig.driver(db=db, resume_prompt="INC")
    for n, kind in enumerate(("ceo", "mission_attempt", "workflow")):
        result, events, err = await run_turn(driver, turn_id=f"t{n}", run_id=f"t{n}", scope_kind=kind, scope_id="s", prompt=f"P{n}")
        assert err is None and result.text == f"echo:P{n}"
    assert SessionRepository(db).db.scalar("SELECT count(*) FROM runtime_sessions") == 0
    assert rig.prompts() == ["P0", "P1", "P2"] and rig.methods().count("session/new") == 3
    assert "session/resume" not in rig.methods()
    assert all("agentos-agy-acp-" in r["cwd"] for r in rig.log("launch"))
    # the same scope id twice in a row still gets a brand-new session each call
    await run_turn(driver, turn_id="t9", run_id="t9", scope_kind="ceo", scope_id="s", prompt="again")
    assert rig.methods().count("session/new") == 4 and "session/resume" not in rig.methods()


# -- interrupt, timeout, cancellation ------------------------------------------------------------------------------------
async def _start_and_wait_streaming(rig, driver, **kw):
    events = []
    seen = asyncio.Event()

    async def emit(e):
        events.append(e)
        if e.type == "content.delta":
            seen.set()

    await driver.start_turn(turn(**kw), emit)
    await asyncio.wait_for(seen.wait(), 20)
    for _ in range(100):
        if rig.log("streaming"):
            break
        await asyncio.sleep(0.05)
    return events


@pytest.mark.asyncio
async def test_interrupt_sends_session_cancel_and_settles_once_when_completion_is_delayed(rig):
    driver = rig.driver("hang_ack")
    events = await _start_and_wait_streaming(rig, driver)
    started = time.monotonic()
    assert await driver.interrupt("t1") == "requested"
    assert time.monotonic() - started < 15
    assert await driver.interrupt("t1") == "already-terminal"
    assert types(events).count("turn.completed") == 1 and events[-1].data["status"] == "cancelled"
    assert not any(e.type == "runtime.error" for e in events)
    cancel = rig.log("cancel")
    assert len(cancel) == 1 and cancel[0]["params"]["sessionId"]
    notifications = [r["msg"] for r in rig.log("in") if r["msg"].get("method") == "session/cancel"]
    assert "id" not in notifications[0]                                   # a notification, not a request
    assert not pid_alive(rig.log("launch")[0]["pid"])
    assert await driver.close("ceo", "scope") == () and await driver.stop_all() == ()
    assert await driver.interrupt("nope") == "unknown"


@pytest.mark.asyncio
async def test_interrupt_falls_back_to_killing_the_tree_when_completion_never_comes(rig):
    driver = rig.driver("hang_ignore", interrupt_grace_s=0.5)
    events = await _start_and_wait_streaming(rig, driver)
    assert await driver.interrupt("t1") == "requested"
    assert types(events).count("turn.completed") == 1 and events[-1].data["status"] == "cancelled"
    assert not pid_alive(rig.log("launch")[0]["pid"])


@pytest.mark.asyncio
async def test_cancel_during_handshake_prevents_the_prompt(rig):
    driver = rig.driver("slow_init")
    events = []

    async def emit(e):
        events.append(e)

    await driver.start_turn(turn(), emit)
    for _ in range(100):
        if rig.log("launch"):
            break
        await asyncio.sleep(0.05)
    assert await driver.interrupt("t1") == "requested"
    assert types(events).count("turn.completed") == 1 and events[-1].data["status"] == "cancelled"
    await asyncio.sleep(0.5)
    assert "session/prompt" not in rig.methods() and not rig.log("prompt")
    assert not pid_alive(rig.log("launch")[0]["pid"])


@pytest.mark.asyncio
async def test_interrupt_before_launch_never_starts_a_process(rig):
    driver = rig.driver()
    events = []

    async def emit(e):
        events.append(e)

    await driver.start_turn(turn(), emit)
    assert await driver.interrupt("t1") == "requested"
    await asyncio.sleep(0.3)
    assert types(events).count("turn.completed") == 1 and events[-1].data["status"] == "cancelled"
    assert not rig.log("prompt")


@pytest.mark.asyncio
async def test_timeout_sends_cancel_then_kills_and_reports_timeout(rig):
    driver = rig.driver("hang_ignore")
    result, events, err = await run_turn(driver, timeout_s=3)
    assert err.failure.category == "timeout" and err.failure.retryable and err.failure.acceptance == "output-seen"
    assert types(events).count("turn.completed") == 1 and events[-1].data["status"] == "failed"
    assert not pid_alive(rig.log("launch")[0]["pid"])


@pytest.mark.asyncio
async def test_caller_cancellation_cleans_up_through_the_collector(rig):
    driver = rig.driver("hang_ack")
    seen = asyncio.Event()

    async def emit(e):
        if e.type == "content.delta":
            seen.set()

    task = asyncio.create_task(collect_turn(driver, turn(), emit))
    await asyncio.wait_for(seen.wait(), 20)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not pid_alive(rig.log("launch")[0]["pid"])


# -- validation, environment, routing -------------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_missing_executable_is_cli_missing(rig, monkeypatch):
    monkeypatch.setattr(ad, "default_cli_cmd", lambda: None)
    result, events, err = await run_turn(AntigravityAcpDriver(None, cwd_root=rig.cwd_root))
    assert err.failure.category == "cli_missing" and err.failure.acceptance == "not-submitted"
    result, events, err = await run_turn(AntigravityAcpDriver([str(rig.dir / "nope.exe")], cwd_root=rig.cwd_root),
                                         turn_id="t2", run_id="t2")
    assert err.failure.category == "cli_missing" and types(events).count("turn.completed") == 1


@pytest.mark.asyncio
async def test_validation_variant_mcp_system_model_prompt(rig):
    cases = [(dict(variant="high"), "unsupported_capability"), (dict(effort="high"), "unsupported_capability"),
             (dict(system="be nice"), "unsupported_capability"), (dict(model_id="x" * 201), "model_unavailable"), (dict(model_id=" gem-x"), "model_unavailable"),
             (dict(prompt="   "), "request_refused"), (dict(prompt="x" * (ad.MAX_PROMPT_CHARS + 1)), "request_refused"),
             (dict(tool_policy=ToolPolicy("scoped", "deny", mcp_servers={"s": {}})), "unsupported_capability")]
    for n, (kw, category) in enumerate(cases):
        result, events, err = await run_turn(rig.driver(), turn_id=f"v{n}", run_id=f"v{n}", **kw)
        assert err.failure.category == category and err.failure.acceptance == "not-submitted", kw
    assert not rig.log("launch")                                          # nothing was even launched


@pytest.mark.asyncio
async def test_busy_scope_and_invalid_arguments(rig):
    driver = rig.driver("hang_ack")
    events = []

    async def emit(e):
        events.append(e)

    await driver.start_turn(turn(), emit)
    with pytest.raises(DriverTurnError) as busy:
        await driver.start_turn(turn(turn_id="t2", run_id="t2"), emit)
    assert busy.value.failure.category == "busy"
    with pytest.raises(ValueError):
        await driver.start_turn(turn(turn_id="", run_id="x", scope_id="other"), emit)
    with pytest.raises(ValueError):
        await driver.start_turn(turn(turn_id="t3", run_id="t3", scope_id="other", timeout_s=0), emit)
    await driver.interrupt("t1")


@pytest.mark.asyncio
async def test_environment_allowlist_and_api_key_opt_in(rig, monkeypatch):
    for name in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "AGENTOS_LOCAL_TOKEN", "FAKE_SECRET"):
        monkeypatch.setenv(name, "must-not-leak")
    result, events, err = await run_turn(rig.driver())
    assert err is None
    launch = rig.log("launch")[0]
    assert launch["secrets"] == [] and "PATH" in {k.upper() for k in launch["env"]}
    result, events, err = await run_turn(rig.driver(allow_api_key_env=True), turn_id="t2", run_id="t2")
    assert err is None
    assert sorted(rig.log("launch")[1]["secrets"]) == ["GEMINI_API_KEY", "GOOGLE_API_KEY"]  # opt-in keys only, never the local token


def test_routing_default_off_and_flag_on(monkeypatch):
    runtime = MockRuntime()
    monkeypatch.delenv(engine_registry.ANTIGRAVITY_DRIVER_ENV, raising=False)
    assert isinstance(engine_registry.create_turn_driver("antigravity", runtime), ExecuteTaskDriver)
    monkeypatch.setenv(engine_registry.ANTIGRAVITY_DRIVER_ENV, "acp")
    monkeypatch.setattr(ad, "default_cli_cmd", lambda: None)
    assert isinstance(engine_registry.create_turn_driver("antigravity", runtime), AntigravityAcpDriver)
    assert isinstance(engine_registry.create_turn_driver("opencode", runtime), ExecuteTaskDriver)
    assert isinstance(engine_registry.create_turn_driver("codex", runtime), ExecuteTaskDriver)
    for value in ("off", "legacy", "0", "wrapper", ""):
        monkeypatch.setenv(engine_registry.ANTIGRAVITY_DRIVER_ENV, value)
        assert isinstance(engine_registry.create_turn_driver("antigravity", runtime), ExecuteTaskDriver), value
    monkeypatch.setenv(engine_registry.ANTIGRAVITY_DRIVER_ENV, "acp")
    assert isinstance(engine_registry.create_turn_driver("antigravity", runtime, enabled=None), AntigravityAcpDriver)


@pytest.mark.asyncio
async def test_health_and_models_delegate_to_the_legacy_probe(rig):
    health = await rig.driver().check_health()
    assert health.status == "ok" and health.models_count == 2 and health.default_model_status == "unverified"
    assert await rig.driver().list_available_models(free_only=True) == []
    models = await rig.driver().list_available_models(free_only=False)
    assert [m.id for m in models] == ["claude-sonnet-4-6", "gem-x"] and not any(m.is_free for m in models)


# -- end to end through the app ----------------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_conversation_runs_use_native_session_without_replaying_history(rig, make_app, monkeypatch, tmp_path):
    monkeypatch.setenv(engine_registry.ANTIGRAVITY_DRIVER_ENV, "acp")
    monkeypatch.setattr(ad, "default_cli_cmd", lambda: [sys.executable, str(rig.script)])
    monkeypatch.setattr(settings, "SQLITE_DB_PATH", str(tmp_path / "app" / "agentos.db"))
    monkeypatch.setattr(AntigravityAcpDriver, "_env", lambda self: {**os.environ, "FAKE_MODE": "ok", "FAKE_CAPS": "resume"})
    api = Api(make_app(runtime_factory=lambda engine: MockRuntime(), mode="real"))
    cid = await new_conv(api)
    first = await api("runs.start_turn", conversation_id=cid, engine="antigravity", model_id="gem-x", prompt="first")
    done1 = await wait_status(api, first["id"], "succeeded", timeout=40)
    second = await api("runs.start_turn", conversation_id=cid, engine="antigravity", model_id="gem-x", prompt="second")
    done2 = await wait_status(api, second["id"], "succeeded", timeout=40)
    assert done1["assistant_content"] == "echo:first" and done2["assistant_content"] == "echo:second"
    assert rig.methods().count("session/new") == 1 and rig.methods().count("session/resume") == 1
    assert "Previous conversation" not in rig.prompts()[1]
    row = SessionRepository(api.db).get("conversation", cid, "antigravity")
    assert row["last_turn_id"] == second["id"] and row["state"] == "ready" and row["protocol"] == "acp-antigravity"
    api.db.execute("UPDATE runtime_sessions SET last_turn_id='someone-else'")
    third = await api("runs.start_turn", conversation_id=cid, engine="antigravity", model_id="gem-x", prompt="third")
    done3 = await wait_status(api, third["id"], "succeeded", timeout=40)
    assert "Previous conversation (JSON)" in done3["assistant_content"] and rig.methods().count("session/new") == 2
    assert SessionRepository(api.db).get("conversation", cid, "antigravity")["generation"] == 2
