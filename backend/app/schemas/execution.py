"""
Execution Pydantic Schemas
"""

from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, ConfigDict


class NodeExecutionResponse(BaseModel):
    id: str
    execution_id: str
    node_id: str
    role_type: Optional[str] = None
    model_id: Optional[str] = None
    native_variant_id: Optional[str] = None
    task_depth: str
    status: str
    attempt_count: int
    input_json: Optional[str] = None
    output_json: Optional[str] = None
    error_message: Optional[str] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    duration_ms: Optional[int] = None

    model_config = ConfigDict(from_attributes=True)


class ExecutionEventResponse(BaseModel):
    id: str
    execution_id: str
    node_execution_id: Optional[str] = None
    event_type: str
    event_json: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class WorkflowExecutionResponse(BaseModel):
    id: str
    workflow_id: str
    status: str
    input_json: Optional[str] = None
    final_output_json: Optional[str] = None
    workflow_snapshot_json: Optional[str] = None
    runtime_status: Optional[str] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    error_summary: Optional[str] = None
    node_executions: List[NodeExecutionResponse] = []
    events: List[ExecutionEventResponse] = []

    model_config = ConfigDict(from_attributes=True)


class ExecutionCreateRequest(BaseModel):
    input_json: Optional[str] = "{}"


class TeamMessageResponse(BaseModel):
    execution_id: str
    turn: int
    from_node_id: str
    from_display_name: str
    from_role: str
    to_node_ids: List[str] = []
    excerpt: str
    full_output_ref: str
    timestamp: str
    created_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=False)


class TeamMessageListResponse(BaseModel):
    execution_id: str
    count: int
    messages: List[TeamMessageResponse]
