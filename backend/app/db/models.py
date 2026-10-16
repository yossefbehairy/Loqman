"""
Database Models for AgentOS (SQLAlchemy 2.0)
Implements the 5 core contract tables with UUID primary keys and strict cascades.
"""

import uuid
from datetime import datetime, timezone
from sqlalchemy import (
    Column,
    String,
    Text,
    Integer,
    DateTime,
    ForeignKey,
    Index,
)
from sqlalchemy.orm import relationship
from backend.app.db.session import Base


def generate_uuid() -> str:
    return str(uuid.uuid4())


def get_utc_now() -> datetime:
    return datetime.now(timezone.utc)


class PendingAction(Base):
    """CEO-proposed write action awaiting explicit human approval.

    Write tools never execute directly: they insert a row here with
    status='pending', and only approve/deny endpoints transition it.
    """

    __tablename__ = "pending_actions"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    # e.g. "update_contract" | "new_profile".
    kind = Column(String(64), nullable=False, index=True)
    # Proposed data + expected_updated_at of the target row (optimistic concurrency).
    payload_json = Column(Text, nullable=False, default="{}")
    summary = Column(Text, nullable=False, default="")
    # pending | approved | denied | failed.
    status = Column(String(32), nullable=False, default="pending", index=True)
    created_by = Column(String(128), nullable=True)
    created_at = Column(DateTime, default=get_utc_now, nullable=False)
    decided_at = Column(DateTime, nullable=True)
    decision_note = Column(Text, nullable=True)


class Workflow(Base):
    __tablename__ = "workflows"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    name = Column(String(255), nullable=False, index=True)
    description = Column(Text, nullable=True)
    definition_json = Column(Text, nullable=False, default="{}")
    version = Column(Integer, default=1, nullable=False)
    created_at = Column(DateTime, default=get_utc_now, nullable=False)
    updated_at = Column(DateTime, default=get_utc_now, onupdate=get_utc_now, nullable=False)

    executions = relationship("WorkflowExecution", back_populates="workflow", cascade="all, delete-orphan")


class AgentProfile(Base):
    __tablename__ = "agent_profiles"

    id = Column(String(64), primary_key=True, default=generate_uuid)
    name = Column(String(255), nullable=False)
    role_type = Column(String(64), nullable=False, index=True)
    description = Column(Text, nullable=True)
    instructions = Column(Text, nullable=False)
    input_schema_json = Column(Text, nullable=True)
    output_schema_json = Column(Text, nullable=True)
    default_model_id = Column(String(128), nullable=False, default="muse-spark-1.3-contributor-free")
    default_variant_id = Column(String(64), nullable=True)
    task_depth = Column(String(32), default="Standard", nullable=False)
    settings_json = Column(Text, nullable=True)
    created_at = Column(DateTime, default=get_utc_now, nullable=False)
    updated_at = Column(DateTime, default=get_utc_now, onupdate=get_utc_now, nullable=False)


class OpenCodeConversation(Base):
    """Maps a stable AgentOS conversation to its persistent OpenCode session."""

    __tablename__ = "opencode_conversations"

    conversation_id = Column(String(128), primary_key=True)
    session_id = Column(String(128), nullable=False, unique=True, index=True)
    model_id = Column(String(128), nullable=False)
    provider_id = Column(String(128), nullable=False)
    agent_id = Column(String(128), nullable=False, default="build")
    created_at = Column(DateTime, default=get_utc_now, nullable=False)
    updated_at = Column(DateTime, default=get_utc_now, onupdate=get_utc_now, nullable=False)


class WorkflowExecution(Base):
    __tablename__ = "workflow_executions"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    workflow_id = Column(String(36), ForeignKey("workflows.id", ondelete="CASCADE"), nullable=False, index=True)
    status = Column(String(32), nullable=False, default="pending", index=True)
    input_json = Column(Text, nullable=True)
    final_output_json = Column(Text, nullable=True)
    workflow_snapshot_json = Column(Text, nullable=True)
    runtime_status = Column(String(64), nullable=True)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    error_summary = Column(Text, nullable=True)

    workflow = relationship("Workflow", back_populates="executions")
    node_executions = relationship("NodeExecution", back_populates="workflow_execution", cascade="all, delete-orphan")
    events = relationship("ExecutionEvent", back_populates="workflow_execution", cascade="all, delete-orphan")


class NodeExecution(Base):
    __tablename__ = "node_executions"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    execution_id = Column(String(36), ForeignKey("workflow_executions.id", ondelete="CASCADE"), nullable=False, index=True)
    node_id = Column(String(64), nullable=False, index=True)
    role_type = Column(String(64), nullable=True)
    model_id = Column(String(128), nullable=True)
    native_variant_id = Column(String(64), nullable=True)
    task_depth = Column(String(32), default="Standard", nullable=False)
    status = Column(String(32), nullable=False, default="pending", index=True)
    attempt_count = Column(Integer, default=0, nullable=False)
    input_json = Column(Text, nullable=True)
    output_json = Column(Text, nullable=True)
    error_message = Column(Text, nullable=True)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    duration_ms = Column(Integer, nullable=True)

    workflow_execution = relationship("WorkflowExecution", back_populates="node_executions")
    events = relationship("ExecutionEvent", back_populates="node_execution", cascade="all, delete-orphan")


class ExecutionEvent(Base):
    __tablename__ = "execution_events"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    execution_id = Column(String(36), ForeignKey("workflow_executions.id", ondelete="CASCADE"), nullable=False, index=True)
    node_execution_id = Column(String(36), ForeignKey("node_executions.id", ondelete="CASCADE"), nullable=True, index=True)
    event_type = Column(String(64), nullable=False, index=True)
    event_json = Column(Text, nullable=False, default="{}")
    created_at = Column(DateTime, default=get_utc_now, nullable=False)

    workflow_execution = relationship("WorkflowExecution", back_populates="events")
    node_execution = relationship("NodeExecution", back_populates="events")


class Mission(Base):
    """CEO-orchestrated end-to-end mission (persisted before any AI call)."""

    __tablename__ = "missions"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    title = Column(String(255), nullable=False, default="Untitled mission")
    objective = Column(Text, nullable=False)
    # queued | planning | running | succeeded | partially_succeeded | failed | cancelled
    status = Column(String(32), nullable=False, default="queued", index=True)
    model_id = Column(String(128), nullable=True)
    variant = Column(String(64), nullable=True)
    source_conversation_id = Column(String(128), nullable=True, index=True)
    idempotency_key = Column(String(128), nullable=True, unique=True, index=True)
    plan_json = Column(Text, nullable=True)
    final_summary = Column(Text, nullable=True)
    error_summary = Column(Text, nullable=True)
    created_at = Column(DateTime, default=get_utc_now, nullable=False)
    updated_at = Column(DateTime, default=get_utc_now, onupdate=get_utc_now, nullable=False)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)

    tasks = relationship("MissionTask", back_populates="mission", cascade="all, delete-orphan")


class MissionTask(Base):
    """One employee task inside a mission (dependency-ordered)."""

    __tablename__ = "mission_tasks"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    mission_id = Column(String(36), ForeignKey("missions.id", ondelete="CASCADE"), nullable=False, index=True)
    # Planner-stable task key from the CEO plan (e.g. "planning", "quality_review")
    task_key = Column(String(64), nullable=False, index=True)
    employee_role = Column(String(64), nullable=False, index=True)
    agent_profile_id = Column(String(64), nullable=True)
    title = Column(String(255), nullable=False, default="")
    instructions = Column(Text, nullable=False, default="")
    input_context = Column(Text, nullable=True)
    expected_output = Column(Text, nullable=True)
    depends_on_json = Column(Text, nullable=True, default="[]")
    # queued | running | succeeded | failed | skipped | cancelled
    status = Column(String(32), nullable=False, default="queued", index=True)
    model_id = Column(String(128), nullable=True)
    variant = Column(String(64), nullable=True)
    attempt_count = Column(Integer, default=0, nullable=False)
    output_json = Column(Text, nullable=True)
    error_message = Column(Text, nullable=True)
    session_id = Column(String(128), nullable=True)
    duration_ms = Column(Integer, nullable=True)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)

    mission = relationship("Mission", back_populates="tasks")
