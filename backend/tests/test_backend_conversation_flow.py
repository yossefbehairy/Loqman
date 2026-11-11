import asyncio

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app.api.routes.conversation_runs import get_run_manager
from backend.app.api.routes.deliverables import get_deliverable_manager
from backend.app.core.conversation_runs import ConversationRunManager
from backend.app.core.deliverables import DeliverableManager
from backend.app.db.session import get_db, init_db
from backend.app.main import app
from backend.app.runtime.base import AgentResult
from backend.app.runtime import ledger


@pytest.mark.asyncio
async def test_authenticated_conversation_flow_and_event_replay(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'flow.db'}", connect_args={"check_same_thread": False})
    init_db(engine)
    factory = sessionmaker(bind=engine, autoflush=False)

    class Runtime:
        async def execute_task(self, task):
            return AgentResult(text="reply", model_id=task.model_id)

        async def abort_task(self, key):
            pass

    manager = ConversationRunManager(session_factory=factory, runtime_factory=lambda _: Runtime())

    def session():
        with factory() as db:
            yield db

    monkeypatch.setenv("AGENTOS_LOCAL_TOKEN", "token")
    monkeypatch.setattr(ledger, "session_factory", factory)
    app.dependency_overrides[get_db] = session
    app.dependency_overrides[get_run_manager] = lambda: manager
    app.dependency_overrides[get_deliverable_manager] = lambda: DeliverableManager(session_factory=factory)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1") as client:
            assert (await client.get("/api/conversations")).status_code == 401
            headers = {"X-AgentOS-Token": "token"}
            created = await client.post("/api/conversations", json={"title": "Flow"}, headers=headers)
            cid = created.json()["id"]
            payload = {"engine": "claude-code", "model_id": "sonnet", "prompt": "hello", "request_key": "one"}
            response = await client.post(f"/api/conversations/{cid}/turns", json=payload, headers=headers)
            rid = response.json()["id"]
            assert response.status_code == 202
            for _ in range(100):
                status = (await client.get(f"/api/conversation-runs/{rid}", headers=headers)).json()
                if status["status"] == "succeeded":
                    break
                await asyncio.sleep(.01)
            assert status["assistant_content"] == "reply"
            events = await client.get(f"/api/conversation-runs/{rid}/events", headers=headers)
            assert "event: queued" in events.text and "event: succeeded" in events.text
            messages = (await client.get(f"/api/conversations/{cid}/messages", headers=headers)).json()
            assert [m["content"] for m in messages] == ["hello", "reply"]
            usage = (await client.get("/api/usage/events", headers=headers)).json()
            assert any(row["run_id"] == rid and row["agent_id"] == "unassigned" for row in usage)
            assert (await client.post(f"/api/conversations/{cid}/turns", json=payload, headers=headers)).json()["id"] == rid
            draft = (await client.post(f"/api/conversation-runs/{rid}/deliverables", json={"filename": "answer.md"}, headers=headers)).json()
            assert draft["status"] == "pending_review" and not draft["written_to_workspace"]
            decision = {"decision": "approve", "content_sha256": draft["content_sha256"]}
            approved = await client.post(f"/api/conversation-runs/{rid}/deliverables/{draft['id']}/decision", json=decision, headers=headers)
            assert approved.json()["status"] == "approved"
            download = await client.get(f"/api/conversation-runs/{rid}/deliverables/{draft['id']}/content", headers=headers)
            assert download.text == "reply"
    finally:
        await manager.shutdown()
        app.dependency_overrides.clear()
        engine.dispose()
