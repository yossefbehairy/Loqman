"""
Model and Health Pydantic Schemas
"""

from typing import List, Optional
from pydantic import BaseModel


class FreeModelResponse(BaseModel):
    id: str
    name: str
    provider_id: str
    input_cost: float = 0.0
    output_cost: float = 0.0
    variants: List[str] = []
    is_free: bool = True
    status: str = "active"


class SystemHealthResponse(BaseModel):
    status: str
    app_name: str
    version: str


class RuntimeHealthResponse(BaseModel):
    status: str
    service_url: Optional[str] = None
    default_model_status: str
    models_count: int
    free_models_count: int
