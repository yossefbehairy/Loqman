"""
Database Models for AgentOS (SQLAlchemy 2.0)
Implements the 5 core contract tables with UUID primary keys and strict cascades.
"""

import uuid
from datetime import datetime, timezone
from sqlalchemy import (
    Boolean,
    Column,
    String,
    Text,
    Integer,
    DateTime,
    ForeignKey,
    Index,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import relationship
from backend.app.config import settings
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
    # e.g. "update_contract" | "new_profile" | "new_team".
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


class UsageEvent(Base):
    """One observation row per model execution.

    Written ONLY by backend.app.runtime.ledger.run_agent_task (the single
    facade around execute_task) so the company can audit every AI call:
    which model, which agent, how long, how many tokens, success or failure.
    Purely observational — a failed write must never break the task itself.
    """

    __tablename__ = "usage_events"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    run_id = Column(String(36), nullable=True, index=True)
    created_at = Column(DateTime, default=get_utc_now, nullable=False, index=True)
    # Stable kind label: chat_consult | ceo_ask | mission_planner |
    # mission_delegate | mission_synthesis | workflow_node | greeting_reply.
    kind = Column(String(64), nullable=False, index=True)
    agent_id = Column(String(128), nullable=False)
    model_id = Column(String(128), nullable=False, index=True)
    provider_id = Column(String(128), nullable=False)
    variant_id = Column(String(64), nullable=True)
    session_id = Column(String(128), nullable=True)
    duration_ms = Column(Integer, nullable=False, default=0)
    tokens_in = Column(Integer, nullable=True)
    tokens_out = Column(Integer, nullable=True)
    # OpenCode reports "input" as NON-cached tokens only; the big share sits in
    # cache reads. Recorded separately so cost analysis is honest.
    tokens_cache_read = Column(Integer, nullable=True)
    success = Column(Boolean, nullable=False, default=True)
    error_kind = Column(String(64), nullable=True)
    error_detail = Column(Text, nullable=True)


class CeoMessage(Base):
    """One turn of the CEO's (Loqman's) ongoing conversation.

    The ask endpoint records every user question and every reply, so the
    CEO conversation continues across asks AND app restarts — the backend
    owns the memory, not the renderer. Written only by routes/ceo.py.
    """

    __tablename__ = "ceo_messages"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    # Monotonic per-write counter — reliable ordering even when created_at ties.
    seq = Column(Integer, nullable=False, default=0, index=True)
    role = Column(String(16), nullable=False)  # "user" | "assistant"
    content = Column(Text, nullable=False)
    fast = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, default=get_utc_now, nullable=False, index=True)


class Conversation(Base):
    __tablename__ = "conversations"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    workspace_id = Column(String(36), ForeignKey("workspaces.id", ondelete="SET NULL"), nullable=True, index=True)
    title = Column(String(255), nullable=False)
    created_at = Column(DateTime, default=get_utc_now, nullable=False)
    updated_at = Column(DateTime, default=get_utc_now, onupdate=get_utc_now, nullable=False)
    messages = relationship("ConversationMessage", cascade="all, delete-orphan", back_populates="conversation")


class ConversationMessage(Base):
    __tablename__ = "conversation_messages"
    __table_args__ = (UniqueConstraint("conversation_id", "client_message_id"),)

    seq = Column(Integer, primary_key=True, autoincrement=True)
    conversation_id = Column(String(36), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=True)
    client_message_id = Column(String(128), nullable=True)
    role = Column(String(16), nullable=False)
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, default=get_utc_now, nullable=False)
    conversation = relationship("Conversation", back_populates="messages")


class ConversationRun(Base):
    """One engine turn (user prompt -> assistant reply) for a conversation.

    status: queued | running | succeeded | failed | cancelled | interrupted.
    A partial unique index guarantees at most ONE active (queued/running) run
    per conversation, enforced by the database itself.
    """

    __tablename__ = "conversation_runs"
    __table_args__ = (
        Index("uq_runs_request_key", "conversation_id", "request_key", unique=True),
        Index("uq_conversation_runs_one_active", "conversation_id", unique=True,
              sqlite_where=text("status IN ('queued', 'running')")),
    )

    id = Column(String(36), primary_key=True, default=generate_uuid)
    # NULL only for kind == "mission_task" runs (they belong to a mission, not a chat).
    conversation_id = Column(String(36), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=True, index=True)
    # "conversation" (chat turn) | "mission_task" (one mission worker attempt)
    kind = Column(String(32), nullable=False, default="conversation", server_default="conversation")
    # Mission worker attempts: plain ids (no FK: runs/events outlive nothing; audit trail only).
    mission_id = Column(String(36), nullable=True, index=True)
    mission_task_id = Column(String(36), nullable=True, index=True)
    attempt = Column(Integer, nullable=True)
    # Bounded raw engine text of a succeeded mission_task run (chat runs use messages).
    result_text = Column(Text, nullable=True)
    engine = Column(String(32), nullable=False)
    model_id = Column(String(128), nullable=False)
    variant = Column(String(64), nullable=True)
    request_key = Column(String(128), nullable=True)
    # JSON snapshot of explicitly selected skills (refs, exact text, sha256); NULL = none.
    skills_json = Column(Text, nullable=True)
    # JSON snapshot of explicit workspace input files (path, exact text, sha256); NULL = none.
    input_files_json = Column(Text, nullable=True)
    timeout = Column(Integer, nullable=False, default=90)
    status = Column(String(16), nullable=False, default="queued", index=True)
    user_message_seq = Column(Integer, nullable=True)
    assistant_message_seq = Column(Integer, nullable=True)
    error = Column(Text, nullable=True)
    created_at = Column(DateTime, default=get_utc_now, nullable=False)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)


class ConversationRunEvent(Base):
    """Durable lifecycle event of a ConversationRun (replayable via SSE).

    Written in the SAME transaction as the run status transition it describes.
    `id` is a never-reused, monotonically increasing integer (AUTOINCREMENT);
    SQLite serialises writers, so id order equals commit order. It doubles as
    the SSE event id / Last-Event-ID. type == the new run status.
    """

    __tablename__ = "conversation_run_events"
    __table_args__ = (
        Index("ix_conversation_run_events_run_id_id", "run_id", "id"),
        {"sqlite_autoincrement": True},
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(String(36), ForeignKey("conversation_runs.id", ondelete="CASCADE"), nullable=False)
    type = Column(String(16), nullable=False)
    data_json = Column(Text, nullable=False, default="{}")
    created_at = Column(DateTime, default=get_utc_now, nullable=False)


class Approval(Base):
    """Human approval request bound to ONE ConversationRun.

    Created only by backend services (core.approvals.ApprovalManager), never
    from user input. status: pending | approved | denied | expired. The only
    transitions are pending -> approved|denied|expired, each a conditional
    UPDATE, so a decision lands at most once and never after the run ended.
    """

    __tablename__ = "approvals"
    __table_args__ = (
        Index("ix_approvals_run_id_status", "run_id", "status"),
    )

    id = Column(String(36), primary_key=True, default=generate_uuid)
    run_id = Column(String(36), ForeignKey("conversation_runs.id", ondelete="CASCADE"), nullable=False)
    action_summary = Column(Text, nullable=False)
    scope = Column(String(255), nullable=False, default="")
    status = Column(String(16), nullable=False, default="pending")
    expires_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, default=get_utc_now, nullable=False)
    decided_at = Column(DateTime, nullable=True)
    decision_note = Column(Text, nullable=True)
    # Why it expired: run_cancelled | run_failed | run_succeeded | run_interrupted | timeout.
    expired_reason = Column(String(32), nullable=True)


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
    # Preferred engine for legacy mission worker tasks. NULL = server default
    # runtime (unchanged behaviour). Nullable column: requires a parent migration.
    engine = Column(String(32), nullable=True)
    created_at = Column(DateTime, default=get_utc_now, nullable=False)
    updated_at = Column(DateTime, default=get_utc_now, onupdate=get_utc_now, nullable=False)


class OpenCodeConversation(Base):
    """Maps a stable AgentOS conversation to its persistent OpenCode session."""

    __tablename__ = "opencode_conversations"

    conversation_id = Column(String(128), primary_key=True)
    session_id = Column(String(128), nullable=False, unique=True, index=True)
    model_id = Column(String(128), nullable=False)
    provider_id = Column(String(128), nullable=False)
    agent_id = Column(String(128), nullable=False, default=settings.CHAT_AGENT_ID)
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
    request_fingerprint = Column(String(64), nullable=True)
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


class Workspace(Base):
    """User-selected local directory that bounds file access.

    `path` is the canonical (realpath) absolute directory captured at creation.
    Registering a workspace grants nothing beyond READ access to files inside it.
    """

    __tablename__ = "workspaces"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    name = Column(String(255), nullable=False)
    path = Column(Text, nullable=False, unique=True)
    created_at = Column(DateTime, default=get_utc_now, nullable=False)

    artifacts = relationship("Artifact", cascade="all, delete-orphan", back_populates="workspace")


class Artifact(Base):
    """Registered reference to an existing file inside a workspace (read-only).

    Stores only the workspace-relative path plus size/mime observed at
    registration; content is never copied and is re-validated on every access.
    """

    __tablename__ = "artifacts"
    __table_args__ = (UniqueConstraint("workspace_id", "relative_path"),)

    id = Column(String(36), primary_key=True, default=generate_uuid)
    workspace_id = Column(String(36), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True)
    # Informational link to a run; deliberately not a FK (runs may be pruned independently).
    run_id = Column(String(36), nullable=True, index=True)
    relative_path = Column(Text, nullable=False)  # POSIX separators
    mime = Column(String(128), nullable=False, default="application/octet-stream")
    size = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=get_utc_now, nullable=False)

    workspace = relationship("Workspace", back_populates="artifacts")


class DeliverableDraft(Base):
    """Immutable snapshot of a succeeded run's assistant reply awaiting ONE review.

    Separate from `Approval` (which expires when its run ends). `content`,
    `content_sha256`, `size_bytes` and `provenance_json` are written once at
    creation and never updated; only the review columns change, and only via
    a conditional UPDATE from 'pending_review'. Nothing here is ever written
    to a workspace on disk.
    status: pending_review | approved | rejected.
    """

    __tablename__ = "deliverable_drafts"
    __table_args__ = (
        UniqueConstraint("run_id", "content_sha256", "filename", name="uq_deliverable_run_content_name"),
        Index("ix_deliverable_drafts_run_id_status", "run_id", "status"),
    )

    id = Column(String(36), primary_key=True, default=generate_uuid)
    run_id = Column(String(36), ForeignKey("conversation_runs.id", ondelete="CASCADE"), nullable=False)
    filename = Column(String(128), nullable=False)
    mime = Column(String(64), nullable=False, default="text/plain")
    content = Column(Text, nullable=False)
    content_sha256 = Column(String(64), nullable=False)
    size_bytes = Column(Integer, nullable=False)
    provenance_json = Column(Text, nullable=False, default="{}")
    status = Column(String(16), nullable=False, default="pending_review")
    created_at = Column(DateTime, default=get_utc_now, nullable=False)
    decided_at = Column(DateTime, nullable=True)
    decision_note = Column(Text, nullable=True)
