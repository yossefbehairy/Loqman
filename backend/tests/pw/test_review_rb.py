"""R-B regressions: deterministic fake engines only; no model/CLI calls."""

import asyncio
import json

import pytest

from backend.pyworker.core import conversation_runs as runs
from backend.pyworker.db.core import new_id, ts
from backend.pyworker.runtime.base import AgentResult
from backend.pyworker.runtime.collector import collect_turn
from backend.pyworker.runtime.driver import DriverEvent, DriverTurnError, StartTurn, ToolPolicy, TurnRef
from backend.pyworker.runtime.execute_driver import ExecuteTaskDriver

from .helpers import Api


class CleanupGate:
    def __init__(self, *, hold_admission=False):
        self.cleaning = asyncio.Event()
        self.release = asyncio.Event()
        self.stashed = asyncio.Event()
        self.closed = False
        self.hold_admission = hold_admission

    async def start_turn(self, turn, emit):
        for seq, (kind, data, item) in enumerate([
            ("item.completed", {"item_type": "assistant", "phase": "final", "text": "answer"}, "a"),
            ("turn.completed", {"status": "succeeded", "final_item_ids": ["a"]}, None),
        ], 1):
            await emit(DriverEvent(1, new_id(), turn.turn_id, turn.run_id, seq,
                                   "fake", "default", ts(), kind, data, item_id=item))
        self.stashed.set()
        if self.hold_admission:
            await asyncio.Event().wait()
        return TurnRef(turn.turn_id, turn.run_id)

    async def interrupt(self, turn_id):
        self.cleaning.set()
        await self.release.wait()
        return "already-terminal"

    async def close(self, kind, scope):
        self.closed = True
        return ()


async def start_chat(make_app, monkeypatch, driver):
    monkeypatch.setattr(runs, "create_turn_driver", lambda *a, **kw: driver)
    api = Api(make_app())
    cid = (await api("conversations.create", title="R-B"))["id"]
    run = await api("runs.start_turn", conversation_id=cid, engine="mock", model_id="m", prompt="hi")
    return api, api.app.ctx.runs, run["id"]


def assert_settled(api, run_id, status):
    row = api.db.one("SELECT * FROM conversation_runs WHERE id=?", (run_id,))
    assert row["status"] == status
    usage = api.db.all("SELECT * FROM usage_events WHERE run_id=?", (run_id,))
    assert len(usage) == 1
    assert usage[0]["success"] == int(status == "succeeded")
    events = [json.loads(r["data_json"])["event"] for r in api.db.all(
        "SELECT * FROM conversation_run_events WHERE run_id=? AND type='runtime'", (run_id,))]
    terminals = [e for e in events if e["type"] == "turn.completed"]
    assert all(e["data"]["status"] == status for e in terminals)
    assert len(terminals) <= 1
    if status == "succeeded":
        assert len(terminals) == 1
    return row


@pytest.mark.asyncio
async def test_cancel_after_stashed_success_drops_terminal_and_usage_success(make_app, monkeypatch):
    driver = CleanupGate()
    api, manager, rid = await start_chat(make_app, monkeypatch, driver)
    await driver.cleaning.wait()
    execution = manager._tasks[rid]
    cancelling = asyncio.create_task(manager.cancel(rid))
    while not manager._durably_cancelled(rid):
        await asyncio.sleep(0)
    driver.release.set()
    await cancelling
    await asyncio.gather(execution, return_exceptions=True)
    row = assert_settled(api, rid, "cancelled")
    assert row["assistant_message_seq"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("cause", ["shutdown", "caller"])
async def test_cleanup_cancel_preserves_success_before_recovery(make_app, monkeypatch, cause):
    driver = CleanupGate()
    api, manager, rid = await start_chat(make_app, monkeypatch, driver)
    await driver.cleaning.wait()
    execution = manager._tasks[rid]
    if cause == "shutdown":
        shutdown = asyncio.create_task(manager.shutdown())
        await asyncio.sleep(0)
    else:
        execution.cancel()
    # Let the shield observe cancellation while cleanup is still blocked.
    await asyncio.sleep(0)
    driver.release.set()
    if cause == "shutdown":
        await shutdown
    else:
        await execution
    assert manager.interrupt_active() == 0
    row = assert_settled(api, rid, "succeeded")
    assert row["assistant_message_seq"] is not None and driver.closed


@pytest.mark.asyncio
async def test_interrupted_row_drops_stashed_success(make_app, monkeypatch):
    driver = CleanupGate()
    api, manager, rid = await start_chat(make_app, monkeypatch, driver)
    await driver.cleaning.wait()
    execution = manager._tasks[rid]
    assert manager.interrupt_active() == 1
    driver.release.set()
    await execution
    row = assert_settled(api, rid, "interrupted")
    assert row["assistant_message_seq"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("durable_cancel", [False, True])
async def test_collector_cleanup_cancel_result_arbitration(durable_cancel):
    driver = CleanupGate()
    turn = StartTurn("t", "r", "conversation", "c", "hi", "m", ".", ToolPolicy("none", "deny"))
    operation = asyncio.create_task(collect_turn(driver, turn, is_cancelled=lambda: durable_cancel))
    await driver.cleaning.wait()
    operation.cancel()
    await asyncio.sleep(0)
    driver.release.set()
    if durable_cancel:
        with pytest.raises(asyncio.CancelledError):
            await operation
    else:
        assert (await operation).text == "answer"
    assert driver.closed


def mission_args(api, runtime, **kw):
    mid = new_id()
    api.db.insert("missions", {"id": mid, "title": "R-B", "objective": "test",
                              "status": "running", "created_at": ts(), "updated_at": ts()})
    return dict(runtime=runtime, mission_id=mid, task_id=None, attempt=1, engine="mock",
                provider_id="mock", model_id="m", variant=None, prompt="test", session_title="test",
                timeout=1, kind=runs.KIND_MISSION_PLANNER, **kw)


@pytest.mark.asyncio
async def test_mission_wait_for_timeout_after_stashed_success(api, monkeypatch):
    driver = CleanupGate(hold_admission=True)
    driver.release.set()
    monkeypatch.setattr(runs, "create_turn_driver", lambda *a, **kw: driver)
    wait_for = asyncio.wait_for

    async def short_mission_timeout(awaitable, timeout):
        return await wait_for(awaitable, 0.03 if timeout == 31 else timeout)

    monkeypatch.setattr(runs.asyncio, "wait_for", short_mission_timeout)
    with pytest.raises(asyncio.TimeoutError):
        await api.app.ctx.runs.run_mission_task(**mission_args(api, object()))
    assert driver.stashed.is_set()
    rid = api.db.scalar("SELECT id FROM conversation_runs WHERE mission_id IS NOT NULL")
    row = assert_settled(api, rid, "failed")
    assert row["result_text"] is None


@pytest.mark.asyncio
async def test_mission_validation_failure_drops_stashed_success(api, monkeypatch):
    driver = CleanupGate()
    driver.release.set()
    monkeypatch.setattr(runs, "create_turn_driver", lambda *a, **kw: driver)

    def reject(text):
        raise ValueError("invalid result")

    with pytest.raises(ValueError):
        await api.app.ctx.runs.run_mission_task(**mission_args(api, object(), validate=reject))
    rid = api.db.scalar("SELECT id FROM conversation_runs WHERE mission_id IS NOT NULL")
    assert_settled(api, rid, "failed")


@pytest.mark.asyncio
async def test_mission_shutdown_preserves_result_through_wait_for(api, monkeypatch):
    driver = CleanupGate()
    monkeypatch.setattr(runs, "create_turn_driver", lambda *a, **kw: driver)
    manager = api.app.ctx.runs
    mission = asyncio.create_task(manager.run_mission_task(**mission_args(api, object())))
    await driver.cleaning.wait()
    shutdown = asyncio.create_task(manager.shutdown())
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    driver.release.set()
    await shutdown
    result, validated = await mission
    assert result.text == "answer" and validated is None
    assert manager.interrupt_active() == 0
    rid = api.db.scalar("SELECT id FROM conversation_runs WHERE mission_id IS NOT NULL")
    assert assert_settled(api, rid, "succeeded")["result_text"] == "answer"


@pytest.mark.asyncio
async def test_mission_cap_rejects_snapshot_before_runtime_storage(api, monkeypatch):
    class Oversized:
        async def execute_task(self, task):
            return AgentResult("x" * 65, "m")

        async def abort_task(self, key):
            pass

    observed = []

    def create(engine, runtime, **kw):
        driver = ExecuteTaskDriver(runtime, engine, legacy_task=kw["legacy_task"])
        start = driver.start_turn

        async def capture(turn, emit):
            observed.append(turn.max_output_chars)
            return await start(turn, emit)

        driver.start_turn = capture
        return driver

    monkeypatch.setattr(runs, "create_turn_driver", create)
    with pytest.raises(DriverTurnError) as exc:
        await api.app.ctx.runs.run_mission_task(**mission_args(api, Oversized(), max_output_chars=64))
    assert exc.value.code == "output_limit" and observed == [64]
    rid = api.db.scalar("SELECT id FROM conversation_runs WHERE mission_id IS NOT NULL")
    assert_settled(api, rid, "failed")
    assert not any("x" * 65 in r["data_json"] for r in api.db.all(
        "SELECT data_json FROM conversation_run_events WHERE run_id=?", (rid,)))


class SlowKill:
    def __init__(self):
        self.started, self.killing, self.release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        self.finished = False

    async def execute_task(self, task):
        self.started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.killing.set()
            await self.release.wait()
        self.finished = True
        return AgentResult("late output", "m")

    async def abort_task(self, key):
        pass


@pytest.mark.asyncio
async def test_abort_kill_is_retained_after_cancelled_terminal(make_app, monkeypatch):
    class SlowAbort(SlowKill):
        async def execute_task(self, task):
            self.started.set()
            await asyncio.Event().wait()

        async def abort_task(self, key):
            self.killing.set()
            await self.release.wait()
            self.finished = True

    runtime = SlowAbort()
    driver = ExecuteTaskDriver(runtime)
    api, manager, rid = await start_chat(make_app, monkeypatch, driver)
    await runtime.started.wait()
    shutdown = asyncio.create_task(manager.shutdown())
    await runtime.killing.wait()
    await asyncio.sleep(1.1)
    state = driver._turns[rid]
    assert state.terminal and not state.abort.done()
    assert not shutdown.done()
    runtime.release.set()
    await asyncio.wait_for(shutdown, 1)
    assert runtime.finished
    assert manager.interrupt_active() == 1
    assert_settled(api, rid, "interrupted")


@pytest.mark.asyncio
async def test_shutdown_joins_kill_beyond_old_one_second_budget(make_app, monkeypatch):
    runtime = SlowKill()
    driver = ExecuteTaskDriver(runtime)
    api, manager, rid = await start_chat(make_app, monkeypatch, driver)
    await runtime.started.wait()
    shutdown = asyncio.create_task(manager.shutdown())
    await runtime.killing.wait()
    await asyncio.sleep(1.1)
    assert not shutdown.done() and not runtime.finished
    runtime.release.set()
    await asyncio.wait_for(shutdown, 1)
    assert runtime.finished
    assert manager.interrupt_active() == 1
    assert_settled(api, rid, "interrupted")
    assert "late output" not in str(api.db.all("SELECT data_json FROM conversation_run_events"))


@pytest.mark.asyncio
async def test_shutdown_deadline_releases_join_without_second_cancel(make_app, monkeypatch):
    runtime = SlowKill()
    driver = ExecuteTaskDriver(runtime)
    api, manager, rid = await start_chat(make_app, monkeypatch, driver)
    await runtime.started.wait()
    loop = asyncio.get_running_loop()
    before = loop.time()
    await asyncio.wait_for(manager.shutdown(), 0.03)
    assert loop.time() - before < 0.5 and runtime.killing.is_set()
    assert manager.interrupt_active() == 1
    assert_settled(api, rid, "interrupted")
    runtime.release.set()
    await asyncio.wait_for(driver._turns[rid].task, 1)
    assert runtime.finished
