"""
Usage Ledger tests:

1. run_agent_task is the ONLY sanctioned path to runtime.execute_task for
   feature code, and it records: kind, agent, model, duration, tokens,
   success/failure.
2. Recording never breaks the caller (ledger write failure is swallowed),
   and failures/cancellations are recorded, not lost.
3. GET /api/usage/summary and /api/usage/events expose the ledger.
4. The consult chat path records usage end-to-end through the API.
"""
from __future__ import annotations

import asyncio

import pytest

from backend.app.config import settings
from backend.app.db.models import UsageEvent
from backend.app.runtime.errors import RuntimeModelUnavailable
from backend.app.runtime.ledger import (
    run_agent_task,
    KIND_CHAT_CONSULT,
    KIND_CEO_ASK,
)
from backend.app.runtime.mock_runtime import MockRuntime
from backend.app.runtime.base import AgentResult, ExecuteAgentTask


class _FailingRuntime(MockRuntime):
    async def execute_task(self, task: ExecuteAgentTask) -> AgentResult:
        raise RuntimeError("boom — model exploded")


def _task(**overrides):
    base = dict(
        prompt="Role: manager. Answer.",
        model_id="muse-spark-1.3-contributor-free",
        variant="high",
        session_title="ledger-test",
        timeout=30,
    )
    base.update(overrides)
    return ExecuteAgentTask(**base)


def _rows(db_session):
    db_session.expire_all()
    return db_session.query(UsageEvent).all()


# --------------------------------------------------------------------------- #
# Facade contract
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_success_records_full_event(db_session):
    res = await run_agent_task(MockRuntime(), _task(), kind=KIND_CEO_ASK)

    events = _rows(db_session)
    assert len(events) == 1
    e = events[0]
    assert e.kind == KIND_CEO_ASK
    assert e.success is True
    assert e.error_kind is None
    assert e.model_id == res.model_id
    assert e.provider_id == "opencode"
    assert e.variant_id == res.variant_id
    assert e.session_id == res.session_id
    assert e.duration_ms >= 0
    # MockRuntime declares its tokens; the ledger must keep them
    assert e.tokens_in == 45
    assert e.tokens_out == 30
    # default work agent for unlabelled tasks
    assert e.agent_id == settings.WORK_AGENT_ID


@pytest.mark.asyncio
async def test_cache_read_tokens_are_recorded(db_session):
    """OpenCode splits input vs cache reads; the ledger must keep both."""
    class _CachedRuntime:
        def __init__(self):
            self._inner = MockRuntime()

        async def execute_task(self, task: ExecuteAgentTask) -> AgentResult:
            result = await self._inner.execute_task(task)
            result.tokens = {
                "input": 45, "output": 30, "reasoning": 0,
                "cache": {"read": 11000, "write": 120},
            }
            return result

    await run_agent_task(_CachedRuntime(), _task(), kind=KIND_CEO_ASK)

    e = _rows(db_session)[0]
    assert e.tokens_in == 45
    assert e.tokens_out == 30
    assert e.tokens_cache_read == 11000


@pytest.mark.asyncio
async def test_failure_records_error_and_reraises(db_session):
    runtime = _FailingRuntime()

    with pytest.raises(RuntimeError, match="boom"):
        await run_agent_task(runtime, _task(), kind=KIND_CEO_ASK)

    events = _rows(db_session)
    assert len(events) == 1
    e = events[0]
    assert e.success is False
    assert e.error_kind == "RuntimeError"
    assert "boom" not in (e.error_detail or "")
    assert e.model_id == "muse-spark-1.3-contributor-free"
    assert e.tokens_in is None  # no tokens were produced — never fake them


@pytest.mark.asyncio
async def test_cancellation_is_recorded_then_reraised(db_session):
    class _CancelledRuntime(MockRuntime):
        async def execute_task(self, task: ExecuteAgentTask) -> AgentResult:
            raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await run_agent_task(_CancelledRuntime(), _task(), kind=KIND_CEO_ASK)

    events = _rows(db_session)
    assert len(events) == 1
    assert events[0].success is False
    assert events[0].error_kind == "CancelledError"


@pytest.mark.asyncio
async def test_broken_ledger_never_breaks_the_task(monkeypatch):
    """A ledger failure must be swallowed: the model answer still arrives."""
    def _broken_factory():
        raise RuntimeError("disk on fire")

    monkeypatch.setattr("backend.app.runtime.ledger.session_factory", _broken_factory)

    res = await run_agent_task(MockRuntime(), _task(), kind=KIND_CEO_ASK)
    assert res.text  # task succeeded despite the ledger being broken


# --------------------------------------------------------------------------- #
# Mock/real runtime contract parity (PR1 lockdown holds in BOTH runtimes)
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_mock_runtime_also_rejects_unrestricted_build_agent():
    """The lockdown is a runtime CONTRACT, not an implementation detail."""
    from backend.app.runtime.mock_runtime import MockRuntime as _Mock

    with pytest.raises(RuntimeModelUnavailable, match="not allowed"):
        await _Mock().execute_task(_task(agent_id="build"))


# --------------------------------------------------------------------------- #
# API surface
# --------------------------------------------------------------------------- #

def test_summary_identifies_missing_token_counts(db_session):
    from backend.app.api.routes.usage import usage_summary

    db_session.add(UsageEvent(kind="unknown_usage", agent_id="x", model_id="m", provider_id="p",
                              duration_ms=1, success=True, tokens_in=None, tokens_out=None))
    db_session.commit()
    result = usage_summary(db_session)
    assert result.totals.token_usage_unknown_events == 1


def test_usage_summary_and_events_api(db_session):
    db_session.add_all([
        UsageEvent(kind=KIND_CHAT_CONSULT, agent_id="agentos-chat", model_id="m-a",
                   provider_id="opencode", variant_id="high", session_id="s1",
                   duration_ms=120, tokens_in=10, tokens_out=5, success=True),
        UsageEvent(kind=KIND_CHAT_CONSULT, agent_id="agentos-chat", model_id="m-a",
                   provider_id="opencode", variant_id="high", session_id="s2",
                   duration_ms=80, tokens_in=8, tokens_out=4, success=False,
                   error_kind="RuntimeError", error_detail="x"),
        UsageEvent(kind=KIND_CEO_ASK, agent_id="agentos-chat", model_id="m-b",
                   provider_id="opencode", duration_ms=200, tokens_in=20,
                   tokens_out=10, success=True),
    ])
    db_session.commit()

    # Summary via API (TestClient from conftest is heavy here; use route funcs.)
    from fastapi.testclient import TestClient
    from backend.app.main import app
    from backend.app.db.session import get_db

    def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    try:
        client = TestClient(app)
        summary = client.get("/api/usage/summary").json()
        assert summary["totals"]["events"] == 3
        assert summary["totals"]["succeeded"] == 2
        assert summary["totals"]["failed"] == 1
        assert summary["totals"]["total_duration_ms"] == 400
        assert summary["by_model"][0]["key"] in ("m-a", "m-b")
        # Column-order regression guard: by_* rows must carry counts in
        # succeeded/failed and TOKENS in tokens_in/tokens_out (a positional
        # mismatch once put token sums where success counts belong).
        ma = summary["by_model"][0] if summary["by_model"][0]["key"] == "m-a" else summary["by_model"][1]
        mb = summary["by_model"][0] if summary["by_model"][0]["key"] == "m-b" else summary["by_model"][1]
        assert ma["events"] == 2 and ma["succeeded"] == 1 and ma["failed"] == 1
        assert ma["total_duration_ms"] == 200 and ma["tokens_in"] == 18 and ma["tokens_out"] == 9
        assert mb["events"] == 1 and mb["succeeded"] == 1 and mb["failed"] == 0
        assert mb["tokens_in"] == 20 and mb["tokens_out"] == 10 and mb["failed"] == 0
        kinds = {g["key"]: g for g in summary["by_kind"]}
        assert kinds[KIND_CHAT_CONSULT]["events"] == 2
        assert kinds[KIND_CHAT_CONSULT]["succeeded"] == 1 and kinds[KIND_CHAT_CONSULT]["failed"] == 1
        assert kinds[KIND_CHAT_CONSULT]["tokens_in"] == 18

        events = client.get("/api/usage/events", params={"limit": 10}).json()
        assert len(events) == 3
        only_chat = client.get("/api/usage/events", params={"kind": KIND_CHAT_CONSULT}).json()
        assert len(only_chat) == 2
        only_failed = client.get("/api/usage/events", params={"success": False}).json()
        assert len(only_failed) == 1
        assert only_failed[0]["error_kind"] == "RuntimeError"
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_consult_chat_records_usage(db_session, client):
    """End-to-end: POST /api/chat must leave exactly one usage row."""
    before = len(_rows(db_session))
    res = client.post("/api/chat", json={"manager_id": "dr_sara", "message": "استشارة سريعة"})
    assert res.status_code == 200, res.text

    rows = _rows(db_session)
    assert len(rows) == before + 1
    e = rows[-1]
    assert e.kind == KIND_CHAT_CONSULT
    assert e.success is True
    assert e.agent_id == settings.CHAT_AGENT_ID
