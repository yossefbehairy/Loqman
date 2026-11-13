"""Conversations + persisted runs: lifecycle, CAS guarantees, replay, recovery."""

import asyncio
import json

import pytest

from backend.pyworker.runtime.base import AgentResult
from backend.pyworker.runtime.errors import RuntimeCancelled, RuntimeErrorBase

from .helpers import Api


class FakeRuntime:
    """Blocks on a gate; abort_task releases it with RuntimeCancelled like the CLI adapters."""

    def __init__(self, text="hello", fail=None, gated=True, ignore_abort=False):
        self.text, self.fail, self.gated, self.ignore_abort = text, fail, gated, ignore_abort
        self.gate = asyncio.Event()
        self.started = asyncio.Event()
        self.calls, self.aborted = [], []
        self._abort = False

    async def execute_task(self, task):
        self.calls.append(task)
        self.started.set()
        if self.gated:
            await self.gate.wait()
        if self._abort and not self.ignore_abort:
            raise RuntimeCancelled("cancelled")
        if self.fail:
            raise self.fail
        return AgentResult(text=self.text, model_id=task.model_id)

    async def abort_task(self, session_id):
        self.aborted.append(session_id)
        self._abort = True
        if not self.ignore_abort:
            self.gate.set()


@pytest.fixture
def rig(make_app):
    rt = FakeRuntime()
    app = make_app(runtime_factory=lambda engine: rt, mode="real")
    return Api(app), rt


async def new_conv(api, title="c"):
    return (await api("conversations.create", title=title))["id"]


@pytest.mark.asyncio
async def test_correction_cancels_old_attempt_and_is_idempotent(make_app):
    runtime = FakeRuntime(ignore_abort=True)
    api = Api(make_app(runtime_factory=lambda _: runtime, mode="real"))
    cid = await new_conv(api)
    original = await api("runs.start_turn", conversation_id=cid, engine="codex",
                         model_id="m", prompt="Compare all columns")
    await runtime.started.wait()
    corrected = await api("runs.steer", run_id=original["id"], correction="Compare price only")
    repeated = await api("runs.steer", run_id=original["id"], correction="Compare price only")
    assert corrected["id"] == repeated["id"] != original["id"]
    assert (await api("runs.get", run_id=original["id"]))["status"] == "cancelled"
    runtime.gate.set()
    await wait_status(api, corrected["id"], "succeeded")
    assert len(runtime.calls) == 2
    assert "Compare all columns" in runtime.calls[-1].prompt
    assert "Compare price only" in runtime.calls[-1].prompt


async def wait_status(api, run_id, status, timeout=5):
    for _ in range(int(timeout / 0.01)):
        run = await api("runs.get", run_id=run_id)
        if run["status"] == status:
            return run
        await asyncio.sleep(0.01)
    raise AssertionError(f"run stuck at {run['status']}, wanted {status}")


async def turn(api, cid, prompt="hi", **kw):
    return await api("runs.start_turn", conversation_id=cid, engine="codex", model_id="m", prompt=prompt, **kw)


@pytest.mark.asyncio
async def test_happy_path_persists_reply_and_ordered_events(rig):
    api, rt = rig
    cid = await new_conv(api)
    run = await turn(api, cid, "hello there")
    assert run["status"] == "queued" and run["kind"] == "conversation" and run["engine"] == "codex"
    await rt.started.wait()
    assert (await api("runs.get", run_id=run["id"]))["status"] == "running"
    rt.gate.set()
    done = await wait_status(api, run["id"], "succeeded")
    assert done["assistant_content"] == "hello" and done["assistant_message_seq"] > done["user_message_seq"]
    msgs = (await api("conversations.messages", conversation_id=cid))["messages"]
    assert [(m["role"], m["content"]) for m in msgs] == [("user", "hello there"), ("assistant", "hello")]
    events = (await api("runs.events", run_id=run["id"]))["events"]
    assert [e["type"] for e in events] == ["queued", "running", "succeeded"]
    ids = [e["id"] for e in events]
    assert ids == sorted(ids) and len(set(ids)) == 3
    assert events[-1]["data"]["assistant_message_seq"] == done["assistant_message_seq"]
    # replay after a seen id never repeats
    later = (await api("runs.events", run_id=run["id"], after_id=ids[0]))["events"]
    assert [e["type"] for e in later] == ["running", "succeeded"]
    # the usage ledger recorded the call and links it to the run
    usage = (await api("usage.events"))["events"]
    assert usage[0]["run_id"] == run["id"] and usage[0]["kind"] == "conversation_run" and usage[0]["success"]


@pytest.mark.asyncio
async def test_prompt_contains_bounded_history_and_user_message_is_stored_raw(rig):
    api, rt = rig
    rt.gated = False
    cid = await new_conv(api)
    r1 = await turn(api, cid, "first question")
    await wait_status(api, r1["id"], "succeeded")
    r2 = await turn(api, cid, "second question")
    await wait_status(api, r2["id"], "succeeded")
    assert rt.calls[0].prompt == "first question"
    assert "Previous conversation (JSON)" in rt.calls[1].prompt
    assert "first question" in rt.calls[1].prompt and "Current user message:\nsecond question" in rt.calls[1].prompt
    msgs = (await api("conversations.messages", conversation_id=cid))["messages"]
    assert msgs[2]["content"] == "second question"  # stored without the history wrapper


@pytest.mark.asyncio
async def test_one_active_run_per_conversation(rig):
    api, rt = rig
    cid = await new_conv(api)
    first = await turn(api, cid)
    err = await api.fail("runs.start_turn", conversation_id=cid, engine="codex", model_id="m", prompt="x")
    assert err.status == 409 and "active run" in err.message
    other = await new_conv(api, "other")
    assert (await turn(api, other))["status"] == "queued"  # a different conversation is independent
    assert len((await api("conversations.messages", conversation_id=cid))["messages"]) == 1
    rt.gate.set()
    await wait_status(api, first["id"], "succeeded")
    assert (await turn(api, cid))["id"] != first["id"]


@pytest.mark.asyncio
async def test_request_key_is_idempotent_and_conflicts_on_different_input(rig):
    api, rt = rig
    rt.gated = False
    cid = await new_conv(api)
    a = await turn(api, cid, "same", request_key="k1")
    b = await turn(api, cid, "same", request_key="k1")
    assert a["id"] == b["id"]
    await wait_status(api, a["id"], "succeeded")
    assert (await turn(api, cid, "same", request_key="k1"))["id"] == a["id"]  # still idempotent when done
    assert len(rt.calls) == 1
    assert (await api.fail("runs.start_turn", conversation_id=cid, engine="codex", model_id="m",
                           prompt="different", request_key="k1")).status == 409
    assert (await api.fail("runs.start_turn", conversation_id=cid, engine="codex", model_id="other",
                           prompt="same", request_key="k1")).status == 409


@pytest.mark.asyncio
async def test_cancel_running_run_aborts_engine_and_discards_late_result(make_app):
    rt = FakeRuntime(text="LATE", ignore_abort=True)
    api = Api(make_app(runtime_factory=lambda e: rt, mode="real"))
    cid = await new_conv(api)
    run = await turn(api, cid)
    await rt.started.wait()
    cancelled = await api("runs.cancel", run_id=run["id"])
    assert cancelled["status"] == "cancelled" and cancelled["error"] == "Cancelled by user."
    assert rt.aborted == [f"conv-run-{run['id']}"]
    rt.gate.set()  # the engine still produces a late result
    await asyncio.sleep(0.1)
    final = await api("runs.get", run_id=run["id"])
    assert final["status"] == "cancelled" and final["assistant_content"] is None
    msgs = (await api("conversations.messages", conversation_id=cid))["messages"]
    assert [m["role"] for m in msgs] == ["user"]  # no assistant message was written
    types = [e["type"] for e in (await api("runs.events", run_id=run["id"]))["events"]]
    assert types[-1] == "cancelled" and "succeeded" not in types
    # a new turn is possible again after cancel
    assert (await turn(api, cid))["status"] == "queued"


@pytest.mark.asyncio
async def test_cancel_queued_run_never_starts(make_app):
    rt = FakeRuntime()
    api = Api(make_app(runtime_factory=lambda e: rt, mode="real", max_concurrent=1))
    c1, c2 = await new_conv(api, "1"), await new_conv(api, "2")
    first = await turn(api, c1)
    await rt.started.wait()
    second = await turn(api, c2)  # waits for the single slot
    assert (await api("runs.get", run_id=second["id"]))["status"] == "queued"
    assert (await api("runs.cancel", run_id=second["id"]))["status"] == "cancelled"
    rt.gate.set()
    await wait_status(api, first["id"], "succeeded")
    await asyncio.sleep(0.05)
    assert len(rt.calls) == 1 and (await api("runs.get", run_id=second["id"]))["status"] == "cancelled"


@pytest.mark.asyncio
async def test_cancel_finished_run_is_conflict_and_unknown_is_404(rig):
    api, rt = rig
    rt.gated = False
    cid = await new_conv(api)
    run = await turn(api, cid)
    await wait_status(api, run["id"], "succeeded")
    err = await api.fail("runs.cancel", run_id=run["id"])
    assert err.status == 409 and "succeeded" in err.message
    assert (await api.fail("runs.cancel", run_id="nope")).status == 404
    assert (await api.fail("runs.get", run_id="nope")).status == 404


@pytest.mark.asyncio
async def test_engine_failure_is_recorded_without_leaking_details(make_app):
    secret = "sk-SECRET-123"
    rt = FakeRuntime(fail=RuntimeErrorBase(f"boom {secret}"), gated=False)
    api = Api(make_app(runtime_factory=lambda e: rt, mode="real"))
    cid = await new_conv(api)
    run = await turn(api, cid)
    done = await wait_status(api, run["id"], "failed")
    assert done["error"] == "Engine request failed (runtime_error)."
    assert secret not in json.dumps(await api("runs.events", run_id=run["id"]))
    assert secret not in json.dumps(await api("usage.events"))
    assert [m["role"] for m in (await api("conversations.messages", conversation_id=cid))["messages"]] == ["user"]


@pytest.mark.asyncio
async def test_validation_and_unsupported_engine(rig):
    api, _ = rig
    cid = await new_conv(api)
    assert (await api.fail("runs.start_turn", conversation_id="missing", engine="codex", model_id="m",
                           prompt="x")).status == 404
    # "mock" is only available in explicit mock mode (this rig runs in "real")
    assert (await api.fail("runs.start_turn", conversation_id=cid, engine="mock", model_id="m",
                           prompt="x")).status == 422
    assert (await api.fail("runs.start_turn", conversation_id=cid, engine="gemini", model_id="m",
                           prompt="x")).status == 422
    assert (await api.fail("runs.start_turn", conversation_id=cid, engine="codex", model_id="m",
                           prompt="")).status == 422
    assert (await api.fail("runs.start_turn", conversation_id=cid, engine="codex", model_id="m",
                           prompt="x", timeout=1)).status == 422
    assert (await api.fail("runs.start_turn", conversation_id=cid, engine="codex", model_id="m", prompt="x",
                           skills=[{"source_id": "a"}])).status == 422


@pytest.mark.asyncio
async def test_too_many_pending_runs(make_app):
    rt = FakeRuntime()
    api = Api(make_app(runtime_factory=lambda e: rt, mode="real", max_pending=1))
    c1, c2 = await new_conv(api, "1"), await new_conv(api, "2")
    await turn(api, c1)
    err = await api.fail("runs.start_turn", conversation_id=c2, engine="codex", model_id="m", prompt="x")
    assert err.status == 429
    assert (await api("conversations.messages", conversation_id=c2))["messages"] == []  # nothing persisted


@pytest.mark.asyncio
async def test_restart_marks_active_runs_interrupted_and_never_reruns(make_app, tmp_path):
    from backend.pyworker.app import Application
    from backend.pyworker.runtime import ledger

    rt = FakeRuntime()
    app = make_app(runtime_factory=lambda e: rt, mode="real")
    api = Api(app)
    cid = await new_conv(api)
    run = await turn(api, cid)
    await rt.started.wait()
    path = app.ctx.db.path
    # Simulate a hard crash: tasks die, the row stays "running".
    for t in list(app.ctx.runs._tasks.values()):
        t.cancel()
    await asyncio.sleep(0.05)
    assert (await api("runs.get", run_id=run["id"]))["status"] == "running"
    ledger.bind(None)
    app.ctx.db.close()

    rt2 = FakeRuntime(gated=False)
    app2 = Application.create(db_path=path, runtime_factory=lambda e: rt2)
    try:
        assert app2.interrupted_runs == 1
        api2 = Api(app2)
        got = await api2("runs.get", run_id=run["id"])
        assert got["status"] == "interrupted" and "not re-executed" in got["error"]
        await asyncio.sleep(0.05)
        assert rt2.calls == []
        types = [e["type"] for e in (await api2("runs.events", run_id=run["id"]))["events"]]
        assert types[-1] == "interrupted"
    finally:
        ledger.bind(None)
        app2.ctx.db.close()


@pytest.mark.asyncio
async def test_iter_events_replays_then_tails_without_duplicates(rig):
    api, rt = rig
    cid = await new_conv(api)
    run = await turn(api, cid)
    mgr = api.app.ctx.runs
    seen = []

    async def consume():
        async for ev in mgr.iter_events(run["id"], 0):
            if ev is not None:
                seen.append(ev)

    task = asyncio.create_task(consume())
    await asyncio.sleep(0.05)  # replayed "queued" (+ maybe "running")
    rt.gate.set()
    await asyncio.wait_for(task, 5)
    assert [e["type"] for e in seen] == ["queued", "running", "succeeded"]
    assert len({e["id"] for e in seen}) == 3
    # resuming after the terminal event yields nothing; mid-stream resume replays only the rest
    rest = [e async for e in mgr.iter_events(run["id"], seen[0]["id"]) if e is not None]
    assert [e["type"] for e in rest] == ["running", "succeeded"]
    assert [e async for e in mgr.iter_events(run["id"], seen[-1]["id"])] == []


@pytest.mark.asyncio
async def test_slow_subscriber_overflows_and_replays_from_db(make_app):
    rt = FakeRuntime(gated=False)
    app = make_app(runtime_factory=lambda e: rt, mode="real", subscriber_queue_size=1)
    api = Api(app)
    cid = await new_conv(api)
    run = await turn(api, cid)
    mgr = app.ctx.runs
    await wait_status(api, run["id"], "succeeded")
    # a consumer that attaches late is fully served from the durable log
    got = [e async for e in mgr.iter_events(run["id"], 0) if e is not None]
    assert [e["type"] for e in got] == ["queued", "running", "succeeded"]


@pytest.mark.asyncio
async def test_conversation_messages_are_idempotent_and_paginated(rig):
    api, _ = rig
    cid = await new_conv(api)
    m1 = await api("conversations.add_message", conversation_id=cid, role="user", content="a",
                   client_message_id="x1")
    again = await api("conversations.add_message", conversation_id=cid, role="user", content="a",
                      client_message_id="x1")
    assert again["seq"] == m1["seq"]
    assert (await api.fail("conversations.add_message", conversation_id=cid, role="user", content="b",
                           client_message_id="x1")).status == 409
    for i in range(5):
        await api("conversations.add_message", conversation_id=cid, role="assistant", content=f"r{i}")
    page = (await api("conversations.messages", conversation_id=cid, limit=3))["messages"]
    assert len(page) == 3
    rest = (await api("conversations.messages", conversation_id=cid, after=page[-1]["seq"]))["messages"]
    assert [m["content"] for m in rest] == ["r2", "r3", "r4"]
    assert (await api.fail("conversations.messages", conversation_id=cid, limit=0)).status == 422
    assert (await api.fail("conversations.messages", conversation_id="nope")).status == 404
    assert (await api.fail("conversations.add_message", conversation_id=cid, role="system", content="x")).status == 422
    assert (await api.fail("conversations.create", title="t", workspace_id="nope")).status == 404


@pytest.mark.asyncio
async def test_import_legacy_ceo_history_once(rig):
    api, _ = rig
    for i, role in enumerate(["user", "assistant"]):
        api.db.insert("ceo_messages", {"id": f"m{i}", "seq": i + 1, "role": role, "content": f"c{i}",
                                       "fast": 0, "created_at": "2026-01-01 00:00:00.000000"})
    first = await api("conversations.import_ceo")
    assert first == {"conversation_id": "legacy-ceo-history", "imported": 2}
    assert (await api("conversations.import_ceo"))["imported"] == 0
    msgs = (await api("conversations.messages", conversation_id="legacy-ceo-history"))["messages"]
    assert [m["content"] for m in msgs] == ["c0", "c1"]
    assert api.db.scalar("SELECT COUNT(*) FROM ceo_messages") == 2  # original history retained
