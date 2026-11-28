"""Turn contract, legacy execution, ordered collection and cancellation arbitration."""

import asyncio
from dataclasses import asdict, replace

import pytest

from backend.pyworker.db.core import new_id, ts
from backend.pyworker.runtime.base import AgentResult, ExecuteAgentTask
from backend.pyworker.runtime.collector import collect_turn
from backend.pyworker.runtime.driver import (
    ApprovalAnswer, DriverEvent, DriverFailure, DriverTurnError, SessionRef, StartTurn, ToolPolicy, TurnRef,
)
from backend.pyworker.runtime.execute_driver import ExecuteTaskDriver
from backend.pyworker.runtime.errors import RuntimeAuthError, RuntimeCancelled, RuntimeRateLimit, RuntimeTimeout
from backend.pyworker.runtime.mock_runtime import MockRuntime
from backend.pyworker.runtime import ledger


def turn(**kwargs):
    return replace(StartTurn("t", "r", "conversation", "c", "hello", "model", ".", ToolPolicy("none", "deny")), **kwargs)


class Fixed(MockRuntime):
    def __init__(self, result=None, error=None):
        super().__init__()
        self.result = result or AgentResult("reply", "effective", reasoning="thinking", variant_id="v",
            cost=0.2, tokens={"input": 3, "output": 4, "cache": {"read": 1}}, duration_ms=27, session_id="native")
        self.error = error
        self.calls, self.aborts = [], []

    async def execute_task(self, task):
        self.calls.append(task)
        if self.error:
            raise self.error
        return self.result

    async def abort_task(self, key):
        self.aborts.append(key)


@pytest.mark.asyncio
async def test_wrapper_order_single_terminal_no_fake_streaming_and_lossless_result():
    runtime = Fixed()
    driver = ExecuteTaskDriver(runtime, "mock")
    events = []

    async def emit(e):
        events.append(e)
        await asyncio.sleep(0)  # delivery must remain ordered under backpressure

    result = await collect_turn(driver, turn(), emit)
    assert asdict(result) == asdict(runtime.result)
    assert [e.seq for e in events] == list(range(1, len(events) + 1))
    assert len({e.event_id for e in events}) == len(events)
    assert events[0].type == "turn.started" and events[-1].type == "turn.completed"
    assert sum(e.type == "turn.completed" for e in events) == 1
    assert sum(e.type == "item.completed" and e.data["item_type"] == "assistant" for e in events) == 1
    assert not any(e.type == "content.delta" for e in events)
    assert not driver.capabilities.streaming_text and driver.capabilities.native_resume == "none"
    assert await driver.respond("t", "q", ApprovalAnswer("allow")) == "unavailable"
    assert await driver.interrupt("t") == "already-terminal"
    assert await driver.interrupt("missing") == "unknown"
    assert runtime.aborts == []


@pytest.mark.asyncio
async def test_admission_registers_stop_before_task_starts_and_rejects_busy_scope():
    runtime = Fixed()
    driver = ExecuteTaskDriver(runtime)
    events = []

    async def emit(e):
        events.append(e)

    ref = await driver.start_turn(turn(), emit)
    assert ref == TurnRef("t", "r")
    with pytest.raises(DriverTurnError) as error:
        await driver.start_turn(turn(turn_id="other"), emit)
    assert error.value.failure.category == "busy"
    assert error.value.failure.acceptance == "not-submitted"
    await driver.interrupt("t")
    assert runtime.calls == []
    assert runtime.aborts == ["t"]
    assert [e.type for e in events] == ["turn.started", "turn.completed"]
    assert events[-1].data["status"] == "cancelled"


@pytest.mark.asyncio
async def test_interrupt_is_idempotent_joins_original_key_and_ignores_mock_late_result():
    class Late(MockRuntime):
        def __init__(self):
            super().__init__()
            self.started = asyncio.Event()
            self.aborts = []
            self.returned = False

        async def execute_task(self, request):
            self.started.set()
            result = await super().execute_task(request)
            self.returned = True
            return result

        async def abort_task(self, key):
            self.aborts.append(key)

    runtime = Late()
    request = ExecuteAgentTask("[mock:delay=10000][mock:late=10]", "m", "mock", session_title="original-key")
    driver = ExecuteTaskDriver(runtime, legacy_task=request)
    events = []

    async def emit(e):
        events.append(e)

    await driver.start_turn(turn(), emit)
    await runtime.started.wait()
    assert await asyncio.gather(driver.interrupt("t"), driver.interrupt("t")) == ["requested", "requested"]
    assert runtime.aborts == ["original-key"] and runtime.returned
    assert [e.type for e in events] == ["turn.started", "turn.completed"]
    assert events[-1].data["status"] == "cancelled"
    assert await driver.stop_all() == ()


@pytest.mark.asyncio
async def test_failure_is_normalised_safe_and_never_replayed():
    runtime = Fixed(error=RuntimeAuthError("secret sk-password"))
    driver = ExecuteTaskDriver(runtime)
    events = []

    async def emit(e):
        events.append(e)

    with pytest.raises(DriverTurnError) as error:
        await collect_turn(driver, turn(), emit)
    assert error.value.code == "auth" and error.value.failure.acceptance == "unknown"
    assert len(runtime.calls) == 1
    assert [e.type for e in events] == ["turn.started", "runtime.error", "turn.completed"]
    assert "password" not in str([asdict(e) for e in events])


@pytest.mark.asyncio
async def test_wrapper_bounds_snapshot_before_persistence():
    runtime = Fixed(result=AgentResult("x" * 101, "m"))
    events = []

    async def emit(e):
        events.append(e)

    with pytest.raises(DriverTurnError) as error:
        await collect_turn(ExecuteTaskDriver(runtime), turn(max_output_chars=100), emit)
    assert error.value.code == "output_limit"
    assert not any(e.type == "item.completed" for e in events)


@pytest.mark.asyncio
async def test_health_and_catalogue_never_execute_and_resume_is_refused():
    runtime = Fixed()
    driver = ExecuteTaskDriver(runtime)
    assert await driver.check_health() == await runtime.check_health()
    assert await driver.list_available_models(refresh=True) == await runtime.list_available_models(True)
    with pytest.raises(DriverTurnError) as error:
        await collect_turn(driver, turn(session=SessionRef("native", "acp-load", "local", 1)))
    assert error.value.code == "unsupported_capability" and not runtime.calls


class ScriptedDriver:
    """Early events delivered before admission returns; one callback, no queue."""
    def __init__(self, script):
        self.script, self.interrupted, self.closed = script, [], []

    async def start_turn(self, request, emit):
        for seq, (type_, data, item) in enumerate(self.script, 1):
            await emit(DriverEvent(1, new_id(), request.turn_id, request.run_id, seq,
                "fake", "default", ts(), type_, data, item_id=item))
        return TurnRef(request.turn_id, request.run_id)

    async def interrupt(self, turn_id):
        self.interrupted.append(turn_id)

    async def close(self, kind, scope):
        self.closed.append((kind, scope))
        return ()


def terminal(ids=("answer",), **kwargs):
    return ("turn.completed", {"status": "succeeded", "final_item_ids": list(ids), **kwargs}, None)


@pytest.mark.asyncio
async def test_collector_authoritative_json_final_ids_exclude_commentary_reasoning_and_tools():
    script = [
        ("turn.started", {}, None),
        ("content.delta", {"stream_kind": "assistant_text", "delta": '{"partial":'}, "answer"),
        ("item.completed", {"item_type": "assistant", "phase": "commentary", "text": "checking"}, "comment"),
        ("item.completed", {"item_type": "tool", "text": "not JSON"}, "tool"),
        ("item.completed", {"item_type": "assistant", "text": "not a reply", "synthetic_error": True}, "error"),
        ("content.delta", {"stream_kind": "reasoning_text", "delta": "think"}, "reason"),
        ("item.completed", {"item_type": "assistant", "text": '{"ok":true}', "phase": "final"}, "answer"),
        ("usage", {"scope": "context", "tokens": {"input": 100000}}, None),
        ("usage", {"scope": "turn", "tokens": {"input": 7}}, None),
        ("usage", {"scope": "turn", "tokens": {"input": 9}}, None),
        terminal(("comment", "tool", "reason", "error", "answer"), model_id="effective", variant="v", duration_ms=8),
        terminal(),  # ignored duplicate terminal and late output
        ("content.delta", {"stream_kind": "assistant_text", "delta": "LATE"}, "answer"),
    ]
    events = []

    async def emit(e):
        events.append(e)

    result = await collect_turn(ScriptedDriver(script), turn(), emit)
    assert result.text == '{"ok":true}' and result.reasoning == "think"
    assert result.tokens == {"input": 9} and result.cost is None
    assert (result.model_id, result.variant_id, result.duration_ms) == ("effective", "v", 8)
    assert len(events) == 11


@pytest.mark.asyncio
@pytest.mark.parametrize("script,code", [
    ([("content.delta", {"stream_kind": "assistant_text", "delta": "x" * 11}, "answer")], "output_limit"),
    ([terminal(("missing",))], "protocol_error"),
    ([("turn.completed", {"status": "failed", "failure": asdict(DriverFailure(
        "timeout", "Timed out.", True, "running", "submitted"))}, None)], "timeout"),
])
async def test_collector_limits_missing_final_and_failure(script, code):
    driver = ScriptedDriver(script)
    with pytest.raises(DriverTurnError) as error:
        await collect_turn(driver, turn(max_output_chars=10))
    assert error.value.code == code
    assert driver.interrupted == ["t"] and driver.closed == [("conversation", "c")]


@pytest.mark.asyncio
@pytest.mark.parametrize("version,identity,seq,type_", [
    (1, "wrong", 9, "turn.started"), (2, "t", 1, "turn.started"), (1, "t", 1, "unknown"),
])
async def test_collector_rejects_out_of_order_identity(version, identity, seq, type_):
    class Bad(ScriptedDriver):
        async def start_turn(self, request, emit):
            await emit(DriverEvent(version, "e", identity, request.run_id, seq, "fake", "default", ts(), type_, {}))
            return TurnRef(request.turn_id, request.run_id)

    with pytest.raises(DriverTurnError) as error:
        await collect_turn(Bad([]), turn())
    assert error.value.code == "protocol_error"


@pytest.mark.asyncio
async def test_caller_cancel_and_timeout_shield_interrupt_and_join_cleanup():
    for timeout in (False, True):
        runtime = Fixed()
        started = asyncio.Event()
        cleaned = asyncio.Event()

        async def execute(request):
            started.set()
            await asyncio.sleep(10)

        async def abort(key):
            await asyncio.sleep(0.01)
            cleaned.set()

        runtime.execute_task, runtime.abort_task = execute, abort
        driver = ExecuteTaskDriver(runtime)
        operation = collect_turn(driver, turn())
        task = asyncio.create_task(asyncio.wait_for(operation, 0.03) if timeout else operation)
        await started.wait()
        if not timeout:
            task.cancel()
        with pytest.raises(asyncio.TimeoutError if timeout else asyncio.CancelledError):
            await task
        assert cleaned.is_set() and driver._turns["t"].task.done()


@pytest.mark.asyncio
async def test_ledger_records_exactly_once_with_usage_snapshots_and_cancellation(api):
    runtime = Fixed()
    request = ExecuteAgentTask("hi", "model", "mock", session_title="original")
    await ledger.run_agent_task(runtime, request, "test", "run")
    rows = api.db.all("SELECT * FROM usage_events WHERE run_id='run'")
    assert len(rows) == 1 and rows[0]["tokens_in"] == 3 and rows[0]["tokens_out"] == 4
    snapshots = ScriptedDriver([
        ("item.completed", {"item_type": "assistant", "text": "reply"}, "answer"),
        ("usage", {"scope": "context", "input": 999}, None),
        ("usage", {"scope": "turn", "measurement_id": "m", "input": 3}, None),
        ("usage", {"scope": "turn", "measurement_id": "m", "input": 5}, None),
        terminal(),
    ])
    await ledger.run_agent_task(runtime, request, "test", "snapshots", driver=snapshots)
    rows = api.db.all("SELECT * FROM usage_events WHERE run_id='snapshots'")
    assert len(rows) == 1 and rows[0]["tokens_in"] == 5
    runtime.error = RuntimeAuthError("secret")
    with pytest.raises(DriverTurnError):
        await ledger.run_agent_task(runtime, request, "test", "failed")
    assert api.db.scalar("SELECT COUNT(*) FROM usage_events WHERE run_id='failed'") == 1
    started = asyncio.Event()

    async def slow(request):
        started.set()
        await asyncio.sleep(30)

    runtime.execute_task = slow
    cancelled = asyncio.create_task(ledger.run_agent_task(runtime, request, "test", "cancelled"))
    await started.wait()
    cancelled.cancel()
    with pytest.raises(asyncio.CancelledError):
        await cancelled
    rows = api.db.all("SELECT * FROM usage_events WHERE run_id='cancelled'")
    assert len(rows) == 1 and rows[0]["error_kind"] == "CancelledError"


@pytest.mark.asyncio
async def test_collector_repeated_cancel_still_joins_cleanup():
    class SlowCleanup(ScriptedDriver):
        def __init__(self):
            super().__init__([])
            self.admitted = asyncio.Event()
            self.stopping = asyncio.Event()
            self.release = asyncio.Event()

        async def start_turn(self, request, emit):
            self.admitted.set()
            return TurnRef(request.turn_id, request.run_id)

        async def interrupt(self, turn_id):
            self.stopping.set()
            await self.release.wait()
            self.interrupted.append(turn_id)

    driver = SlowCleanup()
    task = asyncio.create_task(collect_turn(driver, turn()))
    await driver.admitted.wait()
    task.cancel()
    await driver.stopping.wait()
    task.cancel()
    driver.release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert driver.interrupted == ["t"] and driver.closed == [("conversation", "c")]


@pytest.mark.asyncio
async def test_wrapper_rejects_invalid_arguments_before_execution():
    runtime = Fixed()
    for request in (turn(model_id=""), turn(timeout_s=0), turn(max_output_chars=0)):
        with pytest.raises(ValueError):
            await collect_turn(ExecuteTaskDriver(runtime), request)
    assert runtime.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("error,category,acceptance", [
    (FileNotFoundError("private/path"), "cli_missing", "not-submitted"),
    (RuntimeRateLimit("private"), "rate_limit", "unknown"),
    (RuntimeTimeout("private"), "timeout", "unknown"),
    (RuntimeCancelled("private"), "cancelled", "unknown"),
])
async def test_wrapper_failure_categories_and_submission_certainty(error, category, acceptance):
    events = []

    async def emit(event):
        events.append(event)

    with pytest.raises(DriverTurnError) as caught:
        await collect_turn(ExecuteTaskDriver(Fixed(error=error)), turn(), emit)
    assert (caught.value.failure.category, caught.value.failure.acceptance) == (category, acceptance)
    assert events[-1].type == "turn.completed"
    assert events[-1].data["status"] == ("cancelled" if category == "cancelled" else "failed")


@pytest.mark.asyncio
async def test_abort_failure_is_reported_and_execution_still_joins():
    runtime = Fixed()
    started = asyncio.Event()

    async def execute(request):
        started.set()
        await asyncio.sleep(30)

    async def abort(key):
        raise OSError("private")

    runtime.execute_task, runtime.abort_task = execute, abort
    driver = ExecuteTaskDriver(runtime)
    operation = asyncio.create_task(collect_turn(driver, turn()))
    await started.wait()
    operation.cancel()
    with pytest.raises(asyncio.CancelledError):
        await operation
    issues = await driver.close("conversation", "c")
    assert len(issues) == 1 and "private" not in issues[0].message
    assert driver._turns["t"].task.done()


@pytest.mark.asyncio
async def test_rejected_collector_admission_does_not_close_existing_scope():
    runtime = Fixed()
    started, release = asyncio.Event(), asyncio.Event()

    async def execute(request):
        started.set()
        await release.wait()
        return runtime.result

    runtime.execute_task = execute
    driver = ExecuteTaskDriver(runtime)
    first = asyncio.create_task(collect_turn(driver, turn()))
    await started.wait()
    with pytest.raises(DriverTurnError) as caught:
        await collect_turn(driver, turn(turn_id="other", run_id="other"))
    assert caught.value.code == "busy" and runtime.aborts == [] and not first.done()
    release.set()
    assert (await first).text == "reply"


class Stubborn(MockRuntime):
    """Engine that ignores cancellation and returns a late result."""
    def __init__(self):
        super().__init__()
        self.release = asyncio.Event()
        self.finished = False

    async def execute_task(self, task):
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            await self.release.wait()  # swallow the first cancel
        self.finished = True
        return AgentResult("late", "m", tokens={}, duration_ms=1)

    async def abort_task(self, key):
        pass


@pytest.mark.asyncio
async def test_interrupt_is_bounded_when_engine_ignores_cancel_and_late_result_ignored():
    runtime = Stubborn()
    driver = ExecuteTaskDriver(runtime, "mock", cancel_grace_s=0.2)
    events = []

    async def emit(e):
        events.append(e)

    await driver.start_turn(turn(), emit)
    await asyncio.sleep(0.05)
    loop = asyncio.get_running_loop()
    t0 = loop.time()
    assert await asyncio.wait_for(driver.interrupt("t"), 3) == "requested"
    assert loop.time() - t0 < 2
    assert await asyncio.wait_for(driver.interrupt("t"), 3) in {"requested", "already-terminal"}
    issues = await asyncio.wait_for(driver.close("conversation", "c"), 3)
    assert any("ignored cancellation" in i.message for i in issues)
    assert sum(e.type == "turn.completed" for e in events) == 1
    assert events[-1].data["status"] == "cancelled"
    n = len(events)
    runtime.release.set()  # the abandoned task now finishes with a result
    await asyncio.sleep(0.1)
    assert runtime.finished and len(events) == n
    assert await asyncio.wait_for(driver.stop_all(), 3) is not None
