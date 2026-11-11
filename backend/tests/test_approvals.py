"""Approval state tests: races, cross-run isolation, duplicates, expiry, lifecycle."""
import asyncio
import threading
from datetime import timedelta

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import create_engine, event, update
from sqlalchemy.orm import sessionmaker

from backend.app.api.routes import approvals as routes
from backend.app.core.approvals import (
    APPROVAL_CAPABILITIES, ApprovalConflict, ApprovalManager, ApprovalNotFound, RunNotActive, _now,
)
from backend.app.core.conversation_runs import ConversationRunManager
from backend.app.db.models import Approval, Conversation, ConversationRun
from backend.app.db.session import Base
from backend.app.runtime.base import AgentResult


@pytest.fixture
def factory(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path / 'ap.db'}", connect_args={"check_same_thread": False})

    @event.listens_for(eng, "connect")
    def _pragma(conn, _):
        cur = conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON;")
        cur.execute("PRAGMA busy_timeout=5000;")
        cur.close()

    Base.metadata.create_all(bind=eng)
    yield sessionmaker(autocommit=False, autoflush=False, bind=eng)
    eng.dispose()


def make_run(factory, status="running"):
    db = factory()
    conv = Conversation(title="t")
    db.add(conv)
    db.flush()
    run = ConversationRun(conversation_id=conv.id, engine="opencode", model_id="m", status=status)
    db.add(run)
    db.commit()
    rid = run.id
    db.close()
    return rid


def set_status(factory, rid, status):
    db = factory()
    db.execute(update(ConversationRun).where(ConversationRun.id == rid).values(status=status))
    db.commit()
    db.close()


def status_of(factory, aid):
    db = factory()
    try:
        return db.get(Approval, aid).status
    finally:
        db.close()


def test_create_only_for_active_run(factory):
    m = ApprovalManager(factory)
    for st in ("queued", "running"):
        assert m.create(make_run(factory, st), "write file", "a.txt")["status"] == "pending"
    for st in ("succeeded", "failed", "cancelled", "interrupted"):
        with pytest.raises(RunNotActive):
            m.create(make_run(factory, st), "x")
    with pytest.raises(RunNotActive):
        m.create("missing", "x")


def test_approve_once_and_duplicate_rejected(factory):
    m = ApprovalManager(factory)
    rid = make_run(factory)
    a = m.create(rid, "w")
    assert m.decide(rid, a["id"], "approve", "ok")["status"] == "approved"
    with pytest.raises(ApprovalConflict) as e:
        m.decide(rid, a["id"], "approve")
    assert e.value.status == "approved"
    with pytest.raises(ApprovalConflict):
        m.decide(rid, a["id"], "deny")  # cannot flip
    assert status_of(factory, a["id"]) == "approved"


def test_no_cross_run_decision(factory):
    m = ApprovalManager(factory)
    r1, r2 = make_run(factory), make_run(factory)
    a = m.create(r1, "w")
    with pytest.raises(ApprovalNotFound):
        m.decide(r2, a["id"], "approve")
    assert status_of(factory, a["id"]) == "pending"
    assert m.list_pending(r2) == []
    assert [x["id"] for x in m.list_pending(r1)] == [a["id"]]
    with pytest.raises(ApprovalNotFound):
        m.decide(r1, "nope", "approve")


def test_concurrent_decisions_single_winner(factory):
    m = ApprovalManager(factory)
    rid = make_run(factory)
    a = m.create(rid, "w")
    results, barrier = [], threading.Barrier(8)

    def go(i):
        barrier.wait()
        try:
            results.append(m.decide(rid, a["id"], "approve" if i % 2 else "deny")["status"])
        except ApprovalConflict:
            results.append("conflict")

    ts = [threading.Thread(target=go, args=(i,)) for i in range(8)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len([r for r in results if r != "conflict"]) == 1
    assert results.count("conflict") == 7


def test_expired_by_time_rejected_and_hidden(factory):
    m = ApprovalManager(factory)
    rid = make_run(factory)
    a = m.create(rid, "w")
    db = factory()
    db.execute(update(Approval).where(Approval.id == a["id"]).values(expires_at=_now() - timedelta(seconds=1)))
    db.commit()
    db.close()
    with pytest.raises(ApprovalConflict) as e:
        m.decide(rid, a["id"], "approve")
    assert e.value.status == "expired"
    assert m.get(a["id"])["expired_reason"] == "timeout"
    b = m.create(rid, "w2")
    db = factory()
    db.execute(update(Approval).where(Approval.id == b["id"]).values(expires_at=_now() - timedelta(seconds=1)))
    db.commit()
    db.close()
    assert m.list_pending(rid) == []


def test_late_decision_after_run_terminal_rejected(factory):
    """Run ended without going through lifecycle hooks (e.g. direct status flip):
    the decision predicate itself still refuses."""
    m = ApprovalManager(factory)
    rid = make_run(factory)
    a = m.create(rid, "w")
    set_status(factory, rid, "cancelled")
    with pytest.raises(ApprovalConflict) as e:
        m.decide(rid, a["id"], "approve")
    assert e.value.status == "expired"
    assert m.get(a["id"])["expired_reason"] == "run_cancelled"


def test_create_vs_terminal_race_leaves_no_pending(factory):
    m = ApprovalManager(factory)
    for _ in range(10):
        rid = make_run(factory)
        barrier = threading.Barrier(2)

        def creator():
            barrier.wait()
            try:
                m.create(rid, "w")
            except RunNotActive:
                pass

        def canceller():
            barrier.wait()
            mgr = ConversationRunManager(session_factory=factory, runtime_factory=lambda e: None)
            asyncio.run(mgr.cancel(rid))

        ts = [threading.Thread(target=creator), threading.Thread(target=canceller)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        assert m.list_pending(rid) == []


class Rt:
    async def execute_task(self, task):
        await asyncio.sleep(0)
        return AgentResult(text="ok", model_id=task.model_id)

    async def abort_task(self, session_id):
        pass


def test_cancel_expires_pending_approvals(factory):
    m = ApprovalManager(factory)
    rid = make_run(factory)
    a = m.create(rid, "w")
    mgr = ConversationRunManager(session_factory=factory, runtime_factory=lambda e: Rt())
    asyncio.run(mgr.cancel(rid))
    got = m.get(a["id"])
    assert (got["status"], got["expired_reason"]) == ("expired", "run_cancelled")
    with pytest.raises(ApprovalConflict):
        m.decide(rid, a["id"], "approve")


def test_recover_interrupted_expires(factory):
    m = ApprovalManager(factory)
    rid = make_run(factory, "running")
    a = m.create(rid, "w")
    ConversationRunManager(session_factory=factory).recover_interrupted()
    assert m.get(a["id"])["expired_reason"] == "run_interrupted"


def test_fail_and_complete_expire(factory):
    m = ApprovalManager(factory)
    mgr = ConversationRunManager(session_factory=factory)
    r1, r2 = make_run(factory), make_run(factory)
    a1, a2 = m.create(r1, "w"), m.create(r2, "w")
    assert mgr._transition(r1, "running", "failed", error="boom")
    assert mgr._complete(r2, "done")
    assert m.get(a1["id"])["expired_reason"] == "run_failed"
    assert m.get(a2["id"])["expired_reason"] == "run_succeeded"


def test_start_turn_end_to_end_expiry(factory):
    """Real start_turn -> approval -> run finishes -> approval expired."""
    async def go():
        db = factory()
        conv = Conversation(title="t")
        db.add(conv)
        db.commit()
        cid = conv.id
        db.close()
        mgr = ConversationRunManager(session_factory=factory, runtime_factory=lambda e: Rt())
        snap = mgr.start_turn(cid, "opencode", "m", "hi")
        m = ApprovalManager(factory)
        a = m.create(snap["id"], "w")  # task has not had a chance to run yet
        for _ in range(100):
            await asyncio.sleep(0.02)
            if mgr.get_run(snap["id"])["status"] == "succeeded":
                break
        return m.get(a["id"])

    got = asyncio.run(go())
    assert got is not None and got["status"] == "expired"
    assert got["expired_reason"] == "run_succeeded"


def test_capabilities_all_unsupported():
    assert APPROVAL_CAPABILITIES and all(v["tool_approvals"] is False for v in APPROVAL_CAPABILITIES.values())


def test_routes(factory):
    m = ApprovalManager(factory)
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[routes.get_approval_manager] = lambda: m
    r1, r2 = make_run(factory), make_run(factory)
    a = m.create(r1, "write x", "x.txt")

    async def go():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            assert (await c.get("/api/approvals/capabilities")).json()["codex"]["tool_approvals"] is False
            assert len((await c.get("/api/approvals")).json()) == 1
            assert (await c.get(f"/api/conversation-runs/{r2}/approvals")).json() == []
            url = f"/api/conversation-runs/{r2}/approvals/{a['id']}/decision"
            assert (await c.post(url, json={"decision": "approve"})).status_code == 404
            url = f"/api/conversation-runs/{r1}/approvals/{a['id']}/decision"
            assert (await c.post(url, json={"decision": "maybe"})).status_code == 422
            assert (await c.post(url, json={"decision": "deny"})).json()["status"] == "denied"
            assert (await c.post(url, json={"decision": "approve"})).status_code == 409
            assert (await c.post("/api/approvals", json={})).status_code == 405  # no create route

    asyncio.run(go())
