"""
Usage Ledger — the single facade around runtime.execute_task.

Every model execution in AgentOS goes through run_agent_task() so the
company keeps an inspectable record of each call: which model, which
agent, how long, how many tokens, and whether it succeeded.

Rules:
- The ledger is observational. A failed recording must NEVER break the
  task that is being recorded (or the conversation behind it).
- The ledger writes through its OWN short-lived session (session_factory),
  never the caller's session, so it can't commit half-finished workflow
  state mid-flight.
- Cancelled executions (e.g. asyncio.wait_for timeouts in mission
  orchestration) are recorded as failures with error_kind=CancelledError,
  then re-raised unchanged.
"""

import asyncio
import logging
import time
import uuid
from typing import Any, Optional, Tuple

from sqlalchemy.orm import Session

from backend.app.config import settings
from backend.app.db.models import UsageEvent
from backend.app.db.session import SessionLocal
from backend.app.runtime.base import AgentResult, AgentRuntime, ExecuteAgentTask

logger = logging.getLogger("agentos.usage")

# Stable, queryable kind labels.
KIND_CHAT_CONSULT = "chat_consult"
KIND_CEO_ASK = "ceo_ask"
KIND_CEO_ASK_FAST = "ceo_ask_fast"
KIND_MISSION_PLANNER = "mission_planner"
KIND_MISSION_DELEGATE = "mission_delegate"
KIND_MISSION_SYNTHESIS = "mission_synthesis"
KIND_WORKFLOW_NODE = "workflow_node"
KIND_GREETING = "greeting_reply"

# Overridable for tests: the ledger always uses its own session.
session_factory = SessionLocal


def _tokens_in_out(tokens: Any) -> Tuple[Optional[int], Optional[int], Optional[int]]:
    """Map a runtime token dict to the ledger columns.

    OpenCode shape: {"input": N, "output": N, "reasoning": N, "cache": {"read": N, "write": N}}.
    Unknown shape -> (None, None, None) so real usage is never faked."""
    if not isinstance(tokens, dict):
        return None, None, None

    def _int(value: Any) -> Optional[int]:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    cache = tokens.get("cache")
    cache_read = cache.get("read") if isinstance(cache, dict) else None
    return (
        _int(tokens.get("input", tokens.get("input_tokens"))),
        _int(tokens.get("output", tokens.get("output_tokens"))),
        _int(cache_read if cache_read is not None else tokens.get("cache_read_input_tokens")),
    )


def _record_event(
    task: ExecuteAgentTask,
    kind: str,
    result: Optional[AgentResult],
    elapsed_ms: int,
    success: bool,
    error: Optional[BaseException],
    run_id: Optional[str] = None,
) -> None:
    """Persist one event; swallow any failure (observability must not crash)."""
    try:
        tokens_in, tokens_out, tokens_cache_read = _tokens_in_out(getattr(result, "tokens", None))
        duration_ms = int(getattr(result, "duration_ms", 0) or elapsed_ms)
        event = UsageEvent(
            id=str(uuid.uuid4()),
            run_id=run_id,
            kind=kind,
            agent_id=task.agent_id or (settings.WORK_AGENT_ID if task.provider_id == "opencode" else "unassigned"),
            model_id=(getattr(result, "model_id", None) or task.model_id),
            provider_id=task.provider_id or "opencode",
            variant_id=getattr(result, "variant_id", None),
            session_id=getattr(result, "session_id", None),
            duration_ms=duration_ms,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            tokens_cache_read=tokens_cache_read,
            success=success,
            error_kind=type(error).__name__ if error else None,
            # CLI and hook errors can contain credentials or private prompts.
            error_detail="Runtime request failed; see error_kind." if error else None,
        )
        session: Session = session_factory()
        try:
            session.add(event)
            session.commit()
        finally:
            session.close()
    except Exception as exc:  # pragma: no cover - defensive by design
        logger.warning("Usage ledger write failed (ignored): %s", type(exc).__name__)


async def run_agent_task(
    runtime: AgentRuntime,
    task: ExecuteAgentTask,
    kind: str,
    run_id: Optional[str] = None,
) -> AgentResult:
    """Execute one model task and record its usage. Do NOT call
    runtime.execute_task directly from feature code — call this."""
    t0 = time.monotonic()
    try:
        result = await runtime.execute_task(task)
    except (asyncio.CancelledError, Exception) as exc:  # noqa: B014 - CancelledError is BaseException in 3.8+
        elapsed = int((time.monotonic() - t0) * 1000)
        await asyncio.to_thread(_record_event, task, kind, None, elapsed, False, exc, run_id)
        raise
    await asyncio.to_thread(_record_event, task, kind, result, int((time.monotonic() - t0) * 1000), True, None, run_id)
    return result
