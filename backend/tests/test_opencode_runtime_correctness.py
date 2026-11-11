"""OpenCode runtime correctness: timeout, cancellation mapping, argv size. No live calls."""
import asyncio
import sys

import pytest

from backend.app.runtime import opencode_runtime as oc
from backend.app.runtime.base import ExecuteAgentTask
from backend.app.runtime.errors import RuntimeCancelled, RuntimeErrorBase, RuntimeTimeout
from backend.app.runtime.opencode_runtime import RealOpenCodeRuntime


def _partial(text="par"):
    return {"data": [{"type": "assistant", "id": "m1", "time": {"created": 10}, "content": [{"type": "text", "text": text}]}]}


@pytest.fixture
def fast_clock(monkeypatch):
    """Make the 15s deadline floor elapse quickly: each time() call advances 10s."""
    state = {"t": oc.time.time()}

    def fake_time():
        state["t"] += 10
        return state["t"]

    monkeypatch.setattr(oc.time, "time", fake_time)
    real_sleep = asyncio.sleep

    async def short_sleep(_):
        await real_sleep(0)

    monkeypatch.setattr(oc.asyncio, "sleep", short_sleep)


@pytest.mark.asyncio
async def test_timeout_with_partial_text_is_not_success(fast_clock):
    r = RealOpenCodeRuntime()

    async def fake(method, path, data=None, timeout=90):
        return _partial()

    r._run_cli_api = fake
    with pytest.raises(RuntimeTimeout):
        await r._wait_for_assistant("ses1", 0, timeout=1)


@pytest.mark.asyncio
async def test_completed_reply_still_succeeds(fast_clock):
    r = RealOpenCodeRuntime()
    done = _partial("final")
    done["data"][0]["time"]["completed"] = 11

    async def fake(method, path, data=None, timeout=90):
        return done

    r._run_cli_api = fake
    msg = await r._wait_for_assistant("ses1", 0, timeout=30)
    assert r._assistant_text(msg) == "final"


def _task(title="conv-run-1"):
    return ExecuteAgentTask(model_id="m", prompt="hi", session_title=title, timeout=30)


def _wire(r, calls, on_poll):
    async def resolve(model_id):
        class E:
            provider_id = "opencode"
        return E()

    async def fake(method, path, data=None, timeout=90):
        calls.append((method, path))
        if method == "GET" and path == "/api/session":
            return {"data": []}
        if method == "POST" and path == "/api/session":
            return {"data": {"id": "ses-real"}}
        if path.endswith("/interrupt"):
            return {}
        if path.endswith("/message"):
            return await on_poll()
        return {}

    r.resolve_model = resolve
    r._run_cli_api = fake


@pytest.mark.asyncio
async def test_timeout_interrupts_real_session_and_raises(fast_clock):
    r = RealOpenCodeRuntime()
    calls: list = []

    async def poll():
        return _partial()

    _wire(r, calls, poll)
    with pytest.raises(RuntimeTimeout):
        await r.execute_task(_task("worker-1"))
    assert ("POST", "/api/session/ses-real/interrupt") in calls


@pytest.mark.asyncio
async def test_abort_by_title_maps_to_real_session_and_discards_late_reply():
    r = RealOpenCodeRuntime()
    calls: list = []
    polling = asyncio.Event()
    release = asyncio.Event()

    async def poll():
        polling.set()
        await release.wait()
        done = _partial("late answer")
        done["data"][0]["time"]["completed"] = 11
        return done

    _wire(r, calls, poll)
    t = asyncio.create_task(r.execute_task(_task("conv-run-1")))
    await polling.wait()
    await r.abort_task("conv-run-1")   # AgentOS key, not the OpenCode id
    release.set()                       # the "late response" now arrives
    with pytest.raises(RuntimeCancelled):
        await t
    assert ("POST", "/api/session/ses-real/interrupt") in calls
    assert not any("conv-run-1" in p for _, p in calls)  # title never used as a session id
    assert r._active == {}


@pytest.mark.asyncio
async def test_abort_before_session_known_cancels_run():
    r = RealOpenCodeRuntime()
    calls: list = []
    gate = asyncio.Event()

    async def resolve(model_id):
        class E:
            provider_id = "opencode"
        return E()

    async def fake(method, path, data=None, timeout=90):
        calls.append((method, path))
        if method == "POST" and path == "/api/session":
            await gate.wait()  # session creation in flight; id not yet known
            return {"data": {"id": "ses-early"}}
        return {}

    r.resolve_model = resolve
    r._run_cli_api = fake
    t = asyncio.create_task(r.execute_task(_task("conv-run-2")))
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    await r.abort_task("conv-run-2")  # nothing to interrupt yet; must only flag
    gate.set()
    with pytest.raises(RuntimeCancelled):
        await t
    assert ("POST", "/api/session/ses-early/interrupt") in calls


@pytest.mark.asyncio
async def test_abort_kills_running_cli_process():
    r = RealOpenCodeRuntime()
    run = oc._ActiveRun(key="k")
    r._active["k"] = [run]
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-c", "import time; time.sleep(60)",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    run.procs.add(proc)

    async def no_interrupt(sid):
        return True

    r._interrupt_session = no_interrupt
    await r.abort_task("k")
    await asyncio.wait_for(proc.wait(), 10)
    assert run.cancelled and proc.returncode is not None


@pytest.mark.asyncio
async def test_oversize_body_fails_closed_without_spawning(monkeypatch):
    r = RealOpenCodeRuntime()

    async def boom(*a, **k):
        raise AssertionError("must not spawn")

    monkeypatch.setattr(oc.asyncio, "create_subprocess_exec", boom)
    with pytest.raises(RuntimeErrorBase, match="too large"):
        await r._run_cli_api("POST", "/api/session/x/prompt", {"text": "a" * 40_000})
