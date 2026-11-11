"""
Engine discovery API — read-only, never invokes a model.

GET /api/engines/status : installed / authenticated / unknown per engine CLI
"""

import asyncio
from datetime import datetime, timezone
from typing import List, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.app.runtime import engine_discovery
from backend.app.config import settings
from backend.app.runtime.errors import RuntimeErrorBase
from backend.app.runtime.base import RuntimeHealth
from backend.app.runtime.engine_registry import ENGINE_IDS, create_engine_runtime

router = APIRouter(prefix="/api/engines", tags=["engines"])


class EngineStatusOut(BaseModel):
    engine_id: str
    display_name: str
    install_state: Literal["installed", "not_installed", "unknown"]
    auth_state: Literal["authenticated", "unauthenticated", "unknown"]
    version: str | None = None
    executable: str | None = None
    adapter_supported: bool = False
    detail: str


class EngineStatusList(BaseModel):
    engines: List[EngineStatusOut]


@router.get("/capabilities")
def capabilities():
    """Expose only capabilities implemented and verified by this backend."""
    return {
        "opencode": {"conversation_runs": True, "streaming_text": False, "tool_approvals": False, "cancellation": "best_effort_remote_unconfirmed",
                     "workspace_restriction": False, "model_policy": "verified_free_only"},
        "claude-code": {"conversation_runs": True, "streaming_text": False, "tool_approvals": False, "cancellation": "best_effort_remote_unconfirmed",
                        "workspace_restriction": False, "model_policy": "subscription_or_account"},
        "codex": {"conversation_runs": True, "streaming_text": False, "tool_approvals": False, "cancellation": "best_effort_remote_unconfirmed",
                  "workspace_restriction": False, "model_policy": "subscription_or_account"},
        "antigravity": {"conversation_runs": True, "streaming_text": False, "tool_approvals": False, "cancellation": "best_effort_remote_unconfirmed",
                        "workspace_restriction": False, "model_policy": "subscription_or_account",
                        "permission_policy": "plan_mode_and_deny_all_requests"},
        "mock": {"conversation_runs": settings.RUNTIME_MODE == "mock", "streaming_text": False,
                 "tool_approvals": False, "cancellation": "local", "workspace_restriction": False,
                 "model_policy": "simulated"},
    }


@router.get("/status", response_model=EngineStatusList)
def engines_status():
    # Sync endpoint: FastAPI runs it in a threadpool, so blocking probes are fine.
    return EngineStatusList(
        engines=[EngineStatusOut(**vars(s)) for s in engine_discovery.discover_engines()]
    )


def _runtime(engine_id: str):
    if engine_id not in ENGINE_IDS:
        raise HTTPException(status_code=404, detail="Unknown engine")
    return create_engine_runtime(engine_id)


class EngineSetupOut(BaseModel):
    engine_id: str
    login_method: Literal["external_cli", "not_available"]
    command: list[str] | None
    instructions: str
    credentials_handled_by: str = "official_cli"


@router.get("/{engine_id}/setup", response_model=EngineSetupOut)
def engine_setup(engine_id: str):
    commands = {"opencode": ["opencode", "auth", "login"],
                "claude-code": ["claude", "auth", "login"],
                "codex": ["codex", "login"],
                "antigravity": ["agy", "acp", "--login"]}
    if engine_id not in ENGINE_IDS:
        raise HTTPException(status_code=404, detail="Unknown engine")
    command = commands.get(engine_id)
    return EngineSetupOut(
        engine_id=engine_id, login_method="external_cli" if command else "not_available",
        command=command,
        instructions=("Run this command in your terminal and complete the official sign-in, then check the connection."
                      if command else "Sign in through the installed Antigravity application. A separate CLI login command has not been verified."),
    )


class EngineCheckOut(BaseModel):
    engine_id: str
    checked_at: datetime
    discovery: EngineStatusOut
    runtime: RuntimeHealth | None
    error_code: str | None = None
    generation_verified: bool = False
    billing_state: Literal["unknown"] = "unknown"


@router.post("/{engine_id}/check", response_model=EngineCheckOut)
async def check_engine(engine_id: str):
    runtime = _runtime(engine_id)
    spec = next(s for s in engine_discovery.ENGINE_SPECS if s.engine_id == engine_id)
    discovery = await asyncio.to_thread(engine_discovery.probe_engine, spec)
    health = None
    error_code = None
    if discovery.install_state == "installed":
        try:
            health = await asyncio.wait_for(runtime.check_health(), timeout=45)
        except asyncio.TimeoutError:
            error_code = "timeout"
        except RuntimeErrorBase as exc:
            error_code = exc.code
        except Exception:
            error_code = "health_check_failed"
    return EngineCheckOut(engine_id=engine_id, checked_at=datetime.now(timezone.utc),
                          discovery=EngineStatusOut(**vars(discovery)), runtime=health,
                          error_code=error_code)


class EngineModelOut(BaseModel):
    id: str
    name: str
    provider_id: str
    status: str
    is_free: bool
    variants: list[str]


@router.get("/{engine_id}/models", response_model=list[EngineModelOut])
async def engine_models(engine_id: str):
    runtime = _runtime(engine_id)
    try:
        models = await runtime.list_available_models(free_only=False)
    except RuntimeErrorBase as exc:
        raise HTTPException(status_code=502, detail=f"Engine catalog failed ({exc.code}).") from exc
    return [EngineModelOut(id=m.id, name=m.name, provider_id=m.provider_id,
                           status=m.status, is_free=m.is_free, variants=m.variants)
            for m in models]


