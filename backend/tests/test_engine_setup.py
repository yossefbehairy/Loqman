from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.api.routes import engines
from backend.app.runtime.base import RuntimeHealth
from backend.app.runtime.engine_discovery import EngineStatus


def client():
    app = FastAPI()
    app.include_router(engines.router)
    return TestClient(app)


def test_setup_uses_official_cli_commands_without_running_them(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("setup must not invoke a process")
    monkeypatch.setattr(engines, "create_engine_runtime", forbidden)
    api = client()
    for kind, command in (("codex", ["codex", "login"]),
                          ("claude-code", ["claude", "auth", "login"]),
                          ("opencode", ["opencode", "auth", "login"]),
                          ("antigravity", ["agy", "acp", "--login"])):
        response = api.get(f"/api/engines/{kind}/setup")
        assert response.status_code == 200
        assert response.json()["command"] == command
    assert api.get("/api/engines/antigravity/setup").json()["login_method"] == "external_cli"
    assert api.get("/api/engines/gemini/setup").status_code == 404


def status(installed=True):
    return EngineStatus("claude-code", "Claude Code", "installed" if installed else "not_installed",
                        "authenticated" if installed else "unknown", "1.2.3" if installed else None,
                        "/cli" if installed else None, True, "probe")


def test_check_is_status_only_not_generation(monkeypatch):
    class Runtime:
        async def check_health(self):
            return RuntimeHealth(status="ok", default_model_status="unverified", models_count=3, free_models_count=0)

        async def execute_task(self, task):
            raise AssertionError("health check must not invoke a model")

    monkeypatch.setattr(engines, "create_engine_runtime", lambda _: Runtime())
    monkeypatch.setattr(engines.engine_discovery, "probe_engine", lambda _: status())
    result = client().post("/api/engines/claude-code/check")
    assert result.status_code == 200
    assert result.json()["runtime"]["status"] == "ok"
    assert result.json()["generation_verified"] is False
    assert result.json()["billing_state"] == "unknown"


def test_missing_cli_does_not_run_health(monkeypatch):
    class Runtime:
        async def check_health(self):
            raise AssertionError("missing executable")
    monkeypatch.setattr(engines, "create_engine_runtime", lambda _: Runtime())
    monkeypatch.setattr(engines.engine_discovery, "probe_engine", lambda _: status(False))
    result = client().post("/api/engines/claude-code/check")
    assert result.status_code == 200 and result.json()["runtime"] is None


def test_health_exception_is_sanitized(monkeypatch):
    class Runtime:
        async def check_health(self):
            raise RuntimeError("sk-private-token")
    monkeypatch.setattr(engines, "create_engine_runtime", lambda _: Runtime())
    monkeypatch.setattr(engines.engine_discovery, "probe_engine", lambda _: status())
    result = client().post("/api/engines/claude-code/check")
    assert result.json()["error_code"] == "health_check_failed"
    assert "sk-private-token" not in result.text


def test_antigravity_registry_uses_deny_only_acp_adapter():
    from backend.app.runtime.agy_acp_runtime import AgyAcpRuntime
    assert isinstance(engines._runtime("antigravity"), AgyAcpRuntime)
