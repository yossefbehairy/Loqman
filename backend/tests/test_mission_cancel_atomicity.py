"""Atomicity of mission cancellation: a committed cancel can never be
overwritten by a later start/completion/failure, even when the in-process
check-then-write window is lost (cancel lands between check and write)."""
import asyncio
import json
import threading

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

import backend.app.api.routes.missions as missions_mod
import backend.app.orchestration.ceo as ceo_mod
from backend.app.api.routes.missions import _run_explicit_delegation, cancel_mission_endpoint
from backend.app.db.models import Base, Mission, MissionTask
from backend.app.db.repository import AgentProfileRepository, MissionRepository
from backend.app.orchestration.ceo import (
    MISSION_NON_TERMINAL,
    cancel_mission_rows,
    run_mission_background,
    transition_mission,
    transition_task,
)
from backend.tests.conftest import TestingSessionLocal
from backend.tests.test_mission_cancel_race import PLAN, GatedRuntime, _cancel_via_endpoint, _fresh, _run_gated


@pytest.fixture
def seeded(db_session):
    AgentProfileRepository(db_session).seed_defaults()
    return db_session, AgentProfileRepository(db_session).list_all()[0]


def _mk(db, profile):
    m = MissionRepository(db).create_mission("deliver")
    t = MissionRepository(db).create_task(m.id, "d", profile.role_type, "T", "do")
    return m.id, t.id


# ---------------------------------------------------------------- CAS unit

def test_cancel_wins_over_every_later_task_transition(seeded):
    db, profile = seeded
    mid, tid = _mk(db, profile)
    stale = TestingSessionLocal()  # holds a pre-cancel view
    try:
        assert stale.get(MissionTask, tid).status == "queued"
        assert cancel_mission_rows(db, mid) is True
        for frm, vals in [
            (("queued", "running"), {"status": "running", "attempt_count": 1}),
            (("queued", "running"), {"status": "failed"}),
            (("queued", "running"), {"status": "succeeded", "output_json": "{}"}),
        ]:
            assert transition_task(stale, tid, frm, **vals) is False
        assert transition_mission(stale, mid, MISSION_NON_TERMINAL, status="succeeded") is False
        assert transition_mission(stale, mid, MISSION_NON_TERMINAL, status="failed") is False
    finally:
        stale.close()
    status, summary, tasks = _fresh(mid)
    assert (status, summary, tasks) == ("cancelled", None, [("cancelled", None)])


def test_task_cas_refuses_when_mission_cancelled_even_if_task_still_queued(seeded):
    """Mission cancelled by a writer that did not touch the task row."""
    db, profile = seeded
    mid, tid = _mk(db, profile)
    db.query(Mission).filter_by(id=mid).update({"status": "cancelled"})
    db.commit()
    assert transition_task(db, tid, ("queued",), status="running") is False
    assert _fresh(mid)[2] == [("queued", None)]


def test_cancel_does_not_overwrite_terminal_mission(seeded):
    db, profile = seeded
    mid, _ = _mk(db, profile)
    assert transition_mission(db, mid, MISSION_NON_TERMINAL, status="succeeded") is True
    assert cancel_mission_rows(db, mid) is False
    assert cancel_mission_endpoint(mid, db=db).status == "succeeded"
    assert ceo_mod.is_mission_cancelled(mid) is False  # no leaked in-process flag


def test_cancel_is_idempotent(seeded):
    db, profile = seeded
    mid, _ = _mk(db, profile)
    assert cancel_mission_rows(db, mid) is True
    assert cancel_mission_rows(db, mid) is False
    assert _fresh(mid)[0] == "cancelled"


# ------------------------------------------------- queued delegation start

@pytest.mark.asyncio
async def test_cancelled_queued_delegation_never_starts(seeded):
    db, profile = seeded
    mid, tid = _mk(db, profile)
    _cancel_via_endpoint(mid)
    ceo_mod._clear_cancel(mid)  # simulate cancel coming from another process
    rt = GatedRuntime(["SHOULD NOT RUN"], gate_at=-1)
    await _run_explicit_delegation(mid, tid, profile.id, session_factory=TestingSessionLocal, runtime=rt)
    assert rt.calls == 0
    assert _fresh(mid) == ("cancelled", None, [("cancelled", None)])


@pytest.mark.asyncio
async def test_pipeline_cancelled_queued_mission_never_plans(seeded):
    db, _ = seeded
    m = MissionRepository(db).create_mission("obj")
    _cancel_via_endpoint(m.id)
    ceo_mod._clear_cancel(m.id)
    rt = GatedRuntime([PLAN], gate_at=-1)
    res = await run_mission_background(m.id, db_factory=TestingSessionLocal, runtime=rt)
    assert res["status"] == "cancelled" and rt.calls == 0


# ------------- lost check-then-write window: the pre-check says "not cancelled"

@pytest.mark.asyncio
async def test_delegation_success_cannot_overwrite_cancel_when_precheck_misses(seeded, monkeypatch):
    db, profile = seeded
    mid, tid = _mk(db, profile)
    monkeypatch.setattr(missions_mod, "mission_was_cancelled", lambda *_: False)
    rt = GatedRuntime(["LATE SUCCESS"], gate_at=0)
    await _run_gated(
        rt,
        lambda: _run_explicit_delegation(mid, tid, profile.id, session_factory=TestingSessionLocal, runtime=rt),
        lambda: _cancel_via_endpoint(mid),
    )
    assert _fresh(mid) == ("cancelled", None, [("cancelled", None)])


@pytest.mark.asyncio
async def test_delegation_failure_cannot_overwrite_cancel_when_precheck_misses(seeded, monkeypatch):
    db, profile = seeded
    mid, tid = _mk(db, profile)
    monkeypatch.setattr(missions_mod, "mission_was_cancelled", lambda *_: False)

    class Boom(GatedRuntime):
        async def execute_task(self, task):
            self.entered.set()
            await self.release.wait()
            raise RuntimeError("provider exploded")

    rt = Boom([""], gate_at=0)
    await _run_gated(
        rt,
        lambda: _run_explicit_delegation(mid, tid, profile.id, session_factory=TestingSessionLocal, runtime=rt),
        lambda: _cancel_via_endpoint(mid),
    )
    assert _fresh(mid) == ("cancelled", None, [("cancelled", None)])


@pytest.mark.asyncio
@pytest.mark.parametrize("gate_at", [0, 1, 2])
async def test_pipeline_cancel_survives_missed_precheck(seeded, monkeypatch, gate_at):
    """With mission_was_cancelled blind, only the conditional writes protect the cancel."""
    db, _ = seeded
    m = MissionRepository(db).create_mission("pipeline objective")
    monkeypatch.setattr(ceo_mod, "mission_was_cancelled", lambda *_: False)
    monkeypatch.setattr(ceo_mod, "is_mission_cancelled", lambda *_: False)
    rt = GatedRuntime([PLAN, '{"ok": true}', '{"summary": "LATE"}'], gate_at=gate_at)
    await _run_gated(
        rt,
        lambda: run_mission_background(m.id, db_factory=TestingSessionLocal, runtime=rt),
        lambda: _cancel_via_endpoint(m.id),
    )
    status, summary, tasks = _fresh(m.id)
    assert status == "cancelled"
    assert summary is None
    for s, out in tasks:
        assert s in ("cancelled", "succeeded")  # succeeded only if finished before cancel (synthesis stage)
        if gate_at < 2:
            assert s == "cancelled" and out is None


# -------------------------------------------- real multi-connection race

def test_threaded_cancel_vs_complete_is_never_mixed(tmp_path):
    """Hammer cancel vs completion from separate connections (file SQLite).

    Invariant: either (mission succeeded, task succeeded) or
    (mission cancelled, task cancelled) - never a cancelled mission with a
    succeeded/running task body written after the cancel, nor the reverse."""
    engine = create_engine(f"sqlite:///{tmp_path / 'race.db'}",
                           connect_args={"check_same_thread": False, "timeout": 30},
                           poolclass=NullPool)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    from datetime import datetime, timezone

    seed = Session()
    ids = []
    for i in range(40):
        m = Mission(objective=f"o{i}", status="running")
        seed.add(m)
        seed.flush()
        t = MissionTask(mission_id=m.id, task_key="k", employee_role="r", title="t",
                        instructions="i", status="running")
        seed.add(t)
        seed.flush()
        ids.append((m.id, t.id))
    seed.commit()
    seed.close()

    errors = []
    barrier = threading.Barrier(2)

    def complete(mid, tid):
        s = Session()
        try:
            barrier.wait(10)
            now = datetime.now(timezone.utc)
            ok = transition_task(s, tid, ("running",), commit=False, status="succeeded",
                                 output_json="{}", finished_at=now) and \
                transition_mission(s, mid, MISSION_NON_TERMINAL, commit=False, status="succeeded",
                                   finished_at=now)
            s.commit() if ok else s.rollback()
        except Exception as e:  # pragma: no cover
            errors.append(e)
        finally:
            s.close()

    def cancel(mid):
        s = Session()
        try:
            barrier.wait(10)
            cancel_mission_rows(s, mid)
        except Exception as e:  # pragma: no cover
            errors.append(e)
        finally:
            s.close()

    for mid, tid in ids:
        barrier.reset()
        a = threading.Thread(target=complete, args=(mid, tid))
        b = threading.Thread(target=cancel, args=(mid,))
        a.start(); b.start(); a.join(30); b.join(30)

    assert not errors, errors
    s = Session()
    outcomes = set()
    for mid, tid in ids:
        m = s.get(Mission, mid)
        t = s.get(MissionTask, tid)
        pair = (m.status, t.status)
        assert pair in {("succeeded", "succeeded"), ("cancelled", "cancelled")}, pair
        if pair[0] == "cancelled":
            assert t.output_json is None
        outcomes.add(pair)
    s.close()
    engine.dispose()
    assert outcomes  # at least one outcome observed
