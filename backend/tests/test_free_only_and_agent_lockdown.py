"""Security invariants:

1. Free-only is FAIL-CLOSED: a model is executable only with explicit 0/0
   pricing from an allowlisted provider. Custom/keyed gateways that declare no
   pricing must never be treated as free.
2. Agent lockdown: AgentOS only runs its own locked OpenCode agents. The
   unrestricted "build" agent is rejected before any runtime I/O.
3. Legacy conversations pinned to "build" are re-pointed at the locked agent.
"""
from __future__ import annotations

import pytest

from backend.app.config import settings
from backend.app.db.models import OpenCodeConversation
from backend.app.db.repository import OpenCodeConversationRepository
from backend.app.runtime.base import ExecuteAgentTask
from backend.app.runtime.errors import RuntimeModelUnavailable
from backend.app.runtime.opencode_runtime import RealOpenCodeRuntime


def _raw(**overrides):
    entry = {
        "id": "deepseek-v4.1-flash-free",
        "providerID": "opencode",
        "name": "DeepSeek Flash Free",
        "cost": [{"input": 0.0, "output": 0.0}],
        "variants": [{"id": "low"}, {"id": "high"}],
        "status": "active",
        "enabled": True,
    }
    entry.update(overrides)
    return entry


# ---------------------------------------------------------------------------
# Free-only (fail-closed)
# ---------------------------------------------------------------------------

def test_allowlisted_provider_with_explicit_zero_cost_is_free():
    entry = RealOpenCodeRuntime._entry_from_raw(_raw())
    assert entry.is_free is True
    assert entry.cost.input == 0.0
    assert entry.cost.output == 0.0


def test_non_zero_cost_is_not_free():
    entry = RealOpenCodeRuntime._entry_from_raw(
        _raw(cost=[{"input": 0.5, "output": 1.5}])
    )
    assert entry.is_free is False


def test_missing_pricing_is_not_free():
    """A provider with no declared pricing must fail closed, not default to 0."""
    entry = RealOpenCodeRuntime._entry_from_raw(_raw(cost=None))
    assert entry.is_free is False

    entry = RealOpenCodeRuntime._entry_from_raw(_raw(cost=[]))
    assert entry.is_free is False

    entry = RealOpenCodeRuntime._entry_from_raw(_raw(cost=[{}]))
    assert entry.is_free is False


def test_zero_cost_from_disallowed_provider_is_not_free():
    """A keyed external gateway (e.g. agentrouter) may report 0/0; it is still
    not free because its provider is not allowlisted."""
    entry = RealOpenCodeRuntime._entry_from_raw(_raw(providerID="agentrouter"))
    assert entry.is_free is False
    assert entry.provider_id == "agentrouter"


def test_allowlist_is_configurable(monkeypatch):
    monkeypatch.setattr(settings, "FREE_PROVIDER_ALLOWLIST", ["opencode", "agentrouter"])
    entry = RealOpenCodeRuntime._entry_from_raw(_raw(providerID="agentrouter"))
    assert entry.is_free is True


# ---------------------------------------------------------------------------
# Agent lockdown
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_execute_task_rejects_unrestricted_build_agent():
    runtime = RealOpenCodeRuntime()
    task = ExecuteAgentTask(
        prompt="hello",
        model_id=settings.DEFAULT_MODEL_ID,
        agent_id="build",
    )
    with pytest.raises(RuntimeModelUnavailable, match="not allowed"):
        await runtime.execute_task(task)


@pytest.mark.asyncio
async def test_sdk_proxy_rejects_unrestricted_agent_on_session_create():
    runtime = RealOpenCodeRuntime()
    with pytest.raises(RuntimeModelUnavailable, match="not allowed"):
        await runtime.request_api(
            "POST",
            "/api/session",
            {"agent": "build", "model": {"id": settings.DEFAULT_MODEL_ID, "providerID": "opencode"}},
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/api/session?x=1", "/api/session/../session", "/api/session/abc/prompt"])
async def test_sdk_proxy_rejects_unchecked_post_paths(path):
    runtime = RealOpenCodeRuntime()
    with pytest.raises(RuntimeModelUnavailable, match="not allowed"):
        await runtime.request_api("POST", path, {"agent": "build"})


# ---------------------------------------------------------------------------
# Legacy conversation migration
# ---------------------------------------------------------------------------

def test_legacy_build_conversation_is_repointed_to_locked_agent(db_session):
    legacy = OpenCodeConversation(
        conversation_id="chat-legacy",
        session_id="ses_legacy_build",
        model_id=settings.DEFAULT_MODEL_ID,
        provider_id="opencode",
        agent_id="build",
    )
    db_session.add(legacy)
    db_session.commit()

    repo = OpenCodeConversationRepository(db_session)
    row = repo.save(
        "chat-legacy",
        "ses_locked_chat",
        settings.DEFAULT_MODEL_ID,
        "opencode",
        settings.CHAT_AGENT_ID,
    )

    assert row.agent_id == settings.CHAT_AGENT_ID
    assert row.session_id == "ses_locked_chat"


def test_repository_rejects_unrestricted_agent(db_session):
    repo = OpenCodeConversationRepository(db_session)
    with pytest.raises(ValueError, match="not allowed"):
        repo.save("chat-x", "ses_x", settings.DEFAULT_MODEL_ID, "opencode", "build")
