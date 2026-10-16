"""
Workflow Pydantic Schemas
"""

from datetime import datetime
from typing import Optional, Dict, Any
from pydantic import BaseModel, Field, ConfigDict


class WorkflowBase(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: Optional[str] = None
    definition_json: str = Field(default="{}")


class WorkflowCreate(WorkflowBase):
    pass


class WorkflowUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    definition_json: Optional[str] = None


class WorkflowResponse(WorkflowBase):
    id: str
    version: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
