"""AgentRuntime contract (stdlib dataclasses instead of pydantic models)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Protocol


@dataclass
class ModelVariant:
    id: str
    description: Optional[str] = None


@dataclass
class ModelCost:
    input: float = 0.0
    output: float = 0.0


@dataclass
class ModelCatalogEntry:
    id: str
    name: str
    provider_id: str
    cost: ModelCost
    variants: List[str] = field(default_factory=list)
    is_free: bool = True
    status: str = "active"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class RuntimeHealth:
    status: str  # "ok", "degraded", "offline"
    default_model_status: str
    models_count: int
    free_models_count: int
    service_url: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "service_url": self.service_url,
            "default_model_status": self.default_model_status,
            "models_count": self.models_count,
            "free_models_count": self.free_models_count,
        }


@dataclass
class ExecuteAgentTask:
    prompt: str
    model_id: str
    provider_id: str = "opencode"
    variant: Optional[str] = None
    task_depth: str = "Standard"
    session_title: str = "agentos-task"
    timeout: int = 90
    # Locked OpenCode agent to run this task. None -> the work agent.
    agent_id: Optional[str] = None
    mcp_servers: Dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentResult:
    text: str
    model_id: str
    reasoning: Optional[str] = None
    variant_id: Optional[str] = None
    cost: Optional[float] = None
    tokens: Dict[str, Any] = field(default_factory=dict)
    duration_ms: int = 0
    session_id: Optional[str] = None


class AgentRuntime(Protocol):
    """Standard interface isolating AgentOS core from concrete AI providers."""

    async def check_health(self) -> RuntimeHealth: ...

    async def list_available_models(self, free_only: bool = True) -> List[ModelCatalogEntry]: ...

    async def execute_task(self, task: ExecuteAgentTask) -> AgentResult: ...

    async def abort_task(self, session_id: str) -> None: ...
