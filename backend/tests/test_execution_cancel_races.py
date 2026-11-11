"""
Legacy workflow execution cancellation races.

Invariants under test:
  * a committed cancel is never overwritten by later node/workflow success/failure
  * a cancel never overwrites an execution that is already terminal
  * a late runtime result after cancel is ignored (no output persisted)

Uses file-backed SQLite with SEPARATE connections (the cancel route and the
executor run on different sessions in production). No real model calls: a
gated in-process runtime stands in for the model.
"""

import asyncio
import json
import threading

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from backend.app.db.models import ExecutionEvent, NodeExecution, Workflow, WorkflowExecution
from backend.app.db.repository import ExecutionRepository
from backend.app.db.session import Base
from backend.app.engine.executor import (
    _fail_workflow,
    _transition_execution,
    _transition_node,
    cancel_execution_rows,
    execute_workflow,
)
from backend.app.runtime.base import AgentResult
from backend.app.runtime.mock_runtime import MockRuntime

MODEL = "muse-spark-1.3-contributor-free"
DEFINITION = {
    "nodes": [
        {"id": "start-1", "type": "start"},
        {"id": "agent-1", "type": "agent", "data": {"role_type": "planner", "model_id": MODEL, "max_retries": 0}},
        {"id": "agent-2", "type": "agent", "data": {"role_type": "analyst", "model_id": MODEL, "max_retries": 0}},
        {"id": "output-1", "type": "output"},
    ],
    "edges": [
        {"id": "e1", "source": "start-1", "target": "agent-1"},
        {"id": "e2", "source": "agent-1", "target": "agent-2"},
        {"id": "e3", "source": "agent-2", "target": "output-1"},
    ],
}


class GatedRuntime(MockRuntime):
    """Blocks execute_task until released; then returns or raises on demand."""

    def __init__(self, fail_with: Exception | None = None, text: str = '{"ok": true}'):
        super().__init__()
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.calls = 0
        self.fail_with = fail_with
        self.text = text

    async def execute_task(self, task):
        self.calls += 1
        self.entered.set()
        await self.release.wait()
        if self.fail_with:
            raise self.fail_with
        return AgentResult(text=self.text, model_id=task.model_id, session_id="s", duration_ms=1)


@pytest.fixture()
def factory(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'cancel.db'}",
        connect_args={"check_same_thread": False, "timeout": 30},
        poolclass=NullPool,
    )
    Base.metadata.create_all(bind=engine)
    sf = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    from backend.app.runtime import ledger
    monkeypatch.setattr(ledger, "session_factory", sf)
    yield sf
    engine.dispose()


def _make_execution(sf, status="pending"):
    s = sf()
    wf = Workflow(name="w", definition_json=json.dumps(DEFINITION))
    s.add(wf)
    s.commit()
    ex = ExecutionRepository(s).create_execution(workflow_id=wf.id, workflow_snapshot_json=wf.definition_json)
    if status != "pending":
        ex.status = status
        s.commit()
    eid = ex.id
    s.close()
    return eid


def _snapshot(sf, eid):
    s = sf()
    try:
        ex = s.get(WorkflowExecution, eid)
        nodes = {n.node_id: n for n in s.query(NodeExecution).filter_by(execution_id=eid)}
        events = [e.event_type for e in s.query(ExecutionEvent).filter_by(execution_id=eid)]
        return (
            ex.status, ex.final_output_json, ex.error_summary,
            {k: (v.status, v.output_json) for k, v in nodes.items()}, events,
        )
    finally:
        s.close()


async def _run_until_agent_blocked(sf, runtime):
    eid = _make_execution(sf)
    run_db = sf()
    task = asyncio.create_task(
        execute_workflow(eid, run_db, runtime, DEFINITION, initial_input={"goal": "x"})
    )
    await asyncio.wait_for(runtime.entered.wait(), 10)
    return eid, run_db, task


def _cancel_from_other_connection(sf, eid):
    s = sf()
    try:
        return cancel_execution_rows(s, eid, event_json='{"reason": "t"}')
    finally:
        s.close()


# ----------------------------------------------------- gated deterministic


@pytest.mark.asyncio
async def test_late_success_after_committed_cancel_is_ignored(factory):
    rt = GatedRuntime()
    eid, run_db, task = await _run_until_agent_blocked(factory, rt)

    assert _cancel_from_other_connection(factory, eid) is True
    rt.release.set()  # late runtime result arrives AFTER the commit
    result = await asyncio.wait_for(task, 10)
    run_db.close()

    assert result == {"status": "cancelled"}
    status, final_out, _summary, nodes, events = _snapshot(factory, eid)
    assert status == "cancelled" and final_out is None
    assert nodes["agent-1"] == ("cancelled", None)  # late output NOT persisted
    assert nodes["agent-2"][0] == "cancelled" and nodes["output-1"][0] == "cancelled"
    assert nodes["start-1"][0] == "succeeded"  # finished before the cancel: kept
    assert rt.calls == 1  # no further model calls after cancel
    assert events.count("execution.cancelled") == 1


@pytest.mark.asyncio
async def test_late_failure_after_committed_cancel_is_ignored(factory):
    rt = GatedRuntime(fail_with=RuntimeError("boom"))
    eid, run_db, task = await _run_until_agent_blocked(factory, rt)

    assert _cancel_from_other_connection(factory, eid) is True
    rt.release.set()
    result = await asyncio.wait_for(task, 10)
    run_db.close()

    assert result == {"status": "cancelled"}
    status, _f, summary, nodes, events = _snapshot(factory, eid)
    assert status == "cancelled" and "cancelled" in summary
    assert nodes["agent-1"][0] == "cancelled"
    assert "node.attempt.failed" not in events


@pytest.mark.asyncio
async def test_unparseable_late_result_after_cancel_does_not_fail_workflow(factory):
    rt = GatedRuntime(text="not json at all")
    eid, run_db, task = await _run_until_agent_blocked(factory, rt)

    assert _cancel_from_other_connection(factory, eid) is True
    rt.release.set()
    result = await asyncio.wait_for(task, 10)
    run_db.close()

    assert result == {"status": "cancelled"}
    assert _snapshot(factory, eid)[0] == "cancelled"


@pytest.mark.asyncio
async def test_cancel_after_normal_completion_leaves_execution_succeeded(factory):
    rt = GatedRuntime()
    rt.release.set()
    eid = _make_execution(factory)
    db = factory()
    result = await execute_workflow(eid, db, rt, DEFINITION, initial_input={"goal": "x"})
    db.close()
    assert result["status"] == "succeeded"

    before = _snapshot(factory, eid)
    assert _cancel_from_other_connection(factory, eid) is False
    assert _snapshot(factory, eid) == before  # nothing written, no event added


@pytest.mark.asyncio
async def test_cancel_does_not_overwrite_failed_or_cancelled(factory):
    for terminal in ("failed", "cancelled"):
        eid = _make_execution(factory, status=terminal)
        before = _snapshot(factory, eid)
        assert _cancel_from_other_connection(factory, eid) is False
        assert _snapshot(factory, eid) == before


@pytest.mark.asyncio
async def test_execute_on_already_cancelled_execution_does_not_resurrect(factory):
    eid = _make_execution(factory, status="cancelled")
    rt = GatedRuntime()
    rt.release.set()
    db = factory()
    result = await execute_workflow(eid, db, rt, DEFINITION)
    db.close()
    assert result == {"status": "cancelled"}
    assert rt.calls == 0
    assert _snapshot(factory, eid)[0] == "cancelled"


@pytest.mark.asyncio
async def test_workflow_success_and_failure_writes_lose_to_committed_cancel(factory):
    eid = _make_execution(factory, status="running")
    assert _cancel_from_other_connection(factory, eid) is True
    s = factory()
    try:
        assert _transition_execution(s, eid, ("running",), status="succeeded", final_output_json="{}") is False
        assert _fail_workflow(s, eid, "late failure") is False
    finally:
        s.close()
    status, final_out, summary, _n, _e = _snapshot(factory, eid)
    assert status == "cancelled" and final_out is None and "late failure" not in summary


@pytest.mark.asyncio
async def test_node_write_guarded_by_execution_cancel(factory):
    eid = _make_execution(factory, status="running")
    s = factory()
    node = NodeExecution(execution_id=eid, node_id="n", status="running")
    s.add(node)
    s.commit()
    nid = node.id
    s.close()

    assert _cancel_from_other_connection(factory, eid) is True
    s = factory()
    try:
        # Even from a stale 'running' view the guarded write must lose.
        assert _transition_node(s, nid, ("running",), status="succeeded", output_json='{"x":1}') is False
    finally:
        s.close()
    assert _snapshot(factory, eid)[3]["n"] == ("cancelled", None)


# ----------------------------------------------- separate-connection races


def test_threaded_cancel_vs_workflow_success_is_never_mixed(factory):
    """Cancel and workflow-success race from separate connections.
    Final state must be exactly one of succeeded / cancelled, never a
    cancelled execution carrying a success output (or vice versa)."""
    ids = [_make_execution(factory, status="running") for _ in range(40)]
    errors, barrier = [], threading.Barrier(2)

    def finish(eid):
        s = factory()
        try:
            barrier.wait(10)
            _transition_execution(s, eid, ("running",), status="succeeded", final_output_json='{"done": 1}')
        except Exception as e:  # pragma: no cover
            errors.append(e)
        finally:
            s.close()

    def cancel(eid):
        s = factory()
        try:
            barrier.wait(10)
            cancel_execution_rows(s, eid)
        except Exception as e:  # pragma: no cover
            errors.append(e)
        finally:
            s.close()

    for eid in ids:
        barrier.reset()
        a, b = threading.Thread(target=finish, args=(eid,)), threading.Thread(target=cancel, args=(eid,))
        a.start(); b.start(); a.join(30); b.join(30)

    assert not errors, errors
    outcomes = set()
    for eid in ids:
        status, final_out, _s, _n, events = _snapshot(factory, eid)
        outcomes.add(status)
        if status == "cancelled":
            assert final_out is None
            assert events.count("execution.cancelled") == 0  # no event: cancel() passed no event_json
        else:
            assert status == "succeeded" and final_out == '{"done": 1}'
    assert outcomes <= {"succeeded", "cancelled"}


def test_threaded_cancel_vs_node_success_is_never_mixed(factory):
    """A node success racing a cancel: if the execution ends cancelled, the node
    must not be left running/succeeded-after-cancel with output from the race
    loser; the pair is (cancelled, cancelled) or (cancelled, succeeded-before-cancel)."""
    pairs = []
    for _ in range(40):
        eid = _make_execution(factory, status="running")
        s = factory()
        n = NodeExecution(execution_id=eid, node_id="n", status="running")
        s.add(n)
        s.commit()
        pairs.append((eid, n.id))
        s.close()

    errors, barrier = [], threading.Barrier(2)

    def succeed(nid):
        s = factory()
        try:
            barrier.wait(10)
            _transition_node(s, nid, ("running",), status="succeeded", output_json='{"v": 1}')
        except Exception as e:  # pragma: no cover
            errors.append(e)
        finally:
            s.close()

    def cancel(eid):
        s = factory()
        try:
            barrier.wait(10)
            cancel_execution_rows(s, eid)
        except Exception as e:  # pragma: no cover
            errors.append(e)
        finally:
            s.close()

    for eid, nid in pairs:
        barrier.reset()
        a, b = threading.Thread(target=succeed, args=(nid,)), threading.Thread(target=cancel, args=(eid,))
        a.start(); b.start(); a.join(30); b.join(30)

    assert not errors, errors
    seen = set()
    for eid, _nid in pairs:
        status, _f, _s, nodes, _e = _snapshot(factory, eid)
        assert status == "cancelled"  # cancel always commits for an active execution
        st, out = nodes["n"]
        assert (st, out) in {("cancelled", None), ("succeeded", '{"v": 1}')}, (st, out)
        seen.add(st)
    assert seen  # at least one outcome observed


def test_threaded_double_cancel_has_exactly_one_winner(factory):
    ids = [_make_execution(factory, status="running") for _ in range(20)]
    results, barrier = {}, threading.Barrier(2)

    def cancel(eid, slot):
        s = factory()
        try:
            barrier.wait(10)
            results[(eid, slot)] = cancel_execution_rows(s, eid, event_json="{}")
        finally:
            s.close()

    for eid in ids:
        barrier.reset()
        ts = [threading.Thread(target=cancel, args=(eid, i)) for i in (0, 1)]
        for t in ts: t.start()
        for t in ts: t.join(30)
        assert sorted([results[(eid, 0)], results[(eid, 1)]]) == [False, True]
        assert _snapshot(factory, eid)[4].count("execution.cancelled") == 1
