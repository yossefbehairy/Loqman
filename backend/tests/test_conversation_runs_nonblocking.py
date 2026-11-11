"""The event loop must stay responsive while blocking prep/DB work runs."""
import asyncio
import threading
import time

import httpx
import pytest
from fastapi import FastAPI

from backend.app.api.routes import conversation_runs as routes
from backend.app.core.conversation_runs import ConversationRunManager, TooManyRuns
from backend.app.db.models import ConversationRunEvent
from backend.tests.test_conversation_runs import (  # noqa: F401  (factory is a fixture)
    FakeRuntime, factory, make, messages, new_conv, wait_status,
)

BLOCK = 0.6


class Ticker:
    """Counts loop iterations; a blocked loop cannot tick."""

    def __init__(self):
        self.ticks, self._task = 0, None

    async def _run(self):
        while True:
            await asyncio.sleep(0.01)
            self.ticks += 1

    async def __aenter__(self):
        self._task = asyncio.get_running_loop().create_task(self._run())
        return self

    async def __aexit__(self, *exc):
        self._task.cancel()


def slow(fn, seen_threads):
    def wrapper(*a, **kw):
        seen_threads.append(threading.current_thread())
        time.sleep(BLOCK)  # deliberately blocking (stands in for slow SQLite / file reads)
        return fn(*a, **kw)
    return wrapper


def test_start_turn_async_does_not_block_loop(factory):
    async def go():
        rt = FakeRuntime(gated=False)
        mgr = make(factory, {"codex": rt})
        threads = []
        mgr._prepare_turn = slow(mgr._prepare_turn, threads)
        cid = new_conv(factory)
        async with Ticker() as t:
            snap = await mgr.start_turn_async(cid, "codex", "m", "hi")
        assert t.ticks >= int(BLOCK / 0.01) * 0.5, t.ticks  # loop kept ticking during the block
        assert threads and threads[0] is not threading.main_thread()
        run = await wait_status(mgr, snap["id"], "succeeded")
        assert messages(factory, cid) == [("user", "hi"), ("assistant", "hello")]
        assert not mgr._reserved
    asyncio.run(go())


def test_execute_and_cancel_db_calls_do_not_block_loop(factory):
    async def go():
        rt = FakeRuntime(gated=True)
        mgr = make(factory, {"codex": rt})
        threads = []
        mgr._transition_db = slow(mgr._transition_db, threads)
        mgr._complete_db = slow(mgr._complete_db, threads)
        mgr._cancel_db = slow(mgr._cancel_db, threads)
        a = await mgr.start_turn_async(new_conv(factory, "a"), "codex", "m", "p")
        async with Ticker() as t:
            await asyncio.wait_for(rt.started.wait(), 5)  # queued->running transition slept in a thread
            rt.gate.set()
            await wait_status(mgr, a["id"], "succeeded", timeout=5)  # complete slept in a thread
        assert t.ticks >= int(2 * BLOCK / 0.01) * 0.5, t.ticks
        b = await mgr.start_turn_async(new_conv(factory, "b"), "codex", "m", "p")
        async with Ticker() as t:
            run = await mgr.cancel(b["id"])
        assert run["status"] == "cancelled" and t.ticks >= int(BLOCK / 0.01) * 0.5
        assert all(th is not threading.main_thread() for th in threads)
    asyncio.run(go())


def test_http_requests_stay_responsive_during_blocked_prep(factory):
    async def go():
        mgr = make(factory, {"codex": FakeRuntime(gated=True)})
        mgr._prepare_turn = slow(mgr._prepare_turn, [])
        app = FastAPI()
        app.include_router(routes.router)
        app.dependency_overrides[routes.get_run_manager] = lambda: mgr
        cid = new_conv(factory)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            post = asyncio.ensure_future(c.post(f"/api/conversations/{cid}/turns",
                                                json={"engine": "codex", "model_id": "m", "prompt": "hi"}))
            await asyncio.sleep(0.1)  # prep is now sleeping in its thread
            started = time.monotonic()
            r = await c.get("/api/conversation-runs/nope")  # needs the loop; must not wait for the block
            assert r.status_code == 404 and time.monotonic() - started < BLOCK / 2
            assert not post.done()
            resp = await post
            assert resp.status_code == 202
            await mgr.cancel(resp.json()["id"])
    asyncio.run(go())


def test_pending_cap_holds_under_concurrent_async_starts(factory):
    async def go():
        mgr = make(factory, {"codex": FakeRuntime(gated=True)}, max_pending=3, max_concurrent=1)
        cids = [new_conv(factory, str(i)) for i in range(8)]
        results = await asyncio.gather(*(mgr.start_turn_async(c, "codex", "m", "p") for c in cids),
                                       return_exceptions=True)
        ok = [r for r in results if isinstance(r, dict)]
        assert len(ok) == 3 and sum(isinstance(r, TooManyRuns) for r in results) == 5
        assert len(mgr._tasks) == 3 and mgr._reserved == 0
        await mgr.shutdown()
    asyncio.run(go())


def test_idempotent_retry_not_blocked_by_cap_and_single_run(factory):
    async def go():
        mgr = make(factory, {"codex": FakeRuntime(gated=True)}, max_pending=1)
        cid = new_conv(factory)
        first, again = await asyncio.gather(
            mgr.start_turn_async(cid, "codex", "m", "p", request_key="k"),
            mgr.start_turn_async(cid, "codex", "m", "p", request_key="k"),
            return_exceptions=True)
        for r in (first, again):  # exactly one run; the loser either dedupes or is capped
            assert isinstance(r, (dict, TooManyRuns)), r
        assert len(mgr._tasks) == 1 and mgr._reserved == 0
        retry = await mgr.start_turn_async(cid, "codex", "m", "p", request_key="k")  # cap full, still idempotent
        assert retry["id"] == (first if isinstance(first, dict) else again)["id"]
        await mgr.shutdown()
    asyncio.run(go())


def test_events_ordered_and_cancel_atomic_with_threaded_db(factory):
    async def go():
        rt = FakeRuntime(gated=True)
        mgr = make(factory, {"codex": rt})
        snap = await mgr.start_turn_async(new_conv(factory), "codex", "m", "p")
        await asyncio.wait_for(rt.started.wait(), 5)
        cancelled = await mgr.cancel(snap["id"])
        rt.gate.set()
        await asyncio.sleep(0.1)
        assert cancelled["status"] == "cancelled"
        assert (await asyncio.to_thread(mgr.get_run, snap["id"]))["status"] == "cancelled"
        types = [e["type"] for e in mgr.list_events(snap["id"])]
        assert types == ["queued", "running", "cancelled"]
        ids = [e["id"] for e in mgr.list_events(snap["id"])]
        assert ids == sorted(ids)
    asyncio.run(go())


def test_caller_cancellation_after_commit_still_schedules_task(factory):
    async def go():
        rt = FakeRuntime(gated=True)
        mgr = make(factory, {"codex": rt})
        mgr._prepare_turn = slow(mgr._prepare_turn, [])
        caller = asyncio.ensure_future(mgr.start_turn_async(new_conv(factory), "codex", "m", "p"))
        await asyncio.sleep(0.1)
        caller.cancel()  # client disconnect mid-prep
        with pytest.raises(asyncio.CancelledError):
            await caller
        await asyncio.wait_for(rt.started.wait(), 5)  # run still got scheduled and executed
        await mgr.shutdown()
    asyncio.run(go())
