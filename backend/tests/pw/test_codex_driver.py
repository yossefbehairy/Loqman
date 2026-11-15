"""CodexAppServerDriver against a FAKE `codex app-server` (JSON-RPC over stdio). No real codex, network, login or model."""

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
from backend.pyworker.runtime import codex_driver as xd
from backend.pyworker.runtime import engine_registry
from backend.pyworker.runtime.codex_driver import CodexAppServerDriver
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
    if "--help" in args:
        print("Usage: codex app-server [OPTIONS]\n  -c, --config <key=value>\n      --listen <URL>"); sys.exit(0)

    def log(**kw):
        with open(os.path.join(HERE, "log.jsonl"), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(kw) + "\n")

    def out(o):
        sys.stdout.write(json.dumps(o) + "\n"); sys.stdout.flush()

    secrets = [k for k in ("OPENAI_API_KEY", "CODEX_API_KEY", "AGENTOS_LOCAL_TOKEN", "FAKE_SECRET") if k in os.environ]
    log(event="launch", args=args, cwd=os.getcwd(), pid=os.getpid(), env=sorted(os.environ), secrets=secrets)
    store = os.path.join(HERE, "store"); os.makedirs(store, exist_ok=True)
    state = {"turn": 0}

    def usage(thread, add):
        path = os.path.join(store, thread)
        cur = json.load(open(path)) if os.path.exists(path) else 0
        json.dump(cur + add, open(path, "w"))
        blk = lambda n: {"inputTokens": n, "cachedInputTokens": 1, "outputTokens": 2 * n, "reasoningOutputTokens": 0, "totalTokens": 3 * n}
        return {"total": blk(cur + add), "last": blk(add), "modelContextWindow": 1000}

    def notify(method, **params):
        out({"method": method, "params": params})

    def run_turn(rid, params):
        thread = params["threadId"]; state["turn"] += 1; tid = "turn-%d" % state["turn"]
        prompt = params["input"][0]["text"]
        log(event="prompt", prompt=prompt, params=params)
        notify("turn/started", threadId=thread, turn={"id": tid, "items": [], "status": "inProgress"})
        reply = "echo:" + prompt
        if MODE == "perm":
            out({"id": 900, "method": "item/commandExecution/requestApproval", "params": {"threadId": thread, "turnId": tid, "itemId": "c1"}})
        # notifications BEFORE the response that introduces the turn id must be buffered, then replayed in order
        notify("item/started", threadId=thread, turnId=tid, item={"type": "agentMessage", "id": "m1", "text": "", "phase": "final_answer"}, startedAtMs=1)
        third = max(1, len(reply) // 3)
        parts = [reply[i:i + third] for i in range(0, len(reply), third)]
        notify("item/agentMessage/delta", threadId=thread, turnId=tid, itemId="m1", delta=parts[0])
        out({"id": rid, "result": {"turn": {"id": tid, "items": [], "status": "inProgress"}}})
        if MODE == "perm":
            for line in sys.stdin:
                o = json.loads(line)
                if o.get("id") == 900:
                    log(event="approval_response", answer=o); break
        if MODE == "tools":
            notify("item/completed", threadId=thread, turnId=tid, completedAtMs=2,
                   item={"type": "agentMessage", "id": "m1", "text": "".join(parts), "phase": "commentary"})
            notify("item/started", threadId=thread, turnId=tid, startedAtMs=3, item={"type": "commandExecution", "id": "cmd1", "command": "ls", "status": "inProgress"})
            notify("item/completed", threadId=thread, turnId=tid, completedAtMs=4, item={"type": "commandExecution", "id": "cmd1", "command": "ls", "status": "completed", "exitCode": 0, "aggregatedOutput": "a.txt"})
            notify("item/started", threadId=thread, turnId=tid, startedAtMs=5, item={"type": "commandExecution", "id": "cmd2", "command": "rm", "status": "inProgress"})
            notify("item/completed", threadId=thread, turnId=tid, completedAtMs=6, item={"type": "commandExecution", "id": "cmd2", "command": "rm", "status": "declined", "exitCode": None})
            reply = "done:" + prompt
            notify("item/started", threadId=thread, turnId=tid, startedAtMs=7, item={"type": "agentMessage", "id": "m2", "text": "", "phase": "final_answer"})
            notify("item/agentMessage/delta", threadId=thread, turnId=tid, itemId="m2", delta=reply)
            notify("item/completed", threadId=thread, turnId=tid, completedAtMs=8, item={"type": "agentMessage", "id": "m2", "text": reply, "phase": "final_answer"})
        else:
            for p in parts[1:]:
                notify("item/agentMessage/delta", threadId=thread, turnId=tid, itemId="m1", delta=p)
                if MODE in ("hang", "hang_ignore"):
                    log(event="streaming")
                    if MODE == "hang_ignore":
                        time.sleep(120)
                    for line in sys.stdin:
                        o = json.loads(line)
                        if o.get("method") == "turn/interrupt":
                            log(event="interrupt", params=o["params"])
                            out({"id": o["id"], "result": {}})
                            notify("turn/completed", threadId=thread, turn={"id": tid, "items": [], "status": "interrupted"})
                            time.sleep(0.2); sys.exit(0)
                    sys.exit(0)
            notify("item/completed", threadId=thread, turnId=tid, completedAtMs=2, item={"type": "agentMessage", "id": "m1", "text": reply, "phase": "final_answer"})
        if MODE.startswith("error_"):
            info = {"error_auth": "unauthorized", "error_rate": "rateLimitExceeded", "error_model": "modelNotFound"}[MODE]
            notify("turn/completed", threadId=thread, turn={"id": tid, "items": [], "status": "failed", "error": {"message": "RAW-SECRET-TEXT", "codexErrorInfo": info}})
            return
        if MODE == "no_complete":
            sys.exit(0)
        notify("thread/tokenUsage/updated", threadId=thread, turnId=tid, tokenUsage=usage(thread, 3))
        notify("turn/completed", threadId=thread, turn={"id": tid, "items": [], "status": "completed"})

    for line in sys.stdin:
        o = json.loads(line)
        method, rid, params = o.get("method"), o.get("id"), o.get("params") or {}
        if method != "initialized":
            log(event="req", method=method, params=params)
        if method == "initialize":
            out({"id": rid, "result": {"userAgent": "fake"}})
        elif method == "thread/start":
            tid = str(uuid.uuid4()); open(os.path.join(store, tid), "w").write("0")
            out({"id": rid, "result": {"thread": {"id": tid}, "model": params.get("model"), "approvalPolicy": "never", "sandbox": "read-only"}})
        elif method == "thread/resume":
            tid = params["threadId"]
            if not os.path.exists(os.path.join(store, tid)):
                msg = "401 unauthorized: please sign in" if MODE == "resume_auth" else "no rollout found for thread id " + tid
                out({"id": rid, "error": {"code": -32600, "message": msg}}); continue
            out({"id": rid, "result": {"thread": {"id": tid}, "model": params.get("model")}})
        elif method == "turn/start":
            run_turn(rid, params)
        elif method == "turn/interrupt":
            out({"id": rid, "result": {}})
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
        self.script = self.dir / "fake_codex.py"
        self.script.write_text(FAKE, encoding="utf-8")
        self.cwd_root = str(tmp_path / "cwd")

    def log(self, event=None):
        path = self.dir / "log.jsonl"
        rows = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []
        return [r for r in rows if event is None or r["event"] == event]

    def reqs(self, method):
        return [r["params"] for r in self.log("req") if r["method"] == method]

    def driver(self, mode="ok", db=None, **kw):
        sessions = SessionRepository(db) if db is not None else None
        kw.setdefault("exit_grace_s", 0.3)
        kw.setdefault("interrupt_grace_s", 1.0)
        return CodexAppServerDriver([sys.executable, str(self.script)], sessions=sessions, cwd_root=self.cwd_root,
                                    extra_env={"FAKE_MODE": mode}, cache_flags=False, **kw)


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
    base = StartTurn("t1", "t1", "ceo", "scope", "hi", "gpt-5", ".", ToolPolicy("none", "deny"), timeout_s=20)
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


def seed(db, rig, thread="gone", last="prev"):
    SessionRepository(db).put(scope_kind="conversation", scope_id="c1", engine="codex", runtime_key="default",
                              protocol="codex-app-server", namespace="codex:default", native_session_id=thread,
                              generation=1, state="ready", model_id="gpt-5", cwd=os.path.join(rig.cwd_root, "conversation"),
                              last_turn_id=last)


@pytest.mark.asyncio
async def test_event_order_single_terminal_deltas_equal_text_and_usage_once(rig):
    result, events, err = await run_turn(rig.driver())
    assert err is None
    assert [e.seq for e in events] == list(range(1, len(events) + 1))
    assert types(events)[:2] == ["turn.started", "session.started"]
    assert events[-1].type == "turn.completed" and types(events).count("turn.completed") == 1
    deltas = "".join(e.data["delta"] for e in events if e.type == "content.delta")
    assert deltas == "echo:hi" == result.text  # the delta that preceded the turn/start response was buffered, not lost
    assert sum(e.type == "usage" for e in events) == 1
    usage = next(e for e in events if e.type == "usage")
    assert usage.data["final"] and usage.data["tokens"] == {"input_tokens": 3, "cached_input_tokens": 1, "output_tokens": 6,
                                                             "reasoning_output_tokens": 0}
    assert result.tokens["input_tokens"] == 3 and result.model_id == "gpt-5" and result.session_id
    assert all(e.engine == "codex" for e in events) and not any(e.type == "runtime.error" for e in events)
    assert next(e for e in events if e.type == "item.completed").data["phase"] == "final"


@pytest.mark.asyncio
async def test_tool_events_commentary_excluded_and_declined_tool(rig):
    result, events, err = await run_turn(rig.driver("tools"), prompt="go")
    assert err is None and result.text == "done:go"
    done = [e for e in events if e.type == "item.completed" and e.data["item_type"] == "tool"]
    assert [(e.item_id, e.data["ok"], e.data["status"]) for e in done] == [("cmd1", True, "completed"), ("cmd2", False, "declined")]
    commentary = next(e for e in events if e.type == "item.completed" and e.data.get("phase") == "commentary")
    assert commentary.data["text"].startswith("echo:go")
    assert types(events).index("item.started") < types(events).index("turn.completed")


@pytest.mark.asyncio
async def test_exact_argv_read_only_never_ask_and_protocol_parameters(rig):
    result, events, err = await run_turn(rig.driver(), effort="high")
    assert err is None
    launch = rig.log("launch")[0]
    assert launch["args"] == ["app-server", "-c", 'approval_policy="never"', "-c", 'sandbox_mode="read-only"',
                              "-c", "features.browser_use=false", "-c", "features.browser_use_external=false",
                              "-c", "features.computer_use=false"]
    assert "agentos-codex-" in launch["cwd"] and not os.path.exists(launch["cwd"])
    init = rig.reqs("initialize")[0]
    assert init["clientInfo"]["name"] == "agentos" and init["capabilities"] == {"experimentalApi": False}
    start = rig.reqs("thread/start")[0]
    assert start["approvalPolicy"] == "never" and start["sandbox"] == "read-only" and start["ephemeral"] is True
    assert start["model"] == "gpt-5"
    ts_ = rig.reqs("turn/start")[0]
    assert ts_["approvalPolicy"] == "never" and ts_["sandboxPolicy"] == {"type": "readOnly"} and ts_["effort"] == "high"
    assert ts_["input"] == [{"type": "text", "text": "hi", "text_elements": []}] and ts_["threadId"] == result.session_id


@pytest.mark.asyncio
async def test_conversation_thread_is_stored_and_resumed_with_incremental_prompt(rig, db):
    first, ev1, err = await run_turn(rig.driver(db=db), turn_id="r1", run_id="r1", scope_kind="conversation", scope_id="c1",
                                     prompt="FULL-1")
    assert err is None
    row = SessionRepository(db).get("conversation", "c1", "codex")
    assert row["state"] == "ready" and row["protocol"] == "codex-app-server" and row["last_turn_id"] == "r1"
    assert row["native_session_id"] == first.session_id and row["generation"] == 1
    assert rig.reqs("thread/start")[0]["ephemeral"] is False
    assert next(e for e in ev1 if e.type == "session.started").data["resumed"] is False
    second, ev2, err = await run_turn(rig.driver(db=db, resume_prompt="INCREMENTAL-2"), turn_id="r2", run_id="r2",
                                      scope_kind="conversation", scope_id="c1", prompt="FULL-2")
    assert err is None and second.text == "echo:INCREMENTAL-2"
    resume = rig.reqs("thread/resume")[0]
    assert resume["threadId"] == first.session_id and resume["approvalPolicy"] == "never" and resume["sandbox"] == "read-only"
    assert len(rig.reqs("thread/start")) == 1
    started = next(e for e in ev2 if e.type == "session.started").data
    assert started["resumed"] is True and started["rebuilt"] is False and started["session_id"] == first.session_id
    assert SessionRepository(db).get("conversation", "c1", "codex")["last_turn_id"] == "r2"
    # per-turn usage is the delta of the thread total, not the cumulative total
    assert next(e for e in ev2 if e.type == "usage").data["tokens"]["input_tokens"] == 3
    assert rig.log("launch")[0]["cwd"] == rig.log("launch")[1]["cwd"]


@pytest.mark.asyncio
async def test_unknown_thread_is_rebuilt_once_before_submission(rig, db):
    seed(db, rig)
    result, events, err = await run_turn(rig.driver(db=db, resume_prompt="INCREMENTAL"), turn_id="r2", run_id="r2",
                                         scope_kind="conversation", scope_id="c1", prompt="FULL-WITH-HISTORY")
    assert err is None and result.text == "echo:FULL-WITH-HISTORY"
    assert len(rig.reqs("thread/resume")) == 1 and len(rig.reqs("thread/start")) == 1 and len(rig.log("prompt")) == 1
    notice = next(e for e in events if e.type == "runtime.notice")
    assert notice.data["code"] == "session_rebuilt" and types(events).count("turn.completed") == 1
    started = next(e for e in events if e.type == "session.started").data
    assert started["rebuilt"] is True and started["resumed"] is False and started["generation"] == 2
    row = SessionRepository(db).get("conversation", "c1", "codex")
    assert row["native_session_id"] == result.session_id != "gone" and row["state"] == "ready" and row["generation"] == 2


@pytest.mark.asyncio
async def test_resume_auth_error_is_not_rebuilt(rig, db):
    seed(db, rig)
    result, events, err = await run_turn(rig.driver("resume_auth", db=db), turn_id="r2", run_id="r2",
                                         scope_kind="conversation", scope_id="c1")
    assert err.failure.category == "auth" and err.failure.acceptance == "not-submitted"
    assert rig.log("prompt") == [] and rig.reqs("thread/start") == []
    assert SessionRepository(db).get("conversation", "c1", "codex")["state"] == "ready"
    assert types(events)[-2:] == ["runtime.error", "turn.completed"] and types(events).count("turn.completed") == 1


@pytest.mark.asyncio
async def test_failure_after_submission_is_never_replayed_and_keeps_session(rig, db):
    first, _, err = await run_turn(rig.driver(db=db), turn_id="r1", run_id="r1", scope_kind="conversation", scope_id="c1")
    assert err is None
    result, events, err = await run_turn(rig.driver("hang", db=db), turn_id="r2", run_id="r2", scope_kind="conversation",
                                         scope_id="c1", timeout_s=1.0)
    assert err.failure.category == "timeout" and err.failure.acceptance in ("unknown", "output-seen")
    assert len(rig.log("prompt")) == 2  # one prompt for the failed turn: no replay
    row = SessionRepository(db).get("conversation", "c1", "codex")
    assert row["state"] != "invalid" and row["last_turn_id"] == "r1"
    assert not pid_alive(rig.log("launch")[1]["pid"])


@pytest.mark.asyncio
async def test_session_with_gap_is_not_resumed(rig, db):
    now = "2026-01-01T00:00:00"
    db.execute("INSERT INTO conversations(id,title,created_at,updated_at) VALUES('c1','t',?,?)", (now, now))
    for rid, status, at in (("r1", "succeeded", "2026-01-01T00:00:01"), ("r2", "succeeded", "2026-01-01T00:00:02"),
                            ("r3", "running", "2026-01-01T00:00:03")):
        db.execute("INSERT INTO conversation_runs(id,conversation_id,engine,model_id,timeout,status,created_at) "
                   "VALUES(?,?,?,?,?,?,?)", (rid, "c1", "codex", "m", 90, status, at))
    check = xd.conversation_resume_check(db, "c1", "r3")
    assert check({"last_turn_id": "r2"}) is True and check({"last_turn_id": "r1"}) is False
    seed(db, rig, thread="old", last="r1")
    result, events, err = await run_turn(rig.driver(db=db, resume_prompt="INC", resume_check=check), turn_id="r3", run_id="r3",
                                         scope_kind="conversation", scope_id="c1", prompt="FULL")
    assert err is None and result.text == "echo:FULL"
    assert rig.reqs("thread/resume") == [] and len(rig.reqs("thread/start")) == 1  # fresh thread, full prompt
    assert SessionRepository(db).get("conversation", "c1", "codex")["generation"] == 2


@pytest.mark.asyncio
async def test_interrupt_uses_protocol_then_settles_once_and_process_is_gone(rig):
    driver = rig.driver("hang")
    events = []
    seen = asyncio.Event()

    async def emit(e):
        events.append(e)
        if e.type == "content.delta" and len(events) > 3:
            seen.set()

    await driver.start_turn(turn(), emit)
    await asyncio.wait_for(seen.wait(), 15)
    for _ in range(100):
        if rig.log("streaming"):
            break
        await asyncio.sleep(0.05)
    started = time.monotonic()
    assert await driver.interrupt("t1") == "requested"
    assert time.monotonic() - started < 15
    assert await driver.interrupt("t1") == "already-terminal"
    assert types(events).count("turn.completed") == 1 and events[-1].data["status"] == "cancelled"
    assert not any(e.type == "runtime.error" for e in events)
    interrupt = rig.log("interrupt")
    assert len(interrupt) == 1 and interrupt[0]["params"]["threadId"] and interrupt[0]["params"]["turnId"] == "turn-1"
    assert not pid_alive(rig.log("launch")[0]["pid"])
    assert await driver.close("ceo", "scope") == () and await driver.stop_all() == ()
    assert await driver.interrupt("nope") == "unknown"


@pytest.mark.asyncio
async def test_interrupt_falls_back_to_killing_the_tree_when_ignored(rig):
    driver = rig.driver("hang_ignore", interrupt_grace_s=0.5)
    events = []
    seen = asyncio.Event()

    async def emit(e):
        events.append(e)
        if e.type == "content.delta":
            seen.set()

    await driver.start_turn(turn(), emit)
    await asyncio.wait_for(seen.wait(), 15)
    for _ in range(100):
        if rig.log("streaming"):
            break
        await asyncio.sleep(0.05)
    assert await driver.interrupt("t1") == "requested"
    assert types(events).count("turn.completed") == 1 and events[-1].data["status"] == "cancelled"
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
async def test_approval_requests_are_denied_never_accepted(rig):
    result, events, err = await run_turn(rig.driver("perm"))
    assert err is None and result.text == "echo:hi"
    answer = rig.log("approval_response")[0]["answer"]
    assert answer["id"] == 900 and answer["result"] == {"decision": "decline"}
    assert any(e.type == "runtime.notice" and e.data["code"] == "permission_denied" for e in events)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode,category,retryable", [("error_auth", "auth", False), ("error_rate", "rate_limit", True),
                                                      ("error_model", "model_unavailable", False),
                                                      ("no_complete", "protocol_error", False)])
async def test_error_mapping_one_error_one_terminal_no_raw_text(rig, mode, category, retryable):
    result, events, err = await run_turn(rig.driver(mode))
    assert err.failure.category == category and err.failure.retryable is retryable
    assert types(events)[-2:] == ["runtime.error", "turn.completed"] and types(events).count("turn.completed") == 1
    assert "RAW-SECRET-TEXT" not in json.dumps([e.data for e in events])


def test_classify_error_uses_info_then_text():
    assert xd.classify_error({"message": "x", "codexErrorInfo": {"httpConnectionFailed": {"httpStatusCode": 502}}})[0] == "offline"
    assert xd.classify_error({"message": "usage limit reached"})[0] == "quota"
    assert xd.classify_error({"message": "HTTP 429 Too Many Requests"})[0] == "rate_limit"


@pytest.mark.asyncio
async def test_missing_executable_is_cli_missing(rig):
    driver = CodexAppServerDriver([str(rig.dir / "does-not-exist.exe")], cwd_root=rig.cwd_root, cache_flags=False)
    result, events, err = await run_turn(driver)
    assert err.failure.category in ("cli_missing", "unsupported_capability") and types(events).count("turn.completed") == 1
    driver = CodexAppServerDriver(["x"], cwd_root=rig.cwd_root)
    driver._cmd = None
    result, events, err = await run_turn(driver)
    assert err.failure.category == "cli_missing" and err.failure.acceptance == "not-submitted"


@pytest.mark.asyncio
async def test_validation_model_effort_prompt_and_scoped_mcp(rig):
    for kw, category in (({"model_id": "bad model!"}, "model_unavailable"), ({"variant": "ultra"}, "unsupported_capability"),
                         ({"variant": "Bad Effort!"}, "unsupported_capability"), ({"prompt": "  "}, "request_refused"),
                         ({"tool_policy": ToolPolicy("scoped", "deny", mcp_servers={"x": {}})}, "unsupported_capability")):
        result, events, err = await run_turn(rig.driver(), **kw)
        assert err.failure.category == category and err.failure.acceptance == "not-submitted", kw
    assert rig.log("launch") == [] and rig.log("prompt") == []
    result, events, err = await run_turn(rig.driver(), variant="low")  # effort via the legacy `variant` field
    assert err is None and rig.reqs("turn/start")[0]["effort"] == "low" and result.variant_id == "low"


@pytest.mark.asyncio
async def test_busy_scope_and_invalid_arguments(rig):
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


@pytest.mark.asyncio
async def test_environment_allowlist_and_api_key_opt_in(rig, monkeypatch):
    for name in ("OPENAI_API_KEY", "CODEX_API_KEY", "AGENTOS_LOCAL_TOKEN", "FAKE_SECRET"):
        monkeypatch.setenv(name, "must-not-leak")
    result, events, err = await run_turn(rig.driver())
    assert err is None
    launch = rig.log("launch")[0]
    assert launch["secrets"] == [] and "PATH" in {k.upper() for k in launch["env"]}
    result, events, err = await run_turn(rig.driver(allow_api_key_env=True), turn_id="t2", run_id="t2")
    assert err is None
    assert sorted(rig.log("launch")[1]["secrets"]) == ["CODEX_API_KEY", "OPENAI_API_KEY"]  # opt-in keys only, never the local token


def test_routing_default_off_and_flag_on(monkeypatch):
    runtime = MockRuntime()
    monkeypatch.delenv(engine_registry.CODEX_DRIVER_ENV, raising=False)
    assert isinstance(engine_registry.create_turn_driver("codex", runtime), ExecuteTaskDriver)
    monkeypatch.setenv(engine_registry.CODEX_DRIVER_ENV, "appserver")
    monkeypatch.setattr(xd, "default_cli_cmd", lambda: None)
    assert isinstance(engine_registry.create_turn_driver("codex", runtime), CodexAppServerDriver)
    assert isinstance(engine_registry.create_turn_driver("opencode", runtime), ExecuteTaskDriver)
    monkeypatch.setenv(engine_registry.CODEX_DRIVER_ENV, "off")
    assert isinstance(engine_registry.create_turn_driver("codex", runtime), ExecuteTaskDriver)


@pytest.mark.asyncio
async def test_conversation_runs_use_native_thread_without_replaying_history(rig, make_app, monkeypatch, tmp_path):
    monkeypatch.setenv(engine_registry.CODEX_DRIVER_ENV, "appserver")
    monkeypatch.setattr(xd, "default_cli_cmd", lambda: [sys.executable, str(rig.script)])
    monkeypatch.setattr(xd, "_FLAG_CACHE", {})
    monkeypatch.setattr(settings, "SQLITE_DB_PATH", str(tmp_path / "app" / "agentos.db"))
    api = Api(make_app(runtime_factory=lambda engine: MockRuntime(), mode="real"))
    cid = await new_conv(api)
    first = await api("runs.start_turn", conversation_id=cid, engine="codex", model_id="gpt-5", prompt="first")
    done1 = await wait_status(api, first["id"], "succeeded", timeout=30)
    second = await api("runs.start_turn", conversation_id=cid, engine="codex", model_id="gpt-5", prompt="second")
    done2 = await wait_status(api, second["id"], "succeeded", timeout=30)
    assert done1["assistant_content"] == "echo:first" and done2["assistant_content"] == "echo:second"
    assert len(rig.reqs("thread/start")) == 1 and len(rig.reqs("thread/resume")) == 1
    assert "Previous conversation" not in rig.log("prompt")[1]["prompt"]
    row = SessionRepository(api.db).get("conversation", cid, "codex")
    assert row["last_turn_id"] == second["id"] and row["state"] == "ready"
    api.db.execute("UPDATE runtime_sessions SET last_turn_id='someone-else'")
    third = await api("runs.start_turn", conversation_id=cid, engine="codex", model_id="gpt-5", prompt="third")
    done3 = await wait_status(api, third["id"], "succeeded", timeout=30)
    assert "Previous conversation (JSON)" in done3["assistant_content"] and len(rig.reqs("thread/start")) == 2
    assert SessionRepository(api.db).get("conversation", cid, "codex")["generation"] == 2
