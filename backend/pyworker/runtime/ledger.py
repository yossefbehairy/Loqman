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
import os
import time
from typing import Any, Optional, Tuple

from backend.pyworker.appsettings import settings
from backend.pyworker.db.core import Database, new_id, ts
from backend.pyworker.runtime.base import AgentResult, AgentRuntime, ExecuteAgentTask
from backend.pyworker.runtime.collector import collect_turn
from backend.pyworker.runtime.driver import StartTurn, ToolPolicy
from backend.pyworker.runtime.execute_driver import ExecuteTaskDriver
from backend.pyworker.runtime.providers import ENGINE_LEDGER_PROVIDERS

logger = logging.getLogger("agentos.usage")

# Stable, queryable kind labels.
KIND_CEO_ASK = "ceo_ask"
KIND_CEO_ASK_FAST = "ceo_ask_fast"
KIND_MISSION_PLANNER = "mission_planner"
KIND_MISSION_DELEGATE = "mission_delegate"
KIND_MISSION_SYNTHESIS = "mission_synthesis"
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
            "provider_id": task.provider_id or "unknown",
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
    *, driver=None, emit=None, engine=None, scope_kind=None, scope_id=None,
    max_output_chars=8 * 1024 * 1024,
) -> AgentResult:
    """Execute one model task and record its usage."""
    t0 = time.monotonic()
    logical_id = run_id or new_id()
    engine = engine or next((e for e, p in ENGINE_LEDGER_PROVIDERS.items() if p == task.provider_id), "legacy")
    driver = driver or ExecuteTaskDriver(runtime, engine, legacy_task=task)
    turn = StartTurn(
        logical_id, logical_id, scope_kind or ("ceo" if kind.startswith("ceo_") else "mission_attempt"),
        scope_id or logical_id, task.prompt, task.model_id, os.getcwd(),
        ToolPolicy("scoped" if task.mcp_servers else "none", "deny", mcp_servers=task.mcp_servers),
        conversation_id=scope_id if scope_kind == "conversation" else None,
        provider_id=task.provider_id, agent_id=task.agent_id, variant=task.variant,
        timeout_s=task.timeout, max_output_chars=max_output_chars,
    )
    try:
        result = await collect_turn(driver, turn, emit)
    except (asyncio.CancelledError, Exception) as exc:  # noqa: B014
        state = driver._turns.get(logical_id) if isinstance(driver, ExecuteTaskDriver) else None
        recorded_error = state.error if state is not None and state.error is not None else exc
        _record_event(task, kind, None, int((time.monotonic() - t0) * 1000), False, recorded_error, run_id)
        raise
    _record_event(task, kind, result, int((time.monotonic() - t0) * 1000), True, None, run_id)
    return result
