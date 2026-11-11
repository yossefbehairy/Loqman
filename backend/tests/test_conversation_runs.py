"""Conversation run manager tests: fake runtimes, temp-file SQLite, no model calls."""
import asyncio

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from backend.app.api.routes import conversation_runs as routes
from backend.app.core.conversation_runs import (
    ActiveRunExists, ConversationNotFound, ConversationRunManager, TooManyRuns,
)
from backend.app.db.models import Conversation, ConversationMessage, ConversationRun
from backend.app.db.session import Base
from backend.app.runtime.base import AgentResult
from backend.app.runtime.errors import RuntimeCancelled, RuntimeErrorBase


class FakeRuntime:
    """Blocks on a gate; abort_task releases it with RuntimeCancelled like the CLI adapters."""

    def __init__(self, text="hello", fail=None, gated=True):
        self.text, self.fail, self.gated = text, fail, gated
        self.gate = asyncio.Event()
        self.started = asyncio.Event()
        self.calls, self.aborted, self.cancelled_in_flight = [], [], False
        self._abort = False

    async def execute_task(self, task):
        self.calls.append(task)
        self.started.set()
        if self.gated:
            try:
                await self.gate.wait()
            except asyncio.CancelledError:
                self.cancelled_in_flight = True
                raise
        if self._abort:
            raise RuntimeCancelled("cancelled")
        if self.fail:
            raise self.fail
        return AgentResult(text=self.text, model_id=task.model_id)

    async def abort_task(self, session_id):
        self.aborted.append(session_id)
        self._abort = True
        self.gate.set()


@pytest.fixture
def factory(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path / 'runs.db'}", connect_args={"check_same_thread": False})

    @event.listens_for(eng, "connect")
    def _pragma(conn, _):
        cur = conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON;")
        cur.execute("PRAGMA busy_timeout=5000;")
        cur.close()

    Base.metadata.create_all(bind=eng)
    sf = sessionmaker(autocommit=False, autoflush=False, bind=eng)
    yield sf
    eng.dispose()


def make(factory, runtimes, **kw):
    return ConversationRunManager(session_factory=factory, runtime_factory=lambda e: runtimes[e], **kw)


def new_conv(factory, title="c"):
    db = factory()
    c = Conversation(title=title)
    db.add(c)
    db.commit()
    cid = c.id
    db.close()
    return cid


async def wait_status(mgr, run_id, *statuses, timeout=3):
    for _ in range(int(timeout / 0.01)):
        run = mgr.get_run(run_id)
        if run["status"] in statuses:
            return run
        await asyncio.sleep(0.01)
    raise AssertionError(f"status stuck at {mgr.get_run(run_id)['status']}")


def messages(factory, cid):
    db = factory()
    try:
        return [(m.role, m.content) for m in
                db.query(ConversationMessage).filter_by(conversation_id=cid).order_by(ConversationMessage.seq)]
    finally:
        db.close()


def test_success_persists_reply_and_passes_task(factory):
    async def go():
        rt = FakeRuntime(text="answer")
        mgr = make(factory, {"claude-code": rt})
        cid = new_conv(factory)
        snap = mgr.start_turn(cid, "claude-code", "sonnet", "hi", 30)
        assert snap["status"] == "queued" and snap["user_message_seq"]
        rt.gate.set()
        run = await wait_status(mgr, snap["id"], "succeeded")
        assert run["assistant_content"] == "answer"
        assert messages(factory, cid) == [("user", "hi"), ("assistant", "answer")]
        t = rt.calls[0]
        assert (t.model_id, t.provider_id, t.timeout, t.prompt) == ("sonnet", "anthropic", 30, "hi")
        assert t.session_title == f"conv-run-{snap['id']}"
    asyncio.run(go())


def test_codex_engine_and_failure(factory):
    async def go():
        rt = FakeRuntime(fail=RuntimeErrorBase("boom"), gated=False)
        mgr = make(factory, {"codex": rt})
        cid = new_conv(factory)
        snap = mgr.start_turn(cid, "codex", "gpt", "p")
        run = await wait_status(mgr, snap["id"], "failed")
        assert "runtime_error" in run["error"] and run["assistant_message_seq"] is None
        assert rt.calls[0].provider_id == "openai"
        assert messages(factory, cid) == [("user", "p")]
        # failed run frees the conversation
        mgr.start_turn(cid, "codex", "gpt", "again")
    asyncio.run(go())


def test_later_turn_uses_prior_conversation_and_opencode(factory):
    async def go():
        rt = FakeRuntime(text="answer", gated=False)
        mgr = make(factory, {"opencode": rt})
        cid = new_conv(factory)
        first = mgr.start_turn(cid, "opencode", "m", "first")
        await wait_status(mgr, first["id"], "succeeded")
        second = mgr.start_turn(cid, "opencode", "m", "follow up")
        await wait_status(mgr, second["id"], "succeeded")
        assert rt.calls[1].provider_id == "opencode"
        assert '"content": "first"' in rt.calls[1].prompt
        assert '"content": "answer"' in rt.calls[1].prompt
        assert rt.calls[1].prompt.endswith("follow up")
    asyncio.run(go())


def test_request_key_prevents_duplicate_turns(factory):
    async def go():
        rt = FakeRuntime(gated=True)
        mgr = make(factory, {"codex": rt})
        cid = new_conv(factory)
        first = mgr.start_turn(cid, "codex", "model", "hello", variant="high", request_key="key-1")
        repeated = mgr.start_turn(cid, "codex", "model", "hello", variant="high", request_key="key-1")
        assert repeated["id"] == first["id"]
        with pytest.raises(Exception, match="key-1"):
            mgr.start_turn(cid, "codex", "model", "different", variant="high", request_key="key-1")
        rt.gate.set()
        await wait_status(mgr, first["id"], "succeeded")
        assert rt.calls[0].variant == "high"
        assert len(rt.calls) == 1
        assert len(messages(factory, cid)) == 2
    asyncio.run(go())


def test_mock_engine_requires_explicit_mock_mode(factory, monkeypatch):
    from backend.app.config import settings

    async def go():
        rt = FakeRuntime(gated=False)
        mgr = make(factory, {"mock": rt})
        cid = new_conv(factory)
        monkeypatch.setattr(settings, "RUNTIME_MODE", "real")
        with pytest.raises(Exception):
            mgr.start_turn(cid, "mock", "simulated", "hello")
        monkeypatch.setattr(settings, "RUNTIME_MODE", "mock")
        run = mgr.start_turn(cid, "mock", "simulated", "hello")
        assert (await wait_status(mgr, run["id"], "succeeded"))["assistant_content"] == "hello"
    asyncio.run(go())


def test_antigravity_turn_uses_its_provider(factory):
    async def go():
        rt = FakeRuntime(gated=False)
        mgr = make(factory, {"antigravity": rt})
        run = await mgr.start_turn_async(new_conv(factory), "antigravity", "model", "hello")
        assert (await wait_status(mgr, run["id"], "succeeded"))["engine"] == "antigravity"
        assert rt.calls[0].provider_id == "antigravity"
    asyncio.run(go())


def test_one_active_run_per_conversation_under_concurrency(factory):
    async def go():
        rt = FakeRuntime()
        mgr = make(factory, {"claude-code": rt})
        cid = new_conv(factory)
        # Real thread contention on the DB constraint (start_turn needs the loop, so use the DB layer directly).
        def insert(i):
            db = factory()
            try:
                db.add(ConversationRun(conversation_id=cid, engine="codex", model_id="m", status="queued"))
                db.commit()
                return True
            except Exception:
                db.rollback()
                return False
            finally:
                db.close()
        outcomes = await asyncio.gather(*[asyncio.to_thread(insert, i) for i in range(8)])
        assert sum(outcomes) == 1
        # manager path: second turn rejected, and its user message rolled back
        before = messages(factory, cid)
        with pytest.raises(ActiveRunExists):
            mgr.start_turn(cid, "claude-code", "m", "second")
        assert messages(factory, cid) == before
    asyncio.run(go())


def test_second_turn_rejected_then_allowed_after_finish(factory):
    async def go():
        rt = FakeRuntime()
        mgr = make(factory, {"claude-code": rt})
        cid = new_conv(factory)
        first = mgr.start_turn(cid, "claude-code", "m", "one")
        with pytest.raises(ActiveRunExists):
            mgr.start_turn(cid, "claude-code", "m", "two")
        rt.gate.set()
        await wait_status(mgr, first["id"], "succeeded")
        mgr.start_turn(cid, "claude-code", "m", "three")
        assert [m for m in messages(factory, cid) if m[0] == "user"] == [("user", "one"), ("user", "three")]
    asyncio.run(go())


def test_unknown_conversation_and_engine(factory):
    async def go():
        mgr = make(factory, {})
        with pytest.raises(ConversationNotFound):
            mgr.start_turn("nope", "codex", "m", "p")
        with pytest.raises(Exception):
            mgr.start_turn(new_conv(factory), "gemini", "m", "p")
    asyncio.run(go())


def test_cancel_running_never_becomes_success(factory):
    async def go():
        rt = FakeRuntime(text="late")
        mgr = make(factory, {"claude-code": rt})
        cid = new_conv(factory)
        snap = mgr.start_turn(cid, "claude-code", "m", "p")
        await asyncio.wait_for(rt.started.wait(), 3)
        run = await mgr.cancel(snap["id"])
        assert run["status"] == "cancelled"
        assert rt.aborted == [f"conv-run-{snap['id']}"]
        rt.gate.set()
        await asyncio.sleep(0.05)
        run = mgr.get_run(snap["id"])
        assert run["status"] == "cancelled" and run["assistant_message_seq"] is None
        assert messages(factory, cid) == [("user", "p")]
        assert not mgr._tasks
        mgr.start_turn(cid, "claude-code", "m", "next")  # conversation is free again
    asyncio.run(go())


def test_cancel_cancels_asyncio_task_when_adapter_ignores_abort(factory):
    async def go():
        rt = FakeRuntime()

        async def no_abort(_):  # adapter abort is a no-op; only task cancellation can stop it
            rt.aborted.append(_)
        rt.abort_task = no_abort
        mgr = make(factory, {"claude-code": rt}, abort_grace=2)
        snap = mgr.start_turn(new_conv(factory), "claude-code", "m", "p")
        await asyncio.wait_for(rt.started.wait(), 3)
        run = await mgr.cancel(snap["id"])
        assert run["status"] == "cancelled" and rt.cancelled_in_flight
    asyncio.run(go())


def test_result_racing_cancel_loses(factory):
    """Result is ready but cancel committed first: conditional update must refuse success."""
    async def go():
        rt = FakeRuntime(gated=False)
        mgr = make(factory, {"codex": rt})
        cid = new_conv(factory)
        snap = mgr.start_turn(cid, "codex", "m", "p")
        await wait_status(mgr, snap["id"], "succeeded")
        # Deterministic: running->succeeded cannot be replayed over a cancelled row
        db = factory()
        db.query(ConversationRun).filter_by(id=snap["id"]).update({"status": "cancelled"})
        db.commit()
        db.close()
        assert mgr._complete(snap["id"], "late") is False
        assert [m for m in messages(factory, cid) if m[0] == "assistant"] == [("assistant", "hello")]
    asyncio.run(go())


def test_cancel_queued_run_waiting_for_slot(factory):
    async def go():
        rt = FakeRuntime()
        mgr = make(factory, {"claude-code": rt}, max_concurrent=1)
        a = mgr.start_turn(new_conv(factory, "a"), "claude-code", "m", "pa")
        b = mgr.start_turn(new_conv(factory, "b"), "claude-code", "m", "pb")
        await asyncio.wait_for(rt.started.wait(), 3)
        assert mgr.get_run(b["id"])["status"] == "queued"  # bounded: only one executing
        assert len(rt.calls) == 1
        run = await mgr.cancel(b["id"])
        assert run["status"] == "cancelled" and rt.aborted == []
        rt.gate.set()
        await wait_status(mgr, a["id"], "succeeded")
        assert len(rt.calls) == 1  # cancelled queued run never executed
    asyncio.run(go())


def test_pending_cap(factory):
    async def go():
        rt = FakeRuntime()
        mgr = make(factory, {"codex": rt}, max_pending=1)
        mgr.start_turn(new_conv(factory, "a"), "codex", "m", "p")
        with pytest.raises(TooManyRuns):
            mgr.start_turn(new_conv(factory, "b"), "codex", "m", "p")
        await mgr.shutdown()
    asyncio.run(go())


def test_startup_marks_active_interrupted_without_rerun(factory):
    async def go():
        cid = new_conv(factory)
        db = factory()
        ids = []
        for st, conv in (("queued", cid), ("running", new_conv(factory, "x")), ("succeeded", new_conv(factory, "y"))):
            r = ConversationRun(conversation_id=conv, engine="codex", model_id="m", status=st)
            db.add(r)
            db.flush()
            ids.append(r.id)
        db.commit()
        db.close()
        rt = FakeRuntime(gated=False)
        mgr = make(factory, {"codex": rt})
        assert mgr.recover_interrupted() == 2
        await asyncio.sleep(0.05)
        assert [mgr.get_run(i)["status"] for i in ids] == ["interrupted", "interrupted", "succeeded"]
        assert rt.calls == []
        mgr.start_turn(cid, "codex", "m", "fresh")  # conversation usable again
    asyncio.run(go())


def test_shutdown_leaves_run_active_for_recovery(factory):
    async def go():
        rt = FakeRuntime()
        mgr = make(factory, {"codex": rt})
        snap = mgr.start_turn(new_conv(factory), "codex", "m", "p")
        await asyncio.wait_for(rt.started.wait(), 3)
        await mgr.shutdown()
        assert mgr.get_run(snap["id"])["status"] == "running"
        assert mgr.recover_interrupted() == 1
        assert mgr.get_run(snap["id"])["status"] == "interrupted"
    asyncio.run(go())


def test_http_api_flow(factory):
    async def go():
        rt = FakeRuntime(text="via http")
        mgr = make(factory, {"claude-code": rt})
        app = FastAPI()
        app.include_router(routes.router)
        app.dependency_overrides[routes.get_run_manager] = lambda: mgr
        cid = new_conv(factory)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            body = {"engine": "claude-code", "model_id": "sonnet", "prompt": "hi"}
            r = await c.post(f"/api/conversations/{cid}/turns", json=body)
            assert r.status_code == 202 and r.json()["status"] == "queued"
            rid = r.json()["id"]
            assert (await c.post(f"/api/conversations/{cid}/turns", json=body)).status_code == 409
            assert (await c.post("/api/conversations/zzz/turns", json=body)).status_code == 404
            assert (await c.post(f"/api/conversations/{cid}/turns", json={**body, "engine": "gemini"})).status_code == 422
            rt.gate.set()
            await wait_status(mgr, rid, "succeeded")
            got = await c.get(f"/api/conversation-runs/{rid}")
            assert got.json()["assistant_content"] == "via http"
            assert (await c.post(f"/api/conversation-runs/{rid}/cancel")).status_code == 409
            assert (await c.get("/api/conversation-runs/missing")).status_code == 404
            # cancel path
            rt2 = FakeRuntime()
            mgr._runtimes["claude-code"] = rt2
            r2 = await c.post(f"/api/conversations/{cid}/turns", json=body)
            await asyncio.wait_for(rt2.started.wait(), 3)
            cr = await c.post(f"/api/conversation-runs/{r2.json()['id']}/cancel")
            assert cr.status_code == 200 and cr.json()["status"] == "cancelled"
    asyncio.run(go())


# ---------------------------------------------------------------------------
# Durable run events + SSE replay
# ---------------------------------------------------------------------------
def event_rows(factory, run_id):
    from backend.app.db.models import ConversationRunEvent
    db = factory()
    try:
        return [(e.id, e.type) for e in
                db.query(ConversationRunEvent).filter_by(run_id=run_id).order_by(ConversationRunEvent.id)]
    finally:
        db.close()


async def collect(agen, limit=50):
    out = []
    async for ev in agen:
        if ev is not None:
            out.append(ev)
        if len(out) >= limit:
            break
    return out


def test_events_persisted_for_every_transition(factory):
    async def go():
        ok, bad, canc = FakeRuntime(), FakeRuntime(fail=RuntimeErrorBase("boom"), gated=False), FakeRuntime()
        mgr = make(factory, {"claude-code": ok, "codex": bad})
        a = mgr.start_turn(new_conv(factory, "a"), "claude-code", "m", "p")
        ok.gate.set()
        await wait_status(mgr, a["id"], "succeeded")
        b = mgr.start_turn(new_conv(factory, "b"), "codex", "m", "p")
        await wait_status(mgr, b["id"], "failed")
        mgr._runtimes["claude-code"] = canc
        c = mgr.start_turn(new_conv(factory, "c"), "claude-code", "m", "p")
        await asyncio.wait_for(canc.started.wait(), 3)
        await mgr.cancel(c["id"])
        d = ConversationRun(conversation_id=new_conv(factory, "d"), engine="codex", model_id="m", status="running")
        db = factory(); db.add(d); db.commit(); did = d.id; db.close()
        assert mgr.recover_interrupted() == 1
        assert [t for _, t in event_rows(factory, a["id"])] == ["queued", "running", "succeeded"]
        assert [t for _, t in event_rows(factory, b["id"])] == ["queued", "running", "failed"]
        assert [t for _, t in event_rows(factory, c["id"])] == ["queued", "running", "cancelled"]
        assert [t for _, t in event_rows(factory, did)] == ["interrupted"]
        ids = [i for r in (a, b, c) for i, _ in event_rows(factory, r["id"])]
        assert len(set(ids)) == len(ids)
        assert [i for i, _ in event_rows(factory, a["id"])] == sorted(i for i, _ in event_rows(factory, a["id"]))
        failed = mgr.list_events(b["id"])[-1]
        assert "runtime_error" in failed["data"]["error"]
    asyncio.run(go())


def test_lost_transition_writes_no_event(factory):
    """Event and transition share a transaction: a lost race leaves no phantom event."""
    async def go():
        mgr = make(factory, {"codex": FakeRuntime(gated=False)})
        snap = mgr.start_turn(new_conv(factory), "codex", "m", "p")
        await wait_status(mgr, snap["id"], "succeeded")
        before = event_rows(factory, snap["id"])
        assert mgr._transition(snap["id"], "running", "failed", error="x") is False
        assert mgr._complete(snap["id"], "late") is False
        assert event_rows(factory, snap["id"]) == before
    asyncio.run(go())


def test_event_rolled_back_with_failed_transaction(factory):
    async def go():
        mgr = make(factory, {"codex": FakeRuntime()})
        orig = mgr._add_event

        def boom(*a, **k):
            orig(*a, **k)
            raise RuntimeError("crash before commit")
        mgr._add_event = boom
        cid = new_conv(factory)
        with pytest.raises(RuntimeError):
            mgr.start_turn(cid, "codex", "m", "p")
        db = factory()
        assert db.query(ConversationRun).count() == 0  # run, message and event all rolled back
        assert messages(factory, cid) == []
        db.close()
    asyncio.run(go())


def test_replay_after_last_event_id_and_terminal_end(factory):
    async def go():
        rt = FakeRuntime(gated=False)
        mgr = make(factory, {"codex": rt})
        snap = mgr.start_turn(new_conv(factory), "codex", "m", "p")
        await wait_status(mgr, snap["id"], "succeeded")
        full = await collect(mgr.iter_events(snap["id"]))
        assert [e["type"] for e in full] == ["queued", "running", "succeeded"]
        tail = await collect(mgr.iter_events(snap["id"], full[0]["id"]))
        assert [e["type"] for e in tail] == ["running", "succeeded"]
        assert await collect(mgr.iter_events(snap["id"], full[-1]["id"])) == []  # ends, no hang
        assert not mgr._subscribers
    asyncio.run(go())


def test_live_tail_receives_events_then_ends(factory):
    async def go():
        rt = FakeRuntime(text="done")
        mgr = make(factory, {"codex": rt})
        snap = mgr.start_turn(new_conv(factory), "codex", "m", "p")
        await asyncio.wait_for(rt.started.wait(), 3)
        task = asyncio.create_task(collect(mgr.iter_events(snap["id"])))
        await asyncio.sleep(0.05)
        rt.gate.set()
        evs = await asyncio.wait_for(task, 3)
        assert [e["type"] for e in evs] == ["queued", "running", "succeeded"]
        assert evs[-1]["data"]["assistant_message_seq"] is not None
        assert "done" not in str(evs)  # lifecycle only, no reply text streamed
    asyncio.run(go())


def test_live_notifications_are_read_from_log_in_commit_order(factory):
    async def go():
        rt = FakeRuntime()
        mgr = make(factory, {"codex": rt})
        snap = mgr.start_turn(new_conv(factory), "codex", "m", "p")
        await asyncio.wait_for(rt.started.wait(), 3)
        stream = mgr.iter_events(snap["id"])
        try:
            assert (await anext(stream))["type"] == "queued"
            assert (await anext(stream))["type"] == "running"
            pending = asyncio.create_task(anext(stream))
            await asyncio.sleep(0.05)
            db = factory()
            first = mgr._add_event(db, snap["id"], "notice")
            second = mgr._add_event(db, snap["id"], "notice")
            db.commit()
            db.close()
            mgr._publish([second, first])
            assert (await asyncio.wait_for(pending, 3))["id"] == first["id"]
            assert (await asyncio.wait_for(anext(stream), 3))["id"] == second["id"]
        finally:
            await stream.aclose()
            await mgr.cancel(snap["id"])
    asyncio.run(go())


@pytest.mark.parametrize("commit_before_read", [True, False])
def test_no_gap_between_subscribe_and_replay(factory, commit_before_read):
    """A transition committed after subscribing but around the DB read is delivered exactly once."""
    async def go():
        rt = FakeRuntime()
        mgr = make(factory, {"codex": rt})
        snap = mgr.start_turn(new_conv(factory), "codex", "m", "p")
        await asyncio.wait_for(rt.started.wait(), 3)
        orig, fired = mgr._replay, []

        def racing(run_id, after_id, limit=500):
            if fired:
                return orig(run_id, after_id, limit)
            fired.append(1)
            if commit_before_read:
                mgr._complete(run_id, "x")  # in DB read AND in live queue -> dedupe
                return orig(run_id, after_id, limit)
            rows = orig(run_id, after_id, limit)  # DB read misses it -> only the queue has it
            mgr._complete(run_id, "x")
            return rows
        mgr._replay = racing
        evs = await asyncio.wait_for(collect(mgr.iter_events(snap["id"])), 3)
        assert [e["type"] for e in evs] == ["queued", "running", "succeeded"]
        assert len({e["id"] for e in evs}) == 3
    asyncio.run(go())


def test_slow_client_is_dropped_and_can_replay(factory):
    async def go():
        rt = FakeRuntime(gated=False)
        mgr = make(factory, {"codex": rt}, subscriber_queue_size=2)
        snap = mgr.start_turn(new_conv(factory), "codex", "m", "p")
        await wait_status(mgr, snap["id"], "succeeded")
        stored = mgr.list_events(snap["id"])
        # Fresh active run so the stream stays live; the consumer stalls while events pile up.
        rt2 = FakeRuntime()
        mgr._runtimes["codex"] = rt2
        cid = new_conv(factory, "slow")
        run = mgr.start_turn(cid, "codex", "m", "p")
        agen = mgr.iter_events(run["id"])
        first = await agen.__anext__()
        assert first["type"] == "queued"
        for n in range(1, 8):  # consumer not reading: bounded queue (2) must overflow
            mgr._publish([{"id": 10_000 + n, "run_id": run["id"], "type": "running", "data": {}}])
        got = []
        async for ev in agen:
            if ev is not None:
                got.append(ev)
        assert len(got) <= 2 + 1 and not mgr._subscribers  # dropped, memory bounded
        # Reconnect with the last seen id replays real persisted events from the DB.
        replay = await asyncio.wait_for(collect(mgr.iter_events(run["id"], first["id"]), limit=1), 3)
        assert [e["type"] for e in replay] == ["running"] and replay[0]["id"] > first["id"]
        assert stored  # sanity
        rt2.gate.set()
    asyncio.run(go())


def test_disconnect_unsubscribes(factory):
    async def go():
        rt = FakeRuntime()
        mgr = make(factory, {"codex": rt})
        snap = mgr.start_turn(new_conv(factory), "codex", "m", "p")
        agen = mgr.iter_events(snap["id"])
        await agen.__anext__()
        assert len(mgr._subscribers) == 1
        await agen.aclose()
        assert not mgr._subscribers
        await mgr.shutdown()
    asyncio.run(go())


def test_subscriber_cap(factory):
    async def go():
        rt = FakeRuntime()
        mgr = make(factory, {"codex": rt}, max_subscribers=1)
        snap = mgr.start_turn(new_conv(factory), "codex", "m", "p")
        a = mgr.iter_events(snap["id"])
        await a.__anext__()
        with pytest.raises(TooManyRuns):
            await mgr.iter_events(snap["id"]).__anext__()
        await a.aclose()
        await mgr.shutdown()
    asyncio.run(go())


def test_http_sse_replay_and_validation(factory):
    async def go():
        rt = FakeRuntime(gated=False)
        mgr = make(factory, {"codex": rt})
        app = FastAPI()
        app.include_router(routes.router)
        app.dependency_overrides[routes.get_run_manager] = lambda: mgr
        snap = mgr.start_turn(new_conv(factory), "codex", "m", "p")
        await wait_status(mgr, snap["id"], "succeeded")
        url = f"/api/conversation-runs/{snap['id']}/events"
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            r = await c.get(url)
            assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
            ids = [int(l[4:]) for l in r.text.splitlines() if l.startswith("id: ")]
            types = [l[7:] for l in r.text.splitlines() if l.startswith("event: ")]
            assert types == ["queued", "running", "succeeded"] and ids == sorted(ids)
            r2 = await c.get(url, headers={"Last-Event-ID": str(ids[0])})
            assert [l[7:] for l in r2.text.splitlines() if l.startswith("event: ")] == ["running", "succeeded"]
            r3 = await c.get(url, headers={"Last-Event-ID": str(ids[-1])})
            assert "event:" not in r3.text
            assert (await c.get(url, headers={"Last-Event-ID": "abc"})).status_code == 400
            assert (await c.get(url, headers={"Last-Event-ID": "-1"})).status_code == 400
            assert (await c.get("/api/conversation-runs/missing/events")).status_code == 404
    asyncio.run(go())
