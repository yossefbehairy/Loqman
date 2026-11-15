"""ClaudeStreamDriver against a FAKE stream-json claude CLI. No real `claude`, network, login or model is used."""

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
from backend.pyworker.runtime import claude_driver as cd
from backend.pyworker.runtime import engine_registry
from backend.pyworker.runtime.claude_driver import ClaudeStreamDriver, claude_child_env, conversation_resume_check
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
    args = sys.argv[1:]
    FLAGS = ("--print --input-format --output-format --verbose --include-partial-messages --tools --strict-mcp-config "
             "--model --setting-sources --disable-slash-commands --no-session-persistence --resume --session-id "
             "--mcp-config --allowedTools --permission-prompts").split()
    if "--help" in args:
        flags = [f for f in FLAGS if not (MODE == "old_help" and f == "--setting-sources")
                 and not (MODE == "no_prompts" and f == "--permission-prompts")]
        print("Usage: claude [options]\n" + "\n".join("  " + f + "   description" for f in flags)); sys.exit(0)

    def log(**kw):
        with open(os.path.join(HERE, "log.jsonl"), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(kw) + "\n")

    def emit(o):
        sys.stdout.write(json.dumps(o) + "\n"); sys.stdout.flush()

    def opt(name):
        return args[args.index(name) + 1] if name in args else None

    secrets = [k for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "AGENTOS_LOCAL_TOKEN", "FAKE_SECRET") if k in os.environ]
    mcp = None
    if opt("--mcp-config"):
        with open(opt("--mcp-config"), encoding="utf-8") as fh:
            mcp = json.load(fh)
    log(event="launch", args=args, cwd=os.getcwd(), pid=os.getpid(), env=sorted(os.environ), secrets=secrets, mcp=mcp)
    store = os.path.join(HERE, "store"); os.makedirs(store, exist_ok=True)
    resume = opt("--resume")
    sid = resume or opt("--session-id") or str(uuid.uuid4())
    if resume and not os.path.exists(os.path.join(store, resume)):
        if MODE == "resume_result_error":
            emit({"type": "result", "subtype": "error_during_execution", "is_error": True,
                  "result": "No conversation found with session ID: " + resume}); sys.exit(0)
        if MODE == "refuse_after_read":
            frame = json.loads(sys.stdin.readline())
            log(event="prompt", prompt=frame["message"]["content"], frame_keys=sorted(frame))
            sys.stderr.write("No conversation found with session ID: " + resume); sys.exit(1)
        if MODE == "resume_auth":
            sys.stderr.write("Not logged in - run /login"); sys.exit(1)
        sys.stderr.write("No conversation found with session ID: " + resume); sys.exit(1)
    line = sys.stdin.readline()
    if not line:
        sys.exit(0)
    frame = json.loads(line)
    prompt = frame["message"]["content"]
    log(event="prompt", prompt=prompt, frame_keys=sorted(frame))
    if "--no-session-persistence" not in args:
        open(os.path.join(store, sid), "w").close()
    model = opt("--model")
    emit({"type": "system", "subtype": "init", "session_id": sid, "model": model})
    if MODE == "no_result":
        sys.exit(0)

    def text_message(mid, text, index=0):
        emit({"type": "stream_event", "event": {"type": "message_start", "message": {"id": mid}}})
        emit({"type": "stream_event", "event": {"type": "content_block_start", "index": index, "content_block": {"type": "text"}}})
        third = max(1, len(text) // 3)
        for i in range(0, len(text), third):
            emit({"type": "stream_event", "event": {"type": "content_block_delta", "index": index,
                                                    "delta": {"type": "text_delta", "text": text[i:i + third]}}})
            if MODE == "hang" and i == 0:
                log(event="streaming"); time.sleep(120)
        emit({"type": "stream_event", "event": {"type": "content_block_stop", "index": index}})
        emit({"type": "assistant", "message": {"id": mid, "content": [{"type": "text", "text": text}]}})

    def result(text, **extra):
        emit({"type": "result", "subtype": "success", "is_error": False, "result": text, "session_id": sid,
              "stop_reason": "end_turn", "total_cost_usd": 0.01,
              "usage": {"input_tokens": 3, "output_tokens": 5, "cache_read_input_tokens": 2},
              "modelUsage": {model + "-effective": {}}, **extra})

    if MODE == "api_error":
        emit({"type": "assistant", "is_api_error_message": True, "message": {"content": [{"type": "text", "text": "Not logged in - Please run /login"}]}})
        emit({"type": "result", "subtype": "success", "is_error": True, "result": "Not logged in - Please run /login", "session_id": sid})
    elif MODE == "tools":
        text_message("msg_1", "Let me check.")
        emit({"type": "assistant", "message": {"id": "msg_1", "content": [{"type": "tool_use", "id": "toolu_1", "name": "Read",
              "input": {"path": "a.txt", "api_key": "sk-secret-value"}}]}})
        emit({"type": "tool_progress", "tool_use_id": "toolu_1"})
        emit({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "toolu_1", "content": "file body", "is_error": False}]}})
        emit({"type": "assistant", "message": {"id": "msg_x", "content": [{"type": "tool_use", "id": "toolu_2", "name": "Bash", "input": {}}]}})
        emit({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "toolu_2", "content": "denied", "is_error": True}]}})
        emit({"type": "stream_event", "parent_tool_use_id": "toolu_1", "event": {"type": "content_block_delta", "index": 0,
              "delta": {"type": "text_delta", "text": "NESTED-IGNORED"}}})
        text_message("msg_2", "done:" + prompt)
        result("done:" + prompt)
    elif MODE == "ctl":
        emit({"type": "control_request", "request_id": 7, "request": {"subtype": "hook_callback"}})
        emit({"type": "control_request", "request_id": "m1", "request": {"subtype": "mcp_message"}})
        emit({"type": "control_request", "request_id": 9, "request": {"subtype": "can_use_tool", "tool_name": "Bash", "input": {}}})
        for _ in range(3):
            log(event="control_response", answer=json.loads(sys.stdin.readline()))
        text_message("msg_1", "ctl-ok")
        result("ctl-ok")
    elif MODE == "flood":
        for i in range(400):
            emit({"type": "stream_event", "event": {"type": "message_start", "message": {"id": "m%d" % i}}})
            time.sleep(0.002)
        result("late")
    elif MODE == "perm":
        emit({"type": "control_request", "request_id": "r1", "request": {"subtype": "can_use_tool", "tool_name": "Bash", "input": {}}})
        answer = json.loads(sys.stdin.readline())
        log(event="control_response", answer=answer)
        behavior = answer["response"]["response"]["behavior"]
        text_message("msg_1", "perm:" + behavior)
        result("perm:" + behavior)
    else:
        reply = "echo:" + prompt
        text_message("msg_1", reply)
        result(reply)
    if MODE == "ignore_close":
        time.sleep(120)
    sys.stdin.read()
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
    def __init__(self, tmp_path, monkeypatch):
        self.dir = tmp_path / "fake"
        self.dir.mkdir()
        self.script = self.dir / "fake_claude.py"
        self.script.write_text(FAKE, encoding="utf-8")
        self.cwd_root = str(tmp_path / "cwd")
        self.monkeypatch = monkeypatch

    def log(self, event=None):
        path = self.dir / "log.jsonl"
        rows = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []
        return [r for r in rows if event is None or r["event"] == event]

    def driver(self, mode="ok", db=None, **kw):
        sessions = SessionRepository(db) if db is not None else None
        kw.setdefault("exit_grace_s", 0.3)
        kw.setdefault("interrupt_grace_s", 1.0)
        kw.setdefault("resume_gate_s", 0.05)
        return ClaudeStreamDriver([sys.executable, str(self.script)], sessions=sessions, cwd_root=self.cwd_root,
                                  extra_env={"FAKE_MODE": mode}, cache_flags=False, **kw)


@pytest.fixture
def rig(tmp_path, monkeypatch):
    return Rig(tmp_path, monkeypatch)


@pytest.fixture
def db(tmp_path):
    database = Database(str(tmp_path / "driver.db"))
    init_db(database)
    yield database
    database.close()


def turn(**kw):
    base = StartTurn("t1", "t1", "ceo", "scope", "hi", "sonnet", ".", ToolPolicy("none", "deny"), timeout_s=20)
    return replace(base, **kw)


async def run_turn(driver, **kw):
    events = []

    async def emit(e):
        events.append(e)

    try:
        result = await asyncio.wait_for(collect_turn(driver, turn(**kw), emit), 30)
        return result, events, None
    except DriverTurnError as exc:
        return None, events, exc


def types(events):
    return [e.type for e in events]


@pytest.mark.asyncio
async def test_event_order_single_terminal_deltas_equal_collected_text_and_usage_once(rig):
    result, events, err = await run_turn(rig.driver())
    assert err is None
    assert [e.seq for e in events] == list(range(1, len(events) + 1))
    assert types(events)[:2] == ["turn.started", "session.started"]
    assert events[-1].type == "turn.completed" and types(events).count("turn.completed") == 1
    deltas = "".join(e.data["delta"] for e in events if e.type == "content.delta")
    assert deltas == "echo:hi" == result.text
    assert sum(e.type == "usage" for e in events) == 1
    usage = next(e for e in events if e.type == "usage")
    assert usage.data["final"] and usage.data["tokens"] == {"input_tokens": 3, "output_tokens": 5,
                                                             "cache_read_input_tokens": 2, "reported_cost_usd": 0.01}
    assert result.tokens["input_tokens"] == 3 and result.model_id == "sonnet-effective" and result.session_id
    assert all(e.engine == "claude-code" for e in events)
    assert not any(e.type == "runtime.error" for e in events)


@pytest.mark.asyncio
async def test_tool_events_and_commentary_excluded_from_final_text(rig):
    result, events, err = await run_turn(rig.driver("tools"), prompt="go")
    assert err is None and result.text == "done:go"  # commentary before the tool call and nested deltas are not the reply
    started = [e for e in events if e.type == "item.started"]
    done = [e for e in events if e.type == "item.completed" and e.data["item_type"] == "tool"]
    assert [e.item_id for e in started] == ["toolu_1", "toolu_2"]
    assert [(e.item_id, e.data["ok"]) for e in done] == [("toolu_1", True), ("toolu_2", False)]
    assert "sk-secret-value" not in json.dumps([e.data for e in started]) and "[redacted]" in started[0].data["input"]
    assert any(e.type == "item.updated" for e in events)
    assert "NESTED-IGNORED" not in "".join(e.data.get("delta", "") for e in events)
    assert next(e for e in events if e.type == "item.completed" and e.data.get("text") == "Let me check.").data["phase"] == "commentary"
    assert types(events).index("item.started") < types(events).index("turn.completed")


@pytest.mark.asyncio
async def test_exact_argv_is_tool_less_and_isolated_for_ceo_scope(rig):
    result, events, err = await run_turn(rig.driver())
    assert err is None
    launch = rig.log("launch")[0]
    assert launch["args"] == ["-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
                              "--include-partial-messages", "--model", "sonnet", "--tools", "", "--strict-mcp-config",
                              "--setting-sources", "project", "--disable-slash-commands", "--permission-prompts", "none",
                              "--no-session-persistence"]
    assert "agentos-claude-" in launch["cwd"]
    assert rig.log("prompt")[0]["prompt"] == "hi" and rig.log("prompt")[0]["frame_keys"] == ["message", "type"]
    assert not os.path.exists(launch["cwd"])  # scratch removed with the process


@pytest.mark.asyncio
async def test_scoped_mcp_policy_is_unchanged_and_config_is_not_left_behind(rig):
    servers = {"agentos_workspace": {"command": "x", "args": ["--serve"], "env": {"AGENTOS_MCP_TOKEN": "t0k"}}}
    result, events, err = await run_turn(rig.driver(), tool_policy=ToolPolicy("scoped", "deny", mcp_servers=servers))
    assert err is None
    launch = rig.log("launch")[0]
    a = launch["args"]
    assert a[a.index("--tools") + 1] == "ToolSearch" and "--strict-mcp-config" in a
    assert a[a.index("--allowedTools") + 1] == "mcp__agentos_workspace__*" and "--disable-slash-commands" in a
    assert a.count("--setting-sources") == 1 and a[a.index("--setting-sources") + 1] == "project"
    assert launch["mcp"] == {"mcpServers": servers}
    assert not os.path.exists(a[a.index("--mcp-config") + 1])
    assert not any(k in launch["secrets"] for k in ("AGENTOS_MCP_TOKEN",)) and "AGENTOS_MCP_TOKEN" not in launch["env"]


@pytest.mark.asyncio
async def test_conversation_session_is_stored_and_resumed_on_second_turn(rig, db):
    first, ev1, err = await run_turn(rig.driver(db=db), turn_id="r1", run_id="r1", scope_kind="conversation",
                                     scope_id="c1", prompt="FULL-1")
    assert err is None
    row = SessionRepository(db).get("conversation", "c1", "claude-code")
    assert row["state"] == "ready" and row["protocol"] == "claude-stream-json" and row["last_turn_id"] == "r1"
    assert row["native_session_id"] == first.session_id and row["generation"] == 1 and row["cwd"].endswith("conversation")
    assert next(e for e in ev1 if e.type == "session.started").data["resumed"] is False
    a1 = rig.log("launch")[0]["args"]
    assert a1[a1.index("--session-id") + 1] == first.session_id and "--resume" not in a1 and "--no-session-persistence" not in a1
    second, ev2, err = await run_turn(rig.driver(db=db, resume_prompt="INCREMENTAL-2"), turn_id="r2", run_id="r2",
                                      scope_kind="conversation", scope_id="c1", prompt="FULL-2")
    assert err is None and second.text == "echo:INCREMENTAL-2"  # no replayed history on resume
    a2 = rig.log("launch")[1]["args"]
    assert a2[a2.index("--resume") + 1] == first.session_id and "--session-id" not in a2
    started = next(e for e in ev2 if e.type == "session.started").data
    assert started["resumed"] is True and started["rebuilt"] is False and started["session_id"] == first.session_id
    row = SessionRepository(db).get("conversation", "c1", "claude-code")
    assert row["last_turn_id"] == "r2" and row["state"] == "ready"
    assert rig.log("launch")[0]["cwd"] == rig.log("launch")[1]["cwd"]  # same project dir, or --resume could not find it


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["ok", "resume_result_error"])
async def test_unknown_session_before_prompt_marks_invalid_and_rebuilds_once(rig, db, mode):
    SessionRepository(db).put(scope_kind="conversation", scope_id="c1", engine="claude-code", runtime_key="default",
                              protocol="claude-stream-json", namespace="claude:default", native_session_id="gone",
                              generation=1, state="ready", model_id="sonnet", cwd=os.path.join(rig.cwd_root, "conversation"),
                              last_turn_id="prev")
    result, events, err = await run_turn(rig.driver(mode, db=db, resume_prompt="INCREMENTAL", resume_gate_s=10.0), turn_id="r2", run_id="r2",
                                         scope_kind="conversation", scope_id="c1", prompt="FULL-WITH-HISTORY")
    assert err is None and result.text == "echo:FULL-WITH-HISTORY"  # explicit rebuild from the seeded transcript
    launches = rig.log("launch")
    assert len(launches) == 2 and launches[0]["args"][launches[0]["args"].index("--resume") + 1] == "gone"
    assert "--session-id" in launches[1]["args"] and len(rig.log("prompt")) == 1  # exactly one prompt ever submitted
    notice = next(e for e in events if e.type == "runtime.notice")
    assert notice.data["code"] == "session_rebuilt" and types(events).count("turn.completed") == 1
    started = next(e for e in events if e.type == "session.started").data
    assert started["rebuilt"] is True and started["resumed"] is False and started["generation"] == 2
    row = SessionRepository(db).get("conversation", "c1", "claude-code")
    assert row["native_session_id"] == result.session_id != "gone" and row["state"] == "ready" and row["generation"] == 2


@pytest.mark.asyncio
async def test_resume_with_non_missing_session_error_is_not_rebuilt(rig, db):
    SessionRepository(db).put(scope_kind="conversation", scope_id="c1", engine="claude-code", runtime_key="default",
                              protocol="claude-stream-json", namespace="claude:default", native_session_id="gone",
                              generation=1, state="ready", model_id="sonnet", cwd=os.path.join(rig.cwd_root, "conversation"),
                              last_turn_id="prev")
    result, events, err = await run_turn(rig.driver("resume_auth", db=db), turn_id="r2", run_id="r2",
                                         scope_kind="conversation", scope_id="c1")
    assert err is not None and err.failure.category == "auth" and len(rig.log("launch")) == 1
    assert SessionRepository(db).get("conversation", "c1", "claude-code")["state"] == "ready"  # auth is not "session missing"
    assert types(events)[-2:] == ["runtime.error", "turn.completed"] and types(events).count("turn.completed") == 1


@pytest.mark.asyncio
async def test_timeout_after_submission_is_never_replayed_and_keeps_session(rig, db):
    first, _, err = await run_turn(rig.driver(db=db), turn_id="r1", run_id="r1", scope_kind="conversation", scope_id="c1")
    assert err is None
    result, events, err = await run_turn(rig.driver("hang", db=db), turn_id="r2", run_id="r2", scope_kind="conversation",
                                         scope_id="c1", timeout_s=1.0)
    assert err.failure.category == "timeout" and err.failure.acceptance in ("unknown", "output-seen")
    assert len(rig.log("launch")) == 2 and len(rig.log("prompt")) == 2  # one launch/prompt for the failed turn: no replay
    row = SessionRepository(db).get("conversation", "c1", "claude-code")
    assert row["state"] != "invalid" and row["last_turn_id"] == "r1"
    assert not pid_alive(rig.log("launch")[1]["pid"])


@pytest.mark.asyncio
async def test_session_with_gap_is_not_resumed_and_checker_requires_latest_success(rig, db):
    now = "2026-01-01T00:00:00"
    db.execute("INSERT INTO conversations(id,title,created_at,updated_at) VALUES('c1','t',?,?)", (now, now))
    for rid, status, at in (("r1", "succeeded", "2026-01-01T00:00:01"), ("r2", "succeeded", "2026-01-01T00:00:02"),
                            ("r3", "running", "2026-01-01T00:00:03")):
        db.execute("INSERT INTO conversation_runs(id,conversation_id,engine,model_id,timeout,status,created_at) "
                   "VALUES(?,?,?,?,?,?,?)", (rid, "c1", "claude-code", "m", 90, status, at))
    check = conversation_resume_check(db, "c1", "r3")
    assert check({"last_turn_id": "r2"}) is True
    assert check({"last_turn_id": "r1"}) is False  # another engine/run advanced the conversation: do not resume
    assert check({"last_turn_id": None}) is False


@pytest.mark.asyncio
async def test_missing_flags_are_refused_before_any_launch(rig):
    result, events, err = await run_turn(rig.driver("old_help"))
    assert err.failure.category == "unsupported_capability" and err.failure.acceptance == "not-submitted"
    assert rig.log("launch") == []
    assert types(events) == ["turn.started", "runtime.error", "turn.completed"]
    assert events[-1].data["status"] == "failed"


@pytest.mark.asyncio
async def test_missing_executable_is_cli_missing(rig):
    driver = ClaudeStreamDriver([str(rig.dir / "does-not-exist.exe")], cwd_root=rig.cwd_root, cache_flags=False)
    result, events, err = await run_turn(driver)
    assert err.failure.category in ("cli_missing", "unsupported_capability") and types(events).count("turn.completed") == 1


def test_environment_allowlist_has_no_tokens_and_keeps_windows_basics():
    source = {"PATH": "p", "SystemRoot": "C:\\Windows", "USERPROFILE": "C:\\u", "ANTHROPIC_API_KEY": "sk", "AGENTOS_LOCAL_TOKEN": "t",
              "GITHUB_TOKEN": "g", "AWS_SECRET_ACCESS_KEY": "a", "ProgramFiles(x86)": "pf", "LC_ALL": "C", "CLAUDE_CONFIG_DIR": "cfg",
              "HTTPS_PROXY": "http://user:pw@proxy:8080", "HTTP_PROXY": "http://proxy:8080", "FAKE_SECRET": "s",
              "OPENAI_API_KEY": "o", "APPDATA": "a", "TEMP": "t"}
    env = claude_child_env(source)
    assert set(env) == {"PATH", "SystemRoot", "USERPROFILE", "ProgramFiles(x86)", "LC_ALL", "CLAUDE_CONFIG_DIR",
                        "HTTP_PROXY", "APPDATA", "TEMP"}
    assert claude_child_env(source, {"FAKE_MODE": "x"})["FAKE_MODE"] == "x"


@pytest.mark.asyncio
async def test_no_token_reaches_the_child_process(rig, monkeypatch):
    for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "AGENTOS_LOCAL_TOKEN", "FAKE_SECRET"):
        monkeypatch.setenv(name, "must-not-leak")
    result, events, err = await run_turn(rig.driver())
    assert err is None
    launch = rig.log("launch")[0]
    assert launch["secrets"] == [] and "PATH" in {k.upper() for k in launch["env"]}
    assert "SYSTEMROOT" in {k.upper() for k in launch["env"]} or os.name != "nt"


@pytest.mark.asyncio
async def test_interrupt_during_streaming_settles_once_and_kills_the_tree(rig):
    driver = rig.driver("hang")
    events = []
    first_delta = asyncio.Event()

    async def emit(e):
        events.append(e)
        if e.type == "content.delta":
            first_delta.set()

    await driver.start_turn(turn(), emit)
    await asyncio.wait_for(first_delta.wait(), 15)
    started = time.monotonic()
    assert await driver.interrupt("t1") == "requested"
    assert time.monotonic() - started < 15  # bounded
    assert await driver.interrupt("t1") == "already-terminal"  # idempotent
    assert types(events).count("turn.completed") == 1 and events[-1].data["status"] == "cancelled"
    assert not any(e.type == "runtime.error" for e in events)
    assert not pid_alive(rig.log("launch")[0]["pid"])
    assert await driver.close("ceo", "scope") == () and await driver.stop_all() == ()
    assert await driver.interrupt("nope") == "unknown"


@pytest.mark.asyncio
async def test_cli_that_ignores_stdin_close_is_terminated_after_the_result(rig):
    started = time.monotonic()
    result, events, err = await run_turn(rig.driver("ignore_close"))
    assert err is None and result.text == "echo:hi" and time.monotonic() - started < 15
    assert not pid_alive(rig.log("launch")[0]["pid"])


@pytest.mark.asyncio
async def test_caller_cancellation_cleans_up_through_the_collector(rig):
    driver = rig.driver("hang")
    seen = asyncio.Event()

    async def emit(e):
        if e.type == "content.delta":
            seen.set()

    task = asyncio.create_task(collect_turn(driver, turn(), emit))
    await asyncio.wait_for(seen.wait(), 15)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not pid_alive(rig.log("launch")[0]["pid"])


@pytest.mark.asyncio
async def test_permission_requests_are_denied_never_approved(rig):
    result, events, err = await run_turn(rig.driver("perm"))
    assert err is None and result.text == "perm:deny"
    answer = rig.log("control_response")[0]["answer"]
    assert answer["type"] == "control_response" and answer["response"]["request_id"] == "r1"
    assert any(e.type == "runtime.notice" and e.data["code"] == "permission_denied" for e in events)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode,category", [("api_error", "auth"), ("no_result", "protocol_error")])
async def test_failures_have_one_error_and_one_terminal(rig, mode, category):
    result, events, err = await run_turn(rig.driver(mode))
    assert err.failure.category == category
    assert types(events)[-2:] == ["runtime.error", "turn.completed"] and types(events).count("turn.completed") == 1
    assert "Not logged in - Please" not in json.dumps([e.data for e in events])  # raw CLI text is not echoed


@pytest.mark.asyncio
async def test_invalid_arguments_and_busy_scope(rig):
    driver = rig.driver("hang")
    with pytest.raises(ValueError):
        await driver.start_turn(turn(model_id=""), lambda e: None)

    async def emit(e):
        pass

    await driver.start_turn(turn(), emit)
    with pytest.raises(DriverTurnError) as busy:
        await driver.start_turn(turn(turn_id="t2", run_id="t2"), emit)
    assert busy.value.failure.category == "busy"
    await driver.interrupt("t1")
    result, events, err = await run_turn(rig.driver(), model_id="bad model!")
    assert err.failure.category == "model_unavailable" and rig.log("prompt") == []


def test_routing_is_in_one_place_and_default_on(monkeypatch):
    runtime = MockRuntime()
    monkeypatch.setattr(cd, "default_cli_cmd", lambda: None)
    monkeypatch.delenv(engine_registry.CLAUDE_DRIVER_ENV, raising=False)
    assert isinstance(engine_registry.create_turn_driver("claude-code", runtime), ClaudeStreamDriver)
    monkeypatch.setenv(engine_registry.CLAUDE_DRIVER_ENV, "wrapper")
    assert isinstance(engine_registry.create_turn_driver("claude-code", runtime), ExecuteTaskDriver)
    monkeypatch.setenv(engine_registry.CLAUDE_DRIVER_ENV, "stream")
    assert isinstance(engine_registry.create_turn_driver("claude-code", runtime), ClaudeStreamDriver)
    assert isinstance(engine_registry.create_turn_driver("codex", runtime), ExecuteTaskDriver)
    assert isinstance(engine_registry.create_turn_driver("claude-code", runtime, enabled=False), ExecuteTaskDriver)
    monkeypatch.setenv(engine_registry.CLAUDE_DRIVER_ENV, "off")
    assert isinstance(engine_registry.create_turn_driver("claude-code", runtime), ExecuteTaskDriver)


@pytest.mark.asyncio
async def test_conversation_runs_use_native_session_without_replaying_history(rig, make_app, monkeypatch, tmp_path):
    monkeypatch.setenv(engine_registry.CLAUDE_DRIVER_ENV, "stream")
    monkeypatch.setattr(cd, "default_cli_cmd", lambda: [sys.executable, str(rig.script)])
    monkeypatch.setattr(cd, "_FLAG_CACHE", {})
    monkeypatch.setattr(settings, "SQLITE_DB_PATH", str(tmp_path / "app" / "agentos.db"))
    api = Api(make_app(runtime_factory=lambda engine: MockRuntime(), mode="real"))
    cid = await new_conv(api)
    first = await api("runs.start_turn", conversation_id=cid, engine="claude-code", model_id="sonnet", prompt="first")
    done1 = await wait_status(api, first["id"], "succeeded", timeout=30)
    second = await api("runs.start_turn", conversation_id=cid, engine="claude-code", model_id="sonnet", prompt="second")
    done2 = await wait_status(api, second["id"], "succeeded", timeout=30)
    assert done1["assistant_content"] == "echo:first"
    assert done2["assistant_content"] == "echo:second"  # incremental prompt only; history lives in the native session
    launches = rig.log("launch")
    assert "--session-id" in launches[0]["args"] and "--resume" in launches[1]["args"]
    assert "Previous conversation" not in rig.log("prompt")[1]["prompt"]
    row = SessionRepository(api.db).get("conversation", cid, "claude-code")
    assert row["last_turn_id"] == second["id"] and row["state"] == "ready"
    assert api.db.scalar("SELECT COUNT(*) FROM usage_events WHERE run_id=?", (second["id"],)) == 1  # usage recorded once
    # switching away from the session (a run on another engine) breaks resume: rebuilt from history, never silently resumed
    api.db.execute("UPDATE runtime_sessions SET last_turn_id='someone-else'")
    third = await api("runs.start_turn", conversation_id=cid, engine="claude-code", model_id="sonnet", prompt="third")
    done3 = await wait_status(api, third["id"], "succeeded", timeout=30)
    assert "Previous conversation (JSON)" in done3["assistant_content"] and "--session-id" in rig.log("launch")[2]["args"]
    assert SessionRepository(api.db).get("conversation", cid, "claude-code")["generation"] == 2


def _seed_session(rig, db, sid="gone"):
    SessionRepository(db).put(scope_kind="conversation", scope_id="c1", engine="claude-code", runtime_key="default",
                              protocol="claude-stream-json", namespace="claude:default", native_session_id=sid,
                              generation=1, state="ready", model_id="sonnet", cwd=os.path.join(rig.cwd_root, "conversation"),
                              last_turn_id="prev")


@pytest.mark.asyncio
async def test_s2_refusal_after_the_prompt_was_read_is_never_replayed(rig, db):
    _seed_session(rig, db)
    result, events, err = await run_turn(rig.driver("refuse_after_read", db=db, resume_prompt="INCREMENTAL"), turn_id="r2",
                                         run_id="r2", scope_kind="conversation", scope_id="c1", prompt="FULL")
    assert err is not None and result is None
    assert len(rig.log("launch")) == 1 and len(rig.log("prompt")) == 1  # exactly one prompt ever reached the CLI
    assert rig.log("prompt")[0]["prompt"] == "INCREMENTAL"
    assert not any(e.type == "runtime.notice" and e.data["code"] == "session_rebuilt" for e in events)
    assert types(events)[-2:] == ["runtime.error", "turn.completed"] and events[-1].data["status"] == "failed"
    assert SessionRepository(db).get("conversation", "c1", "claude-code")["state"] == "invalid"
    # the NEXT turn starts a fresh session instead of resuming the dead one
    second, ev2, err2 = await run_turn(rig.driver("ok", db=db), turn_id="r3", run_id="r3", scope_kind="conversation",
                                       scope_id="c1", prompt="NEXT")
    assert err2 is None and second.text == "echo:NEXT"
    assert "--session-id" in rig.log("launch")[1]["args"] and "--resume" not in rig.log("launch")[1]["args"]


def test_s2_refusal_pattern_matches_only_an_explicit_missing_session():
    assert cd._REFUSED_RE.search("No conversation found with session ID: abc")
    assert cd._REFUSED_RE.search("Session abc does not exist")
    assert not cd._REFUSED_RE.search("session token invalid, please log in")
    assert not cd._REFUSED_RE.search("your session is invalid")


@pytest.mark.asyncio
async def test_s6_stdout_queue_is_bounded_and_overflow_fails_the_turn(rig, monkeypatch):
    monkeypatch.setattr(cd, "MAX_QUEUE_ITEMS", 4)
    driver = rig.driver("flood")
    real_line = driver._line

    async def slow_line(s, raw):
        await asyncio.sleep(0.05)
        await real_line(s, raw)

    monkeypatch.setattr(driver, "_line", slow_line)
    result, events, err = await run_turn(driver)
    assert err is not None and err.failure.category == "output_limit"
    assert types(events).count("turn.completed") == 1
    assert not pid_alive(rig.log("launch")[0]["pid"])


@pytest.mark.asyncio
async def test_s7_stdin_writes_are_serialised_across_the_whole_line(rig):
    order = []

    class Stdin:
        def write(self, data):
            order.append("w")
            time.sleep(0.05)

        def flush(self):
            order.append("f")

    class P:
        class proc:
            stdin = Stdin()

    async def emit(e):
        pass

    driver = rig.driver()
    s = cd._Turn(turn(), emit)
    s.proc = P()
    reqs = [{"type": "control_request", "request_id": f"id{i}", "request": {"subtype": "can_use_tool"}} for i in range(2)]
    await asyncio.gather(*(driver._control(s, r) for r in reqs))
    assert order == ["w", "f", "w", "f"]


@pytest.mark.asyncio
async def test_s8_api_key_env_only_with_explicit_opt_in(rig, monkeypatch):
    for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "AGENTOS_LOCAL_TOKEN", "FAKE_SECRET", "OPENAI_API_KEY"):
        monkeypatch.setenv(name, "v")
    result, events, err = await run_turn(rig.driver(allow_api_key_env=True))
    assert err is None
    assert sorted(rig.log("launch")[0]["secrets"]) == ["ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"]
    assert "OPENAI_API_KEY" not in rig.log("launch")[0]["env"]
    assert rig.driver()._env().get("ANTHROPIC_API_KEY") is None  # default stays closed


def test_s8_registry_reuses_the_runtimes_opt_in(monkeypatch):
    from backend.pyworker.runtime.claude_runtime import ClaudeCodeRuntime
    monkeypatch.setattr(cd, "default_cli_cmd", lambda: None)
    monkeypatch.delenv(engine_registry.CLAUDE_DRIVER_ENV, raising=False)
    on = engine_registry.create_turn_driver("claude-code", ClaudeCodeRuntime(["x"], allow_api_key_env=True))
    off = engine_registry.create_turn_driver("claude-code", ClaudeCodeRuntime(["x"]))
    assert on._allow_api_key_env is True and off._allow_api_key_env is False


@pytest.mark.asyncio
async def test_s10_every_control_request_gets_an_answer_whatever_its_subtype_or_id_type(rig):
    result, events, err = await run_turn(rig.driver("ctl"))
    assert err is None and result.text == "ctl-ok"
    answers = {a["answer"]["response"]["request_id"]: a["answer"]["response"] for a in rig.log("control_response")}
    assert set(answers) == {7, "m1", 9}
    assert answers[7]["subtype"] == "error" and answers["m1"]["subtype"] == "error"
    assert answers[9]["subtype"] == "success" and answers[9]["response"]["behavior"] == "deny"


@pytest.mark.asyncio
async def test_s10_missing_permission_prompts_flag_refuses_before_launch(rig):
    result, events, err = await run_turn(rig.driver("no_prompts"))
    assert err.failure.category == "unsupported_capability" and err.failure.acceptance == "not-submitted"
    assert rig.log("launch") == []
