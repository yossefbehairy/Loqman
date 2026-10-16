"""
AgentRuntime Base Protocol and Schemas
Provides the contract for AI execution engines (RealOpenCodeRuntime and MockRuntime).
"""

from typing import Protocol, List, Optional, Dict, Any
from pydantic import BaseModel, Field


class ModelVariant(BaseModel):
    id: str
    description: Optional[str] = None


class ModelCost(BaseModel):
    input: float = 0.0
    output: float = 0.0


class ModelCatalogEntry(BaseModel):
    id: str
    name: str
    provider_id: str
    cost: ModelCost
    variants: List[str] = Field(default_factory=list)
    is_free: bool = True
    status: str = "active"


class RuntimeHealth(BaseModel):
    status: str  # "ok", "degraded", "offline"
    service_url: Optional[str] = None
    default_model_status: str
    models_count: int
    free_models_count: int


class ExecuteAgentTask(BaseModel):
    prompt: str
    model_id: str
    provider_id: str = "opencode"
    variant: Optional[str] = None
    task_depth: str = "Standard"
    session_title: str = "agentos-task"
    timeout: int = 90


class AgentResult(BaseModel):
    text: str
    reasoning: Optional[str] = None
    model_id: str
    variant_id: Optional[str] = None
    cost: float = 0.0
    tokens: Dict[str, Any] = Field(default_factory=dict)
    duration_ms: int = 0
    session_id: Optional[str] = None


class AgentRuntime(Protocol):
    """
    Standard interface isolating AgentOS core from concrete AI providers.
    """
    async def check_health(self) -> RuntimeHealth:
        ...

    async def list_available_models(self, free_only: bool = True) -> List[ModelCatalogEntry]:
        ...

    async def execute_task(self, task: ExecuteAgentTask) -> AgentResult:
        ...

    async def abort_task(self, session_id: str) -> None:
        ...
