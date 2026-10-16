"""
Health Check Routes
Provides endpoints for overall system health and AI runtime readiness.
"""

from fastapi import APIRouter, Depends
from backend.app.config import settings
from backend.app.runtime import get_runtime, AgentRuntime
from backend.app.schemas.model import SystemHealthResponse, RuntimeHealthResponse

router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health", response_model=SystemHealthResponse)
def get_system_health():
    """
    Returns core AgentOS application health.
    """
    return SystemHealthResponse(
        status="ok",
        app_name=settings.APP_NAME,
        version=settings.APP_VERSION
    )


@router.get("/runtime/health", response_model=RuntimeHealthResponse)
async def get_runtime_health(runtime: AgentRuntime = Depends(get_runtime)):
    """
    Returns AI Runtime status and availability of the free default model.
    """
    health = await runtime.check_health()
    return RuntimeHealthResponse(
        status=health.status,
        service_url=health.service_url,
        default_model_status=health.default_model_status,
        models_count=health.models_count,
        free_models_count=health.free_models_count
    )
