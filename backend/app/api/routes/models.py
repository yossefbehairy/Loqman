"""
Model Catalog Routes
Exposes strictly verified zero-cost models and catalog refresh operations.
"""

from typing import List
from fastapi import APIRouter, Depends
from backend.app.runtime import get_runtime, AgentRuntime
from backend.app.schemas.model import FreeModelResponse

router = APIRouter(prefix="/api/models", tags=["models"])


@router.get("/free", response_model=List[FreeModelResponse])
async def get_free_models(runtime: AgentRuntime = Depends(get_runtime)):
    """
    Returns only models whose input and output cost are verified to be explicitly 0.
    """
    models = await runtime.list_available_models(free_only=True)
    return [
        FreeModelResponse(
            id=m.id,
            name=m.name,
            provider_id=m.provider_id,
            input_cost=m.cost.input,
            output_cost=m.cost.output,
            variants=m.variants,
            is_free=m.is_free,
            status=m.status
        )
        for m in models
    ]


@router.post("/refresh", response_model=List[FreeModelResponse])
async def refresh_models(runtime: AgentRuntime = Depends(get_runtime)):
    """
    Refreshes the model catalog from OpenCode.
    """
    models = await runtime.list_available_models(free_only=True)
    return [
        FreeModelResponse(
            id=m.id,
            name=m.name,
            provider_id=m.provider_id,
            input_cost=m.cost.input,
            output_cost=m.cost.output,
            variants=m.variants,
            is_free=m.is_free,
            status=m.status
        )
        for m in models
    ]
