from fastapi.testclient import TestClient

from backend.app.main import app


def test_desktop_token_required_for_api(monkeypatch):
    monkeypatch.setenv("AGENTOS_LOCAL_TOKEN", "test-secret")
    client = TestClient(app)
    assert client.get("/api/health").status_code == 401
    assert client.get("/api/health", headers={"X-AgentOS-Token": "wrong"}).status_code == 401
    assert client.get("/api/health", headers={"X-AgentOS-Token": "test-secret"}).status_code == 200
    assert client.get("/api/workspaces").status_code == 401
    assert client.post("/api/backup").status_code == 401


def test_legacy_standalone_mode_without_token(monkeypatch):
    monkeypatch.delenv("AGENTOS_LOCAL_TOKEN", raising=False)
    monkeypatch.setenv("AGENTOS_INSECURE_DEV", "1")
    assert TestClient(app).get("/api/health").status_code == 200


def test_missing_token_fails_closed_and_rejects_untrusted_host(monkeypatch):
    monkeypatch.delenv("AGENTOS_LOCAL_TOKEN", raising=False)
    monkeypatch.delenv("AGENTOS_INSECURE_DEV", raising=False)
    client = TestClient(app, base_url="http://127.0.0.1")
    assert client.get("/api/health").status_code == 503
    monkeypatch.setenv("AGENTOS_LOCAL_TOKEN", "secret")
    assert client.get("/api/health", headers={"Host": "evil.example", "X-AgentOS-Token": "secret"}).status_code == 400


def test_child_runtimes_do_not_inherit_sidecar_token(monkeypatch):
    from backend.app.runtime.claude_runtime import ClaudeCodeRuntime
    from backend.app.runtime.codex_runtime import CodexCliRuntime
    from backend.app.runtime.agy_runtime import AgyCliRuntime

    monkeypatch.setenv("AGENTOS_LOCAL_TOKEN", "test-secret")
    for runtime in (ClaudeCodeRuntime(), CodexCliRuntime(), AgyCliRuntime()):
        assert "AGENTOS_LOCAL_TOKEN" not in runtime._env()
