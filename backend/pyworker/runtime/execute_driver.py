"""Whole-result compatibility driver; deliberately never fabricates text deltas."""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
import time

from backend.pyworker.db.core import new_id, ts
from .base import AgentRuntime, ExecuteAgentTask
from .driver import (
    ApprovalAnswer, CleanupIssue, DriverCapabilities, DriverEvent, DriverFailure,
    DriverTurnError, Emit, StartTurn, TurnRef,
)


@dataclass
class _Turn:
    turn: StartTurn
    emit: Emit
    request: ExecuteAgentTask
    task: asyncio.Task | None = None
    stop: asyncio.Task | None = None
    abort: asyncio.Task | None = None
    cleanup_deadline: float | None = None
    cancelled: bool = False
    abandoned: bool = False
    terminal: bool = False
    started: bool = False
    seq: int = 0
    error: BaseException | None = None


class ExecuteTaskDriver:
    capabilities = DriverCapabilities(False, False, False, False, "none", "relaunch", False)

    def __init__(self, runtime: AgentRuntime, engine: str = "legacy", runtime_key: str = "default",
                 *, legacy_task: ExecuteAgentTask | None = None, cancel_grace_s: float = 15.0):
        self.cancel_grace_s = cancel_grace_s  # bound on waiting for an engine that ignores cancellation
        self.runtime, self.engine, self.runtime_key = runtime, engine, runtime_key
        self._legacy_task = legacy_task
        self._turns: dict[str, _Turn] = {}
        self._issues: list[CleanupIssue] = []
        self._cleanup_expired = asyncio.Event()

    def expire_cleanup(self):
        """Application shutdown reached its deadline; release pending joins."""
        self._cleanup_expired.set()

    async def start_turn(self, turn: StartTurn, emit: Emit) -> TurnRef:
        if not all((turn.turn_id, turn.run_id, turn.scope_id, turn.model_id, turn.cwd)) or turn.timeout_s <= 0 or turn.max_output_chars < 1:
            raise ValueError("Invalid turn arguments")
        if turn.scope_kind not in {"conversation", "ceo", "mission_attempt", "workflow"}:
            raise ValueError("Invalid scope")
        if turn.session is not None:
            raise DriverTurnError(DriverFailure("unsupported_capability", "Legacy runtime cannot resume sessions.",
                                               False, "resume", "not-submitted"))
        if turn.turn_id in self._turns or any(
            not s.terminal and (s.turn.scope_kind, s.turn.scope_id) == (turn.scope_kind, turn.scope_id)
            for s in self._turns.values()
        ):
            raise DriverTurnError(DriverFailure("busy", "Scope already has an active turn.", False, "launch", "not-submitted"))
        # Keep a bounded terminal tombstone window, never evict active ownership.
        if len(self._turns) >= 256:
            for key, state in list(self._turns.items()):
                if (state.terminal and (state.task is None or state.task.done())
                        and (state.abort is None or state.abort.done())
                        and (state.stop is None or state.stop.done())):
                    del self._turns[key]
                    break
            else:
                raise DriverTurnError(DriverFailure("busy", "Driver admission limit reached.", False, "launch", "not-submitted"))
        request = self._legacy_task or ExecuteAgentTask(
            prompt=turn.prompt, model_id=turn.model_id, provider_id=turn.provider_id or "unknown",
            variant=turn.variant, session_title=turn.turn_id, timeout=turn.timeout_s,
            agent_id=turn.agent_id, mcp_servers=turn.tool_policy.mcp_servers,
        )
        if any(not s.terminal and s.request.session_title == request.session_title for s in self._turns.values()):
            raise DriverTurnError(DriverFailure("busy", "Legacy cancellation key is active.", False, "launch", "not-submitted"))
        state = _Turn(turn, emit, request)
        self._turns[turn.turn_id] = state  # cancellation registration precedes scheduling
        state.task = asyncio.create_task(self._run(state), name=f"driver-{turn.turn_id}")
        return TurnRef(turn.turn_id, turn.run_id)

    async def _send(self, s, type_, data, *, item_id=None, session_id=None):
        if s.terminal or (s.cancelled and type_ not in {"turn.started", "turn.completed"}):
            return
        s.seq += 1
        if type_ == "turn.completed":
            s.terminal = True  # the sole settlement gate, before awaiting the sink
        await s.emit(DriverEvent(1, new_id(), s.turn.turn_id, s.turn.run_id, s.seq,
                                 self.engine, self.runtime_key, ts(), type_, data,
                                 native_session_id=session_id, item_id=item_id))

    async def _started(self, s):
        if not s.started:
            s.started = True
            await self._send(s, "turn.started", {"model_id": s.turn.model_id,
                "policy": {"mode": s.turn.tool_policy.mode, "approval_mode": s.turn.tool_policy.approval_mode}})

    async def _run(self, s):
        t0 = time.monotonic()
        try:
            await self._started(s)
            if s.cancelled:
                return
            result = await self.runtime.execute_task(s.request)
            if s.cancelled:
                return
            # Check before passing a potentially enormous snapshot to persistence.
            if len(result.text or "") + len(result.reasoning or "") > s.turn.max_output_chars:
                raise DriverTurnError(DriverFailure("output_limit", "Engine output exceeded the allowed character limit.",
                                                   False, "running", "output-seen"))
            item_id = f"{s.turn.turn_id}:assistant"
            if result.reasoning is not None:
                await self._send(s, "item.completed", {"item_type": "reasoning", "text": result.reasoning},
                                 item_id=f"{s.turn.turn_id}:reasoning")
            if s.cancelled:
                return
            await self._send(s, "item.completed", {"item_type": "assistant", "phase": "final", "text": result.text},
                             item_id=item_id, session_id=result.session_id)
            tokens = result.tokens if isinstance(result.tokens, dict) else {}
            cache = tokens.get("cache")
            if not isinstance(cache, dict):
                cache = {}
            usage = {"tokens": result.tokens, "cost": result.cost,
                "input": tokens.get("input", tokens.get("input_tokens")),
                "output": tokens.get("output", tokens.get("output_tokens")),
                "cache_read": cache.get("read", tokens.get("cache_read_input_tokens")),
                "cache_write": cache.get("write", tokens.get("cache_creation_input_tokens")),
                "reasoning": tokens.get("reasoning"), "context_size": None}
            if result.tokens or result.cost is not None:
                await self._send(s, "usage", {"scope": "turn", "measurement_id": s.turn.turn_id,
                    "final": True, **usage}, session_id=result.session_id)
            if not s.cancelled:
                await self._send(s, "turn.completed", {"status": "succeeded", "stop_reason": "end_turn",
                    "duration_ms": result.duration_ms, "final_item_ids": [item_id],
                    "model_id": result.model_id, "variant": result.variant_id, "usage": usage,
                    "session_id": result.session_id}, session_id=result.session_id)
        except asyncio.CancelledError:
            s.cancelled = True
        except Exception as exc:
            s.error = exc
            code = getattr(exc, "code", "runtime_error")
            if code not in {"auth", "quota", "rate_limit", "rate_limited", "model_unavailable", "timeout", "cli_missing",
                            "cancelled", "protocol_error", "upstream_unavailable", "request_refused",
                            "unsupported_capability", "output_limit", "runtime_error", "offline"}:
                code = "runtime_error"
            failure = exc.failure if isinstance(exc, DriverTurnError) else DriverFailure(
                {"rate_limited": "rate_limit"}.get(code, code),
                f"Request failed ({code}).", bool(getattr(exc, "retryable", False)),
                "running", "unknown")
            if isinstance(exc, FileNotFoundError):
                failure = DriverFailure("cli_missing", "Engine executable not found.", False, "launch", "not-submitted")
            if not s.cancelled:
                await self._send(s, "runtime.error", asdict(failure))
                await self._send(s, "turn.completed", {"status": "cancelled" if failure.category == "cancelled" else "failed", "stop_reason": failure.category,
                    "duration_ms": int((time.monotonic() - t0) * 1000), "final_item_ids": [], "failure": asdict(failure)})
        finally:
            if s.cancelled and not s.terminal:
                await self._send(s, "turn.completed", {"status": "cancelled", "stop_reason": "cancelled",
                    "duration_ms": int((time.monotonic() - t0) * 1000), "final_item_ids": []})

    async def _join(self, s):
        """Retain both execution and abort ownership through the kill budget.

        Legacy Windows kills allow 10s for taskkill and 5s for confirmed exit.
        Repeated close/interrupt calls share one budget; shutdown can shorten it.
        """
        pending = {t for t in (s.task, s.abort) if t is not None and not t.done()}
        if not pending:
            return
        loop = asyncio.get_running_loop()
        if s.cleanup_deadline is None:
            s.cleanup_deadline = loop.time() + self.cancel_grace_s
        expiry = asyncio.create_task(self._cleanup_expired.wait())
        try:
            while pending and not self._cleanup_expired.is_set():
                finished, _ = await asyncio.wait(pending | {expiry},
                    timeout=max(0, s.cleanup_deadline - loop.time()), return_when=asyncio.FIRST_COMPLETED)
                pending -= finished
                if not finished or expiry in finished:
                    break
        finally:
            expiry.cancel()
            await asyncio.gather(expiry, return_exceptions=True)
        if pending and not s.abandoned:
            s.abandoned = True  # late results are dropped: s.cancelled is set and s.terminal gates _send
            self._issues.append(CleanupIssue(s.turn.turn_id, None, "Engine ignored cancellation; task abandoned."))
            self._issues[:] = self._issues[-256:]

    async def _stop(self, s):
        async def abort():
            try:
                await self.runtime.abort_task(s.request.session_title)
            except Exception:
                self._issues.append(CleanupIssue(s.turn.turn_id, None, "Legacy abort failed."))
                self._issues[:] = self._issues[-256:]

        s.abort = asyncio.create_task(abort())
        try:
            # Give protocol abort a brief head start, but never cancel its kill.
            expiry = asyncio.create_task(self._cleanup_expired.wait())
            try:
                await asyncio.wait({s.abort, expiry}, timeout=min(1.0, self.cancel_grace_s),
                                   return_when=asyncio.FIRST_COMPLETED)
            finally:
                expiry.cancel()
                await asyncio.gather(expiry, return_exceptions=True)
        finally:
            if s.task is not None and not s.task.done():
                s.task.cancel()
            # A task cancelled before its first instruction has no finally block.
            if not s.terminal:
                await self._started(s)
                await self._send(s, "turn.completed", {"status": "cancelled", "stop_reason": "cancelled",
                    "duration_ms": 0, "final_item_ids": []})
            await self._join(s)

    async def interrupt(self, turn_id):
        s = self._turns.get(turn_id)
        if s is None:
            return "unknown"
        if s.stop is not None:
            await asyncio.shield(s.stop)
            return "requested"
        if s.terminal:
            return "already-terminal"
        s.cancelled = True
        s.stop = asyncio.create_task(self._stop(s))
        await asyncio.shield(s.stop)
        return "requested"

    async def respond(self, turn_id: str, request_id: str, answer: ApprovalAnswer):
        return "unavailable"

    async def close(self, scope_kind, scope_id):
        for s in list(self._turns.values()):
            if (s.turn.scope_kind, s.turn.scope_id) == (scope_kind, scope_id):
                await self.interrupt(s.turn.turn_id)
                await self._join(s)
        return tuple(self._issues)

    async def stop_all(self):
        for s in list(self._turns.values()):
            await self.close(s.turn.scope_kind, s.turn.scope_id)
        return tuple(self._issues)

    async def check_health(self):
        return await self.runtime.check_health()

    async def list_available_models(self, free_only=True, *, refresh=False):
        return await self.runtime.list_available_models(free_only)
