"""
AgentProfile Pydantic Schemas
"""

from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field, ConfigDict


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


class AgentProfileCreate(AgentProfileBase):
    id: Optional[str] = None


class AgentProfileResponse(AgentProfileBase):
    id: str
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
