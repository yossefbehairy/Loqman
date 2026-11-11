"""Usage ledger: the single facade around ``runtime.execute_task``.

Every model execution goes through ``run_agent_task`` so the company keeps an
inspectable record of each call. The ledger is observational: a failed
recording must never break the task being recorded. Writes happen on the
event-loop thread through the application's shared database (set once by the
bootstrap via ``bind``).
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Optional, Tuple

from backend.pyworker.appsettings import settings
from backend.pyworker.db.core import Database, new_id, ts
from backend.pyworker.runtime.base import AgentResult, AgentRuntime, ExecuteAgentTask

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

_db: Optional[Database] = None


def bind(db: Optional[Database]) -> None:
    """Point the ledger at the application database (None disables it)."""
    global _db
    _db = db


def _tokens_in_out(tokens: Any) -> Tuple[Optional[int], Optional[int], Optional[int]]:
    """Map a runtime token dict to the ledger columns (unknown shape -> Nones)."""
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
    try:
        if _db is None:
            return
        tokens_in, tokens_out, tokens_cache_read = _tokens_in_out(getattr(result, "tokens", None))
        duration_ms = int(getattr(result, "duration_ms", 0) or elapsed_ms)
        _db.insert("usage_events", {
            "id": new_id(),
            "run_id": run_id,
            "created_at": ts(),
            "kind": kind,
            "agent_id": task.agent_id or (settings.WORK_AGENT_ID if task.provider_id == "opencode" else "unassigned"),
            "model_id": getattr(result, "model_id", None) or task.model_id,
            "provider_id": task.provider_id or "opencode",
            "variant_id": getattr(result, "variant_id", None),
            "session_id": getattr(result, "session_id", None),
            "duration_ms": duration_ms,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "tokens_cache_read": tokens_cache_read,
            "success": 1 if success else 0,
            "error_kind": type(error).__name__ if error else None,
            # CLI and hook errors can contain credentials or private prompts.
            "error_detail": "Runtime request failed; see error_kind." if error else None,
        })
    except Exception as exc:  # defensive by design
        logger.warning("Usage ledger write failed (ignored): %s", type(exc).__name__)


async def run_agent_task(
    runtime: AgentRuntime,
    task: ExecuteAgentTask,
    kind: str,
    run_id: Optional[str] = None,
) -> AgentResult:
    """Execute one model task and record its usage."""
    t0 = time.monotonic()
    try:
        result = await runtime.execute_task(task)
    except (asyncio.CancelledError, Exception) as exc:  # noqa: B014
        _record_event(task, kind, None, int((time.monotonic() - t0) * 1000), False, exc, run_id)
        raise
    _record_event(task, kind, result, int((time.monotonic() - t0) * 1000), True, None, run_id)
    return result
