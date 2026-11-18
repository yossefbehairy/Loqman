"""OpenCodeAcpDriver against a FAKE `opencode acp` (ACP over stdio). No real opencode, network, login or model."""

import asyncio
import os
import sys
import textwrap
import time
from dataclasses import replace
from types import SimpleNamespace

import pytest

from backend.pyworker.db.core import Database
from backend.pyworker.db.migrate import init_db
from backend.pyworker.runtime import engine_registry
from backend.pyworker.runtime import opencode_driver as od
from backend.pyworker.runtime.antigravity_driver import scope_dir_name
from backend.pyworker.runtime.collector import collect_turn
from backend.pyworker.runtime.driver import DriverTurnError, StartTurn, ToolPolicy
from backend.pyworker.runtime.errors import RuntimeModelUnavailable
from backend.pyworker.runtime.execute_driver import ExecuteTaskDriver
from backend.pyworker.runtime.mock_runtime import MockRuntime
from backend.pyworker.runtime.opencode_driver import OpenCodeAcpDriver
from backend.pyworker.runtime.sessions import SessionRepository

from .test_antigravity_driver import Rig as _AgyRig, pid_alive

FAKE = textwrap.dedent(r'''
    import json, os, sys, time, uuid
    HERE = os.path.dirname(os.path.abspath(__file__))
    MODE = os.environ.get("FAKE_MODE", "ok")
    CAPS = os.environ.get("FAKE_CAPS", "resume")
    out = sys.stdout.buffer

    def log(**kw):
        with open(os.path.join(HERE, "log.jsonl"), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(kw) + "\n")

    def send(obj):
        out.write((json.dumps(obj) + "\n").encode()); out.flush()
    def ok(mid, result): send({"jsonrpc": "2.0", "id": mid, "result": result})
    def err(mid, code, message): send({"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}})
    def update(sid, upd): send({"jsonrpc": "2.0", "method": "session/update", "params": {"sessionId": sid, "update": upd}})
    def chunk(sid, text): update(sid, {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": text}})
    def recv():
        line = sys.stdin.buffer.readline()
        if not line: sys.exit(0)
        msg = json.loads(line); log(event="in", msg=msg); return msg
    def recv_response(rid):
        while True:
            m = recv()
            if m.get("id") == rid and "method" not in m:
                log(event="client_response", id=rid, result=m.get("result"), error=m.get("error")); return m

    secrets = [k for k in ("NVIDIA_API_KEY", "OPENCODE_API_KEY", "AGENTOS_LOCAL_TOKEN", "FAKE_SECRET", "XDG_CONFIG_HOME") if k in os.environ]
    log(event="launch", args=sys.argv[1:], cwd=os.getcwd(), pid=os.getpid(), env=sorted(os.environ), secrets=secrets)
    store = os.path.join(HERE, "store"); os.makedirs(store, exist_ok=True)
    MODELS = [{"modelId": "opencode/free-x", "name": "Free X"}, {"modelId": "opencode/paid-y", "name": "Paid Y"}]
    MODES = [{"id": "build", "name": "build"}, {"id": "plan", "name": "plan"}]
    if MODE != "hidden_agents":
        MODES += [{"id": "agentos-chat", "name": "chat"}, {"id": "agentos-worker", "name": "worker"}]
    CURRENT = {"mode": "build"}

    def do_prompt(mid, sid, text):
        log(event="prompt", prompt=text, sid=sid)
        if MODE in ("hang", "hang_ignore"):
            chunk(sid, "part"); log(event="streaming")
            if MODE == "hang_ignore": time.sleep(120)
            while True:
                m = recv()
                if m.get("method") == "session/cancel":
                    log(event="cancel", params=m["params"]); time.sleep(0.3); ok(mid, {"stopReason": "cancelled"}); time.sleep(0.2); sys.exit(0)
        if MODE == "perm_continue":
            update(sid, {"sessionUpdate": "tool_call", "toolCallId": "t1", "title": "bash", "status": "pending"})
            send({"jsonrpc": "2.0", "id": "perm-1", "method": "session/request_permission",
                  "params": {"sessionId": sid, "toolCall": {"toolCallId": "t1"},
                             "options": [{"optionId": "once", "name": "Allow", "kind": "allow_once"}]}})
            recv_response("perm-1")
            update(sid, {"sessionUpdate": "tool_call_update", "toolCallId": "t1", "status": "completed"})
            chunk(sid, "finished"); ok(mid, {"stopReason": "end_turn"}); return
        if MODE == "mode_switch":
            update(sid, {"sessionUpdate": "current_mode_update", "currentModeId": "build"}); time.sleep(0.2)
            chunk(sid, "after"); ok(mid, {"stopReason": "end_turn"}); return
        if MODE == "perm_ok":
            send({"jsonrpc": "2.0", "id": "perm-1", "method": "session/request_permission",
                  "params": {"sessionId": sid, "toolCall": {"toolCallId": "tp"}, "options": []}})
            recv_response("perm-1")
            send({"jsonrpc": "2.0", "id": 77, "method": "fs/read_text_file", "params": {"sessionId": sid, "path": "x"}})
            recv_response(77)
        reply = "echo:" + text
        chunk(sid, reply[:4]); chunk(sid, reply[4:])
        ok(mid, {"stopReason": "end_turn", "usage": {"inputTokens": 3, "outputTokens": 2}})

    while True:
        msg = recv()
        m, mid, params = msg.get("method"), msg.get("id"), msg.get("params") or {}
        if m == "initialize":
            caps = {"sessionCapabilities": {"resume": {}}} if CAPS == "resume" else {"loadSession": True}
            ok(mid, {"protocolVersion": 1, "agentCapabilities": caps, "authMethods": []})
        elif m == "session/new":
            sid = "ses_" + uuid.uuid4().hex[:8]
            open(os.path.join(store, sid), "w").write("1")
            ok(mid, {"sessionId": sid, "models": {"availableModels": MODELS, "currentModelId": "opencode/free-x"},
                     "modes": {"availableModes": MODES, "currentModeId": "build"}})
        elif m == "session/resume":
            if not os.path.exists(os.path.join(store, params["sessionId"])):
                err(mid, -32603, "Session not found: " + params["sessionId"]); continue
            ok(mid, {})
        elif m == "session/set_mode":
            if params["modeId"] not in [x["id"] for x in MODES]: err(mid, -32602, "invalid mode"); continue
            ok(mid, {})
        elif m == "session/set_model":
            ok(mid, {})
        elif m == "session/prompt":
            do_prompt(mid, params["sessionId"], params["prompt"][0]["text"])
        elif mid is not None:
            err(mid, -32601, "Method not found")
''')

FREE = {"free-x": "opencode"}


async def resolver(model_id):
    if model_id in FREE:
        return SimpleNamespace(provider_id=FREE[model_id], id=model_id)
    raise RuntimeModelUnavailable("Model is not confirmed free. No paid fallback used.")


class Rig(_AgyRig):
    def __init__(self, tmp_path):
        super().__init__(tmp_path)
        self.script.write_text(FAKE, encoding="utf-8")

    def driver(self, mode="ok", caps="resume", db=None, resolve=resolver, **kw):
        sessions = SessionRepository(db) if db is not None else None
        kw.setdefault("exit_grace_s", 0.3)
        kw.setdefault("interrupt_grace_s", 1.0)
        kw.setdefault("cancel_grace_s", 0.3)
        return OpenCodeAcpDriver([sys.executable, str(self.script)], resolve_model=resolve, sessions=sessions,
                                 cwd_root=self.cwd_root, extra_env={"FAKE_MODE": mode, "FAKE_CAPS": caps}, **kw)


@pytest.fixture
def rig(tmp_path):
    return Rig(tmp_path)


@pytest.fixture
def db(tmp_path):
    database = Database(str(tmp_path / "oc.db"))
    init_db(database)
    yield database
    database.close()


def turn(**kw):
    return replace(StartTurn("t1", "t1", "ceo", "scope", "hi", "free-x", ".", ToolPolicy("none", "deny"), timeout_s=20), **kw)


async def run_turn(driver, **kw):
    events = []

    async def emit(e):
        events.append(e)

    try:
        return await asyncio.wait_for(collect_turn(driver, turn(**kw), emit), 40), events, None
    except DriverTurnError as exc:
        return None, events, exc


def types(events):
    return [e.type for e in events]


def conv(**kw):
    return dict(scope_kind="conversation", scope_id="c1", **kw)


@pytest.mark.asyncio
async def test_handshake_prompt_in_jsonrpc_never_on_argv_and_locked_agent_and_free_model(rig):
    secret_prompt = "TOPSECRET-PROMPT " * 5
    result, events, err = await run_turn(rig.driver(), prompt=secret_prompt)
    assert err is None and result.text == "echo:" + secret_prompt and result.model_id == "free-x"
    launch = rig.log("launch")[0]
    assert launch["args"] == ["acp"] and "TOPSECRET" not in " ".join(launch["args"])  # S4: argv carries no prompt
    assert rig.methods() == ["initialize", "session/new", "session/set_mode", "session/set_model", "session/prompt"]
    sid = result.session_id
    assert rig.reqs("initialize")[0]["clientCapabilities"] == {"fs": {"readTextFile": False, "writeTextFile": False}, "terminal": False}
    assert rig.reqs("session/new")[0]["mcpServers"] == []
    assert rig.reqs("session/set_mode")[0] == {"sessionId": sid, "modeId": "agentos-worker"}  # never build/plan
    assert rig.reqs("session/set_model")[0] == {"sessionId": sid, "modelId": "opencode/free-x"}
    assert rig.reqs("session/prompt")[0]["prompt"] == [{"type": "text", "text": secret_prompt}]
    assert events[-1].type == "turn.completed" and types(events).count("turn.completed") == 1
    assert all(e.engine == "opencode" for e in events)
    assert events[-1].data["usage"]["tokens"] == {"input_tokens": 3, "output_tokens": 2}


@pytest.mark.asyncio
async def test_chat_agent_is_used_when_requested_and_other_agents_are_refused_before_launch(rig):
    result, events, err = await run_turn(rig.driver(), agent_id="agentos-chat")
    assert err is None and rig.reqs("session/set_mode")[0]["modeId"] == "agentos-chat"
    before = len(rig.log("launch"))
    for bad in ("build", "plan", "supervisor"):
        result, events, err = await run_turn(rig.driver(), turn_id=bad, run_id=bad, agent_id=bad)
        assert err.failure.category == "model_unavailable" and err.failure.acceptance == "not-submitted"
    assert len(rig.log("launch")) == before


@pytest.mark.asyncio
async def test_locked_agent_not_offered_fails_closed_and_the_prompt_is_never_sent(rig):
    result, events, err = await run_turn(rig.driver("hidden_agents"))
    assert err.failure.category == "unsupported_capability" and err.failure.acceptance == "not-submitted"
    assert "opencode acp" in err.failure.message and "agy" not in err.failure.message
    assert "session/prompt" not in rig.methods() and "session/set_mode" not in rig.methods()
    assert types(events).count("turn.completed") == 1 and events[-1].data["status"] == "failed"


@pytest.mark.asyncio
async def test_free_model_rule_is_verified_before_launch_and_model_must_be_offered(rig):
    result, events, err = await run_turn(rig.driver(), model_id="paid-y")
    assert err.failure.category == "model_unavailable" and rig.log("launch") == []
    result, events, err = await run_turn(rig.driver(resolve=None))  # no resolver: nothing proves free-only
    assert err.failure.category == "unsupported_capability" and rig.log("launch") == []

    FREE["ghost"] = "opencode"
    try:
        result, events, err = await run_turn(rig.driver(), model_id="ghost")  # free but not offered by the agent
    finally:
        FREE.pop("ghost")
    assert err.failure.category == "model_unavailable" and "session/set_model" not in rig.methods()
    assert "session/prompt" not in rig.methods()


@pytest.mark.asyncio
async def test_validation_variant_mcp_system_empty_prompt_and_long_prompt(rig):
    d = rig.driver()
    for kw in (dict(variant="high"), dict(effort="high"), dict(system="be nice"), dict(prompt="  "), dict(prompt="x" * 200_001)):
        result, events, err = await run_turn(d, turn_id="v", run_id="v", **kw)
        assert err is not None and err.failure.acceptance == "not-submitted"
    assert rig.log("launch") == []


@pytest.mark.asyncio
async def test_permission_requests_are_cancelled_other_requests_rejected_and_acting_after_denial_fails(rig):
    result, events, err = await run_turn(rig.driver("perm_ok"))
    assert err is None
    answers = {r["id"]: r for r in rig.log("client_response")}
    assert answers["perm-1"]["result"] == {"outcome": {"outcome": "cancelled"}}
    assert answers[77]["error"]["code"] == -32601
    assert any(e.type == "runtime.notice" and e.data["code"] == "permission_denied" for e in events)
    result, events, err = await run_turn(rig.driver("perm_continue"), turn_id="t2", run_id="t2")
    assert err.failure.category == "runtime_error" and "denied" in err.failure.message
    assert types(events).count("turn.completed") == 1 and events[-1].data["status"] == "failed"


@pytest.mark.asyncio
async def test_leaving_the_locked_agent_aborts_the_turn(rig):
    result, events, err = await run_turn(rig.driver("mode_switch"))
    assert err.failure.category == "unsupported_capability"
    assert types(events).count("turn.completed") == 1 and events[-1].data["status"] == "failed"
    assert not any(e.type == "content.delta" and e.data["delta"] == "after" for e in events)


@pytest.mark.asyncio
async def test_resume_is_probed_session_resume_only_and_incremental_prompt(rig, db):
    driver = rig.driver(db=db, resume_prompt="INCREMENTAL")
    result, events, err = await run_turn(driver, prompt="FULL1", **conv())
    assert err is None
    row = SessionRepository(db).get("conversation", "c1", "opencode")
    assert row["protocol"] == "acp-resume" and row["namespace"] == "opencode:default" and row["state"] == "ready"
    assert row["native_session_id"] == result.session_id and os.path.isdir(row["cwd"])
    result2, events2, err = await run_turn(driver, turn_id="t2", run_id="t2", prompt="FULL2", **conv())
    assert err is None and result2.session_id == result.session_id and rig.prompts() == ["FULL1", "INCREMENTAL"]
    assert rig.methods().count("session/new") == 1 and rig.methods().count("session/resume") == 1
    assert "session/load" not in rig.methods()
    idx = rig.methods().index("session/resume")  # the locked agent and the model are re-applied on the resumed session
    assert rig.methods()[idx:idx + 3] == ["session/resume", "session/set_mode", "session/set_model"]
    assert rig.reqs("session/set_mode")[-1]["modeId"] == "agentos-worker"


@pytest.mark.asyncio
async def test_load_only_agent_gets_a_new_session_with_full_prompt_and_never_session_load(rig, db):
    driver = rig.driver(caps="load", db=db, resume_prompt="INCREMENTAL")
    await run_turn(driver, prompt="FULL1", **conv())
    result, events2, err = await run_turn(driver, turn_id="t2", run_id="t2", prompt="FULL2", **conv())
    assert err is None and rig.prompts() == ["FULL1", "FULL2"] and "session/load" not in rig.methods()
    assert any(e.type == "runtime.notice" and e.data["code"] == "session_resume_unsupported" for e in events2)


@pytest.mark.asyncio
async def test_missing_session_is_rebuilt_once_before_submission(rig, db):
    SessionRepository(db).put(scope_kind="conversation", scope_id="c1", engine="opencode", runtime_key="default",
                              protocol="acp-resume", namespace="opencode:default", native_session_id="ses_gone", generation=1,
                              state="ready", model_id="free-x", last_turn_id="prev",
                              cwd=os.path.join(rig.cwd_root, "conversation", scope_dir_name("c1")))
    driver = rig.driver(db=db, resume_prompt="INCREMENTAL")
    result, events, err = await run_turn(driver, prompt="FULL", **conv())
    assert err is None and rig.prompts() == ["FULL"]
    assert rig.methods().count("session/new") == 1 and result.session_id != "ses_gone"


async def _start(rig, driver):
    events = []

    async def emit(e):
        events.append(e)

    await driver.start_turn(turn(), emit)
    for _ in range(300):
        if rig.log("streaming"):
            break
        await asyncio.sleep(0.05)
    assert rig.log("streaming")
    return events


@pytest.mark.asyncio
async def test_interrupt_sends_session_cancel_single_terminal_and_process_gone(rig):
    driver = rig.driver("hang")
    events = await _start(rig, driver)
    assert await driver.interrupt("t1") == "requested"
    assert await driver.interrupt("t1") == "already-terminal"
    assert types(events).count("turn.completed") == 1 and events[-1].data["status"] == "cancelled"
    assert not any(e.type == "runtime.error" for e in events)
    assert len(rig.log("cancel")) == 1 and not pid_alive(rig.log("launch")[0]["pid"])


@pytest.mark.asyncio
async def test_interrupt_kills_the_tree_when_cancel_is_ignored(rig):
    driver = rig.driver("hang_ignore", interrupt_grace_s=0.5)
    events = await _start(rig, driver)
    started = time.monotonic()
    assert await driver.interrupt("t1") == "requested"
    assert time.monotonic() - started < 15
    assert types(events).count("turn.completed") == 1 and events[-1].data["status"] == "cancelled"
    assert not pid_alive(rig.log("launch")[0]["pid"])


@pytest.mark.asyncio
async def test_environment_allowlist_and_key_opt_in(rig, monkeypatch):
    for name in ("NVIDIA_API_KEY", "OPENCODE_API_KEY", "AGENTOS_LOCAL_TOKEN", "FAKE_SECRET"):
        monkeypatch.setenv(name, "must-not-leak")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(rig.dir))
    result, events, err = await run_turn(rig.driver())
    assert err is None
    launch = rig.log("launch")[0]
    assert launch["secrets"] == ["XDG_CONFIG_HOME"] and "PATH" in {k.upper() for k in launch["env"]}  # a location, not a secret
    result, events, err = await run_turn(rig.driver(allow_api_key_env=True), turn_id="t2", run_id="t2")
    assert err is None
    assert sorted(rig.log("launch")[1]["secrets"]) == ["NVIDIA_API_KEY", "OPENCODE_API_KEY", "XDG_CONFIG_HOME"]
    assert "AGENTOS_LOCAL_TOKEN" not in rig.log("launch")[1]["secrets"]


@pytest.mark.asyncio
async def test_missing_executable_is_cli_missing(rig, monkeypatch):
    monkeypatch.setattr(od, "default_cli_cmd", lambda: None)
    driver = OpenCodeAcpDriver(None, resolve_model=resolver)
    result, events, err = await run_turn(driver)
    assert err.failure.category == "cli_missing" and err.failure.acceptance == "not-submitted", err.failure


def test_routing_default_off_and_flag_on(monkeypatch):
    runtime = MockRuntime()
    monkeypatch.delenv(engine_registry.OPENCODE_DRIVER_ENV, raising=False)
    assert isinstance(engine_registry.create_turn_driver("opencode", runtime), ExecuteTaskDriver)
    for value in ("off", "legacy", "0", "wrapper", "", "http"):
        monkeypatch.setenv(engine_registry.OPENCODE_DRIVER_ENV, value)
        assert isinstance(engine_registry.create_turn_driver("opencode", runtime), ExecuteTaskDriver), value
    monkeypatch.setenv(engine_registry.OPENCODE_DRIVER_ENV, "acp")
    monkeypatch.setattr(od, "default_cli_cmd", lambda: None)
    assert isinstance(engine_registry.create_turn_driver("opencode", runtime), OpenCodeAcpDriver)
    assert isinstance(engine_registry.create_turn_driver("claude-code", runtime, enabled=False), ExecuteTaskDriver)
    assert isinstance(engine_registry.create_turn_driver("antigravity", runtime), ExecuteTaskDriver)  # other flags unaffected
