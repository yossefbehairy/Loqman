"""
Thread sessions (PR6): conversation continuity via stable-title reuse.

The complaint: every conversation message opened a brand-new OpenCode session,
so the persona remembered nothing between turns (and the CEO loop opened one
session PER ITERATION).

Contract now:
- stable titles (consult-*, ceo-ask-*, ceo-fast-*) reuse ONE OpenCode thread:
  cached in-process, and durably re-found by title after an app restart;
- worker one-shots (missions/planner/delegate...) always open a fresh
  stateless session — fresh context is intentional for handovers;
- a stale cached id self-heals: the durable title lookup re-finds the live
  thread or opens a clean replacement.
"""
import pytest

from backend.app.config import settings
from backend.app.runtime.base import ExecuteAgentTask
from backend.app.runtime.mock_runtime import MockRuntime
from backend.app.runtime.opencode_runtime import RealOpenCodeRuntime


def _fake_transport(created, existing):
    """Swap _run_cli_api for a session-list/create stub; anything else = bug."""

    async def fake(method, path, data=None, timeout=90):
        if method == "GET" and path == "/api/session":
            return {"data": list(existing)}
        if method == "POST" and path == "/api/session":
            created.append(data)
            sid = f"ses-new-{len(created)}"
            existing.append({"id": sid, "title": (data or {}).get("title")})
            return {"data": {"id": sid}}
        raise AssertionError(f"unexpected transport call {method} {path}")

    return fake


# ---------------------------------------------------------------------------
# RealOpenCodeRuntime thread behavior
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_thread_title_reuses_existing_session_without_creating():
    r = RealOpenCodeRuntime()
    created: list = []
    r._run_cli_api = _fake_transport(created, [{"id": "ses-keep", "title": "consult-m1"}])

    first = await r._get_or_create_session("consult-m1", settings.CHAT_AGENT_ID, {})
    second = await r._get_or_create_session("consult-m1", settings.CHAT_AGENT_ID, {})

    assert first == ("ses-keep", True)
    assert second == ("ses-keep", True)
    assert created == []  # found durably by title — no new session opened


@pytest.mark.asyncio
async def test_thread_title_opens_once_then_caches():
    r = RealOpenCodeRuntime()
    created: list = []
    r._run_cli_api = _fake_transport(created, [])

    first = await r._get_or_create_session("consult-m1", settings.CHAT_AGENT_ID, {})
    second = await r._get_or_create_session("consult-m1", settings.CHAT_AGENT_ID, {})

    assert first == ("ses-new-1", False)
    assert second == ("ses-new-1", True)
    assert len(created) == 1


@pytest.mark.asyncio
async def test_worker_sessions_are_always_fresh():
    """Handover one-shots MUST be stateless: same title never means same thread."""
    r = RealOpenCodeRuntime()
    created: list = []
    r._run_cli_api = _fake_transport(created, [])

    a = await r._get_or_create_session("planner-x1", settings.WORK_AGENT_ID, {})
    b = await r._get_or_create_session("planner-x1", settings.WORK_AGENT_ID, {})

    assert a == ("ses-new-1", False)
    assert b == ("ses-new-2", False)
    assert len(created) == 2


@pytest.mark.asyncio
async def test_deleted_thread_creates_replacement():
    """Stale cache + vanished session → clean replacement, never an error loop."""
    r = RealOpenCodeRuntime()
    created: list = []
    existing: list = [{"id": "ses-old", "title": "consult-m1"}]
    r._run_cli_api = _fake_transport(created, existing)
    r._thread_sessions["consult-m1"] = "ses-old"

    existing.clear()  # OpenCode lost the session behind our back
    sid, reused = await r._get_or_create_session("consult-m1", settings.CHAT_AGENT_ID, {})

    assert sid == "ses-new-1"
    assert reused is False
    assert r._thread_sessions["consult-m1"] == "ses-new-1"
    assert len(created) == 1


# ---------------------------------------------------------------------------
# MockRuntime contract parity
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_mock_runtime_thread_parity():
    mock = MockRuntime()

    def task(title: str) -> ExecuteAgentTask:
        return ExecuteAgentTask(
            prompt="hello from the user",
            model_id="muse-spark-1.3-contributor-free",
            variant="high",
            session_title=title,
            agent_id=settings.CHAT_AGENT_ID,
        )

    a = await mock.execute_task(task("consult-dr_sara"))
    b = await mock.execute_task(task("consult-dr_sara"))
    worker = await mock.execute_task(task("planner-x1"))

    assert a.session_id == b.session_id  # same thread
    assert a.session_id.startswith("mock-thread-consult-dr_sara")
    assert worker.session_id == "mock-session-id"  # stateless one-shot


# ---------------------------------------------------------------------------
# Route-level: the consult lane is keyed by the manager, not by uuid
# ---------------------------------------------------------------------------

def test_consult_route_uses_stable_manager_thread_title(client, monkeypatch):
    from backend.app.api.routes import chat as chat_mod

    titles: list = []
    real_run = chat_mod.run_agent_task

    async def spy(runtime, task, kind=None):
        titles.append(task.session_title)
        return await real_run(runtime, task, kind=kind)

    monkeypatch.setattr(chat_mod, "run_agent_task", spy)

    payload = {"manager_id": "dr_sara", "message": "أزيك يا دكتورة سارة؟"}
    r1 = client.post("/api/chat", json=payload)
    r2 = client.post("/api/chat", json=payload)

    assert r1.status_code == 200 and r2.status_code == 200
    assert titles == ["consult-dr_sara", "consult-dr_sara"]
    # No uuid suffix: two calls to the same manager hit the same thread.
    assert "consult-dr_sara" == titles[0] == titles[1]
