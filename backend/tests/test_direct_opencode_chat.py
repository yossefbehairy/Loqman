"""Acceptance coverage for persistent direct chat and explicitly delegated work."""
from __future__ import annotations

from backend.app.runtime.mock_runtime import MockRuntime
from backend.app.runtime.base import AgentResult
from backend.app.runtime import set_runtime


class SessionRuntime(MockRuntime):
    def __init__(self):
        super().__init__()
        self.sessions = {}
        self.calls = []
        self.fail = False

    async def request_api(self, method, path, data=None, timeout=180):
        if self.fail:
            raise RuntimeError("OpenCode disconnected")
        self.calls.append((method, path, data))
        if method == "POST" and path == "/api/session":
            session_id = f"session-{len(self.sessions) + 1}"
            self.sessions[session_id] = []
            return {"data": {"id": session_id}}
        parts = path.split("?", 1)[0].strip("/").split("/")
        session_id = parts[2]
        if len(parts) >= 4 and parts[3] == "message" and method == "POST":
            text = (data.get("parts") or [{}])[0].get("text", "")
            self.sessions[session_id].append({"info": {"role": "user"}, "parts": [{"type": "text", "text": text}]})
            answer = f"OpenCode response to: {text}"
            self.sessions[session_id].append({"info": {"role": "assistant"}, "parts": [{"type": "text", "text": answer}]})
            return {"data": {"info": {"role": "assistant"}, "parts": [{"type": "text", "text": answer}]}}
        if len(parts) >= 4 and parts[3] == "message" and method == "GET":
            return {"data": self.sessions[session_id]}
        if method == "GET":
            return {"data": {"id": session_id}}
        raise AssertionError((method, path))

    async def execute_task(self, task):
        self.calls.append(("execute_task", task.model_id, task.variant, task.prompt))
        return AgentResult(
            text="Quality review: concrete issue found in the project plan.",
            model_id=task.model_id,
            variant_id=task.variant,
            session_id="delegated-session",
        )


def _select_runtime():
    runtime = SessionRuntime()
    set_runtime(runtime)
    return runtime


def _create_direct_session(client, runtime, conversation="chat-a"):
    model_id = "muse-spark-1.3-contributor-free"
    create = client.post("/api/opencode-proxy/session", json={
        "title": "Acceptance chat", "agent": "build",
        "model": {"id": model_id, "providerID": "opencode", "variant": "high"},
    })
    assert create.status_code == 200, create.text
    session_id = create.json()["data"]["id"]
    saved = client.post(f"/api/chat/sessions/{conversation}", json={
        "session_id": session_id, "model_id": model_id,
        "provider_id": "opencode", "agent_id": "build",
    })
    assert saved.status_code == 200, saved.text
    return session_id, model_id


def _prompt(client, session_id, text, model_id="muse-spark-1.3-contributor-free"):
    return client.post(f"/api/opencode-proxy/session/{session_id}/message", json={
        "agent": "build",
        "model": {"providerID": "opencode", "modelID": model_id},
        "variant": "high",
        "parts": [{"type": "text", "text": text}],
    })


def test_acceptance_a_normal_chat_is_direct_and_creates_no_mission(client):
    runtime = _select_runtime()
    session_id, model_id = _create_direct_session(client, runtime)
    response = _prompt(client, session_id, "Hey, explain what a workflow execution engine does.")
    assert response.status_code == 200
    assert "OpenCode response" in response.json()["data"]["parts"][0]["text"]
    assert runtime.calls[-1][2]["model"]["modelID"] == model_id
    assert client.get("/api/missions").json() == []


def test_acceptance_b_coding_chat_reuses_session_without_employee_tasks(client):
    runtime = _select_runtime()
    session_id, _ = _create_direct_session(client, runtime)
    first = _prompt(client, session_id, "Explain the login flow.")
    second = _prompt(client, session_id, "Inspect this repository and explain why the login flow fails.")
    assert first.status_code == second.status_code == 200
    assert [call[1] for call in runtime.calls if call[0] == "POST" and "/message" in call[1]] == [
        f"/api/session/{session_id}/message", f"/api/session/{session_id}/message"
    ]
    assert client.get("/api/missions").json() == []


def test_acceptance_c_explicit_employee_task_executes_and_saves_output(client):
    runtime = _select_runtime()
    response = client.post("/api/missions/delegate", json={
        "conversation_id": "chat-a", "employee_role": "reviewer",
        "title": "Review project plan", "instructions": "List concrete problems in the project plan.",
        "input_context": "Current project plan", "expected_output": "Concrete issues",
        "model_id": "muse-spark-1.3-contributor-free", "variant": "high",
    })
    assert response.status_code == 201, response.text
    mission = client.get(f"/api/missions/{response.json()['id']}").json()
    assert mission["status"] == "succeeded", f"{mission.get('error_summary')} | {mission['tasks']}"
    assert len(mission["tasks"]) == 1
    assert mission["tasks"][0]["employee_role"] == "reviewer"
    assert mission["tasks"][0]["output"]["result"] == "Quality review: concrete issue found in the project plan."
    execution = next(call for call in runtime.calls if call[0] == "execute_task")
    assert "reviewer" in execution[3].lower()
    assert "concrete problems" in execution[3]


def test_acceptance_d_delegation_uses_separate_context_and_chat_resumes(client):
    runtime = _select_runtime()
    session_id, _ = _create_direct_session(client, runtime)
    _prompt(client, session_id, "What does a workflow engine do?")
    _prompt(client, session_id, "Thanks. Explain how that differs from a queue.")
    mission_response = client.post("/api/missions/delegate", json={
        "conversation_id": "chat-a", "employee_role": "reviewer",
        "title": "Review plan", "instructions": "Review the current project plan.",
        "model_id": "muse-spark-1.3-contributor-free", "variant": "high",
    })
    assert mission_response.status_code == 201
    mapped = client.get("/api/chat/sessions/chat-a").json()
    assert mapped["session_id"] == session_id
    assert client.get(f"/api/missions/{mission_response.json()['id']}").json()["tasks"][0]["session_id"] == "delegated-session"
    resumed = _prompt(client, session_id, "Continue our conversation.")
    assert resumed.status_code == 200


def test_acceptance_e_runtime_failure_is_reported_without_fallback_or_mission(client):
    runtime = _select_runtime()
    runtime.fail = True
    response = client.post("/api/opencode-proxy/session", json={
        "model": {"id": "muse-spark-1.3-contributor-free", "providerID": "opencode"},
        "agent": "build",
    })
    assert response.status_code == 502
    assert "OpenCode request failed" in response.json()["detail"]
    assert client.get("/api/missions").json() == []


def test_acceptance_f_refresh_reads_same_session_and_real_history(client):
    runtime = _select_runtime()
    session_id, _ = _create_direct_session(client, runtime)
    _prompt(client, session_id, "First message")
    _prompt(client, session_id, "Second message")
    saved = client.get("/api/chat/sessions/chat-a").json()
    messages = client.get(f"/api/opencode-proxy/session/{saved['session_id']}/message").json()["data"]
    assert saved["session_id"] == session_id
    assert [item["info"]["role"] for item in messages] == ["user", "assistant", "user", "assistant"]
    assert client.get("/api/missions").json() == []


def test_session_proxy_rejects_models_not_verified_as_free(client):
    _select_runtime()
    response = client.post("/api/opencode-proxy/session", json={
        "model": {"id": "paid-model", "providerID": "opencode"}, "agent": "build",
    })
    assert response.status_code == 400
    assert "not verified as free" in response.json()["detail"]
