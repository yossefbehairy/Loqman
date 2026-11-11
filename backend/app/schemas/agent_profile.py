"""
AgentProfile Pydantic Schemas
"""

from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field, ConfigDict, field_validator


PROFILE_ENGINES = ("opencode", "claude-code", "codex", "antigravity")


def validate_profile_engine(value: Optional[str]) -> Optional[str]:
    """None/"" = server default. "mock" is accepted only in explicit mock mode."""
    if value is None or value == "":
        return None
    from backend.app.config import settings
    allowed = PROFILE_ENGINES + (("mock",) if settings.RUNTIME_MODE == "mock" else ())
    if value not in allowed:
        raise ValueError(f"engine must be one of {list(allowed)} or null")
    return value


class AgentProfileBase(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    role_type: str = Field(min_length=1, max_length=64)
    description: Optional[str] = None
    instructions: str = Field(min_length=1)
    input_schema_json: Optional[str] = None
    output_schema_json: Optional[str] = None
    default_model_id: str = "muse-spark-1.3-contributor-free"
    default_variant_id: Optional[str] = None
    task_depth: str = "Standard"
    settings_json: Optional[str] = None
    engine: Optional[str] = None


class AgentProfileCreate(AgentProfileBase):
    id: Optional[str] = None

    @field_validator("engine")
    @classmethod
    def _check_engine(cls, v: Optional[str]) -> Optional[str]:
        return validate_profile_engine(v)


class AgentProfileResponse(AgentProfileBase):
    id: str
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
