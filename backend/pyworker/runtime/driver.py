"""Version-one turn boundary. Native protocol drivers implement this contract."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Awaitable, Callable, Literal, Protocol

from .base import ModelCatalogEntry, RuntimeHealth
from .errors import RuntimeErrorBase

ScopeKind = Literal["conversation", "ceo", "mission_attempt", "workflow"]
SessionProtocol = Literal["claude-stream-json", "codex-app-server", "acp-load", "acp-resume", "opencode-legacy"]
FailureCategory = Literal[
    "auth", "quota", "rate_limit", "model_unavailable", "timeout", "cli_missing",
    "cancelled", "protocol_error", "upstream_unavailable", "request_refused",
    "unsupported_capability", "output_limit", "runtime_error", "offline", "busy",
]
EventType = Literal[
    "turn.started", "session.started", "content.delta", "item.started", "item.updated",
    "item.completed", "request.opened", "request.resolved", "usage", "runtime.error",
    "runtime.notice", "turn.completed",
]
EVENT_TYPES = frozenset(EventType.__args__)


@dataclass(frozen=True, slots=True)
class SessionRef:
    native_session_id: str
    protocol: SessionProtocol
    namespace: str
    generation: int


@dataclass(frozen=True, slots=True)
class ToolPolicy:
    mode: Literal["none", "scoped", "native"]
    approval_mode: Literal["deny", "ask"]
    allowed_tools: tuple[str, ...] = ()
    mcp_servers: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class StartTurn:
    turn_id: str
    run_id: str
    scope_kind: ScopeKind
    scope_id: str
    prompt: str
    model_id: str
    cwd: str
    tool_policy: ToolPolicy
    conversation_id: str | None = None
    provider_id: str | None = None
    agent_id: str | None = None
    variant: str | None = None
    effort: str | None = None
    system: str | None = None
    session: SessionRef | None = None
    session_reset: bool = False
    timeout_s: float = 90.0
    max_output_chars: int = 20000


@dataclass(frozen=True, slots=True)
class TurnRef:
    turn_id: str
    run_id: str


@dataclass(frozen=True, slots=True)
class ApprovalAnswer:
    behavior: Literal["allow", "deny", "answer"]
    message: str | None = None
    answers: dict[str, tuple[str, ...]] = field(default_factory=dict)
    remember_for_session: bool = False


@dataclass(frozen=True, slots=True)
class DriverCapabilities:
    streaming_text: bool
    tool_events: bool
    permission_requests: bool
    question_requests: bool
    native_resume: Literal["none", "load", "resume", "cli-resume"]
    model_switch: Literal["in-session", "relaunch", "unsupported"]
    scoped_mcp: bool
    effort_levels: tuple[str, ...] = ()
    model_variants: bool = False


@dataclass(frozen=True, slots=True)
class DriverFailure:
    category: FailureCategory
    message: str
    retryable: bool
    phase: Literal["launch", "handshake", "resume", "prompt", "running", "cleanup"]
    acceptance: Literal["not-submitted", "submitted", "output-seen", "unknown"]
    native_code: str | int | None = None
    retry_after_s: float | None = None


@dataclass(frozen=True, slots=True)
class DriverEvent:
    version: int
    event_id: str
    turn_id: str
    run_id: str
    seq: int
    engine: str
    runtime_key: str
    created_at: str
    type: EventType
    data: dict[str, object]
    native_session_id: str | None = None
    native_turn_id: str | None = None
    item_id: str | None = None
    request_id: str | None = None


@dataclass(frozen=True, slots=True)
class CleanupIssue:
    process_generation: str
    pid: int | None
    message: str


class DriverTurnError(RuntimeErrorBase, RuntimeError):
    """Safe public failure plus structured submission certainty; never triggers replay."""

    def __init__(self, failure: DriverFailure):
        super().__init__(failure.message)
        self.failure = failure
        self.code = "rate_limited" if failure.category == "rate_limit" else failure.category
        self.retryable = failure.retryable


Emit = Callable[[DriverEvent], Awaitable[None]]


class TurnDriver(Protocol):
    engine: str
    runtime_key: str
    capabilities: DriverCapabilities

    async def start_turn(self, turn: StartTurn, emit: Emit) -> TurnRef: ...
    async def interrupt(self, turn_id: str) -> Literal["requested", "already-terminal", "unknown"]: ...
    async def respond(self, turn_id: str, request_id: str, answer: ApprovalAnswer) -> Literal[
        "allowed-once", "allowed-session", "rejected", "answered", "unavailable"
    ]: ...
    async def close(self, scope_kind: str, scope_id: str) -> tuple[CleanupIssue, ...]: ...
    async def stop_all(self) -> tuple[CleanupIssue, ...]: ...
    async def check_health(self) -> RuntimeHealth: ...
    async def list_available_models(self, free_only: bool = True, *, refresh: bool = False) -> list[ModelCatalogEntry]: ...
