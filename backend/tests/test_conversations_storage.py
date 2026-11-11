from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.api.routes.conversations import router
from backend.app.db.session import get_db
from backend.app.db.models import CeoMessage, Workspace


def test_conversations_are_isolated_ordered_and_idempotent(db_session):
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = lambda: db_session
    client = TestClient(app)
    first = client.post("/api/conversations", json={"title": "First"}).json()["id"]
    second = client.post("/api/conversations", json={"title": "Second"}).json()["id"]
    payload = {"role": "user", "content": "hello", "client_message_id": "request-1"}
    message = client.post(f"/api/conversations/{first}/messages", json=payload).json()
    duplicate = client.post(f"/api/conversations/{first}/messages", json=payload).json()
    assert message["seq"] == duplicate["seq"]
    assert client.post(f"/api/conversations/{first}/messages", json={**payload, "content": "different"}).status_code == 409
    reply = client.post(f"/api/conversations/{first}/messages", json={"role": "assistant", "content": "hi"}).json()
    assert [m["content"] for m in client.get(f"/api/conversations/{first}/messages").json()] == ["hello", "hi"]
    assert client.get(f"/api/conversations/{first}/messages", params={"after": message["seq"]}).json()[0]["seq"] == reply["seq"]
    assert client.get(f"/api/conversations/{second}/messages").json() == []
    assert client.get("/api/conversations/missing/messages").status_code == 404


def test_ceo_history_import_is_repeatable_and_preserves_original(db_session):
    db_session.add_all([CeoMessage(seq=1, role="user", content="question"),
                        CeoMessage(seq=2, role="assistant", content="answer")])
    db_session.commit()
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = lambda: db_session
    client = TestClient(app)
    result = client.post("/api/conversations/import/ceo").json()
    assert result["imported"] == 2
    assert client.post("/api/conversations/import/ceo").json()["imported"] == 0
    messages = client.get(f"/api/conversations/{result['conversation_id']}/messages").json()
    assert [m["content"] for m in messages] == ["question", "answer"]
    assert db_session.query(CeoMessage).count() == 2


def test_conversation_can_reference_a_registered_workspace(db_session, tmp_path):
    ws = Workspace(name="work", path=str(tmp_path))
    db_session.add(ws)
    db_session.commit()
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = lambda: db_session
    client = TestClient(app)
    response = client.post("/api/conversations", json={"title": "Work", "workspace_id": ws.id})
    assert response.status_code == 201 and response.json()["workspace_id"] == ws.id
    assert client.post("/api/conversations", json={"title": "Bad", "workspace_id": "missing"}).status_code == 404
