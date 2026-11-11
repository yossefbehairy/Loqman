"""
Bounded, persisted conversation run manager.

Each turn = one user message + one ConversationRun row (queued) committed in a
single short transaction. An asyncio task then sends the prompt through the
existing engine adapter (via the usage ledger facade) and persists the reply.

Guarantees
- One active (queued/running) run per conversation: enforced by a partial
  unique index in the DB, so concurrent requests cannot both win.
- Every status transition is a conditional UPDATE (`WHERE status IN (...)`), so
  a cancelled run can never be flipped to succeeded, and no assistant message
  is written unless the same transaction won the transition.
- Concurrency is bounded (global semaphore + cap on pending tasks).
- On process start, active runs are marked `interrupted`; nothing is re-run.

Durable events
- Every status transition writes a ConversationRunEvent row in the SAME short
  transaction as the transition (type == new status; ids are monotonic).
- `iter_events` serves replay + live tail: it subscribes BEFORE reading the DB
  (no lost gap), dedupes by event id, and drops subscribers whose bounded queue
  overflows (the stream ends; the client reconnects with Last-Event-ID and
  replays from the DB). Events are lifecycle only: no token/text streaming.

Non-goals: no resume of the engine call. The adapters are stateless, so
only the single prompt of the turn is sent (plus bounded prior history).
"""

import asyncio
import json
import logging
import threading
import weakref
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Callable, Dict, List, Optional, Set

from sqlalchemy import and_, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.config import settings
from backend.app.db.models import (
    Conversation, ConversationMessage, ConversationRun, ConversationRunEvent, Mission, MissionTask,
)
from backend.app.core import run_inputs, run_skills
from backend.app.core.approvals import expire_for_run as expire_approvals_for_run
from backend.app.runtime.base import ExecuteAgentTask
from backend.app.runtime.errors import safe_error_message
from backend.app.runtime.ledger import (
    run_agent_task, KIND_MISSION_DELEGATE, KIND_MISSION_PLANNER, KIND_MISSION_SYNTHESIS,
)

logger = logging.getLogger("agentos.conversation_runs")

ACTIVE_STATUSES = ("queued", "running")
TERMINAL_STATUSES = ("succeeded", "failed", "cancelled", "interrupted")
ENGINE_PROVIDERS = {"claude-code": "anthropic", "codex": "openai", "opencode": "opencode", "antigravity": "antigravity", "mock": "mock"}
INTERRUPTED_ERROR = "Run interrupted by server restart or shutdown; it was not re-executed."
KIND_CONVERSATION_RUN = "conversation_run"
KIND_MISSION_TASK = "mission_task"  # ConversationRun.kind of a mission worker attempt
MISSION_STAGE_KINDS = (KIND_MISSION_PLANNER, KIND_MISSION_SYNTHESIS)  # mission-level runs, no task id


class _Subscriber:
    """Bounded live queue for one SSE client. On overflow it is flagged and
    dropped: the consumer ends its stream and the client replays from the DB."""

    def __init__(self, run_id: str, maxsize: int, loop: asyncio.AbstractEventLoop):
        self.run_id = run_id
        self.queue: "asyncio.Queue[Dict[str, Any]]" = asyncio.Queue(maxsize=maxsize)
        self.loop = loop
        self.overflowed = False

    def offer(self, event: Dict[str, Any]) -> None:  # runs on self.loop
        if self.overflowed:
            return
        try:
            self.queue.put_nowait(event)
        except asyncio.QueueFull:
            self.overflowed = True


class ConversationNotFound(Exception):
    pass


class ActiveRunExists(Exception):
    pass


class RequestKeyConflict(Exception):
    pass


class RunNotFound(Exception):
    pass


class TooManyRuns(Exception):
    pass


class UnsupportedEngine(Exception):
    pass


class MissionRunCancelled(Exception):
    """A mission worker run lost to a cancel (user, mission, or shutdown); no output is valid."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _default_runtime_factory(engine_id: str):
    if engine_id == "mock":
        from backend.app.runtime.mock_runtime import MockRuntime
        return MockRuntime()
    if engine_id == "opencode":
        from backend.app.runtime.opencode_runtime import RealOpenCodeRuntime
        return RealOpenCodeRuntime()
    if engine_id == "claude-code":
        from backend.app.runtime.claude_runtime import ClaudeCodeRuntime
        return ClaudeCodeRuntime()
    if engine_id == "codex":
        from backend.app.runtime.codex_runtime import CodexCliRuntime
        return CodexCliRuntime()
    if engine_id == "antigravity":
        from backend.app.runtime.agy_acp_runtime import AgyAcpRuntime
        return AgyAcpRuntime()
    raise UnsupportedEngine(engine_id)


_factory_managers = weakref.WeakValueDictionary()


def manager_for_factory(session_factory: Optional[Callable[[], Session]]) -> "ConversationRunManager":
    """Run manager for mission workers. Default DB -> the process-wide manager
    (same one the HTTP routes/cancel use); an injected session factory (tests)
    gets one cached manager bound to that same database."""
    from backend.app.db.session import SessionLocal
    if session_factory is None or session_factory is SessionLocal:
        from backend.app.api.routes.conversation_runs import get_run_manager
        return get_run_manager()
    key = (id(session_factory), id(asyncio.get_running_loop()))
    mgr = _factory_managers.get(key)
    if mgr is None:
        mgr = _factory_managers[key] = ConversationRunManager(session_factory=session_factory)
    return mgr


class ConversationRunManager:
    def __init__(
        self,
        session_factory: Optional[Callable[[], Session]] = None,
        runtime_factory: Optional[Callable[[str], Any]] = None,
        max_concurrent: int = 2,
        max_pending: int = 32,
        abort_grace: float = 5.0,
        subscriber_queue_size: int = 64,
        max_subscribers: int = 256,
        heartbeat_seconds: float = 15.0,
    ):
        self._subscriber_queue_size = max(1, subscriber_queue_size)
        self._max_subscribers = max(1, max_subscribers)
        self._heartbeat = heartbeat_seconds
        self._subscribers: Set[_Subscriber] = set()
        self._lock = threading.Lock()  # guards _subscribers snapshots and the pending-slot counter
        self._reserved = 0  # pending slots held by in-flight start_turn prep (not yet tasks)
        self._session_factory = session_factory
        self._runtime_factory = runtime_factory or _default_runtime_factory
        self._max_concurrent = max(1, max_concurrent)
        self._max_pending = max(1, max_pending)
        self._abort_grace = abort_grace
        self._sem: Optional[asyncio.Semaphore] = None
        self._tasks: Dict[str, asyncio.Task] = {}
        self._preparations: Set[asyncio.Task] = set()
        self._runtimes: Dict[str, Any] = {}  # one instance per engine so abort_task sees in-flight procs
        self._run_runtimes: Dict[str, Any] = {}  # mission runs: the exact adapter selected for the profile
        self._run_keys: Dict[str, str] = {}  # mission runs: session_title the adapter tracks the process by

    # -- helpers -------------------------------------------------------------
    def _session(self) -> Session:
        factory = self._session_factory
        if factory is None:
            from backend.app.db.session import SessionLocal
            factory = SessionLocal
        return factory()

    def _runtime(self, engine_id: str):
        rt = self._runtimes.get(engine_id)
        if rt is None:
            rt = self._runtimes[engine_id] = self._runtime_factory(engine_id)
        return rt

    @staticmethod
    def _key(run_id: str) -> str:
        # Adapters key in-flight processes by session_title.
        return f"conv-run-{run_id}"

    # -- durable events ------------------------------------------------------
    @staticmethod
    def _add_event(db: Session, run_id: str, type_: str, **data: Any) -> Dict[str, Any]:
        """Insert an event in the caller's transaction; returns its dict (id assigned).
        Caller must commit, then call `_publish`."""
        payload = {"run_id": run_id, "status": type_, **{k: v for k, v in data.items() if v is not None}}
        row = ConversationRunEvent(run_id=run_id, type=type_,
                                   data_json=json.dumps(payload, ensure_ascii=False))
        db.add(row)
        db.flush()
        return {"id": row.id, "run_id": run_id, "type": type_, "data": payload}

    @staticmethod
    def _event_dict(row: ConversationRunEvent) -> Dict[str, Any]:
        return {"id": row.id, "run_id": row.run_id, "type": row.type, "data": json.loads(row.data_json)}

    def _publish(self, events: List[Dict[str, Any]]) -> None:
        """Fan out committed events to live subscribers. Thread-safe, never blocks."""
        with self._lock:
            subs = list(self._subscribers)
        for sub in subs:
            for ev in events:
                if ev["run_id"] != sub.run_id:
                    continue
                try:
                    running = asyncio.get_running_loop()
                except RuntimeError:
                    running = None
                if running is sub.loop:
                    sub.offer(ev)
                else:
                    try:
                        sub.loop.call_soon_threadsafe(sub.offer, ev)
                    except RuntimeError:  # subscriber loop closed
                        with self._lock:
                            self._subscribers.discard(sub)

    def _replay(self, run_id: str, after_id: int, limit: int = 500) -> List[Dict[str, Any]]:
        db = self._session()
        try:
            rows = db.execute(
                select(ConversationRunEvent)
                .where(ConversationRunEvent.run_id == run_id, ConversationRunEvent.id > after_id)
                .order_by(ConversationRunEvent.id).limit(limit)).scalars().all()
            return [self._event_dict(r) for r in rows]
        finally:
            db.close()

    def _terminal_event_id(self, run_id: str) -> Optional[int]:
        db = self._session()
        try:
            return db.execute(
                select(ConversationRunEvent.id)
                .where(ConversationRunEvent.run_id == run_id,
                       ConversationRunEvent.type.in_(TERMINAL_STATUSES))
                .order_by(ConversationRunEvent.id).limit(1)).scalar()
        finally:
            db.close()

    def subscriber_capacity_exhausted(self) -> bool:
        return len(self._subscribers) >= self._max_subscribers

    def list_events(self, run_id: str, after_id: int = 0) -> List[Dict[str, Any]]:
        """All persisted events of a run after `after_id` (raises RunNotFound)."""
        self.get_run(run_id)
        out: List[Dict[str, Any]] = []
        while True:
            batch = self._replay(run_id, after_id)
            out.extend(batch)
            if len(batch) < 500:
                return out
            after_id = batch[-1]["id"]

    async def iter_events(self, run_id: str, last_event_id: int = 0) -> AsyncIterator[Optional[Dict[str, Any]]]:
        """Replay persisted events after `last_event_id`, then tail live ones.

        Yields event dicts, or None as a heartbeat tick. Ends after the terminal
        event has been delivered, or when this client is too slow (its bounded
        queue overflowed) — it then reconnects with its last seen id.
        Subscribe happens BEFORE the first DB read, so an event committed in
        between appears in the DB read, the queue, or both (deduped by id)."""
        await asyncio.to_thread(self.get_run, run_id)  # RunNotFound before subscribing
        sub = _Subscriber(run_id, self._subscriber_queue_size, asyncio.get_running_loop())
        with self._lock:
            if len(self._subscribers) >= self._max_subscribers:
                raise TooManyRuns("Too many event subscribers.")
            self._subscribers.add(sub)
        try:
            last = max(0, int(last_event_id))
            while True:  # replay in pages until caught up
                batch = await asyncio.to_thread(self._replay, run_id, last)
                for ev in batch:
                    last = ev["id"]
                    yield ev
                    if ev["type"] in TERMINAL_STATUSES:
                        return
                if len(batch) < 500:
                    break
            terminal_id = await asyncio.to_thread(self._terminal_event_id, run_id)
            if terminal_id is not None and terminal_id <= last:
                return  # client already saw the terminal event
            while True:
                if sub.overflowed:
                    return
                try:
                    ev = await asyncio.wait_for(sub.queue.get(), timeout=self._heartbeat)
                except asyncio.TimeoutError:
                    if sub.overflowed:
                        return
                    yield None
                    continue
                if ev["id"] <= last:
                    continue
                # Notifications may arrive out of commit order across threads.
                # Read the durable log in order instead of trusting queue order.
                while True:
                    batch = await asyncio.to_thread(self._replay, run_id, last)
                    for persisted in batch:
                        last = persisted["id"]
                        yield persisted
                        if persisted["type"] in TERMINAL_STATUSES:
                            return
                    if len(batch) < 500:
                        break
        finally:
            with self._lock:
                self._subscribers.discard(sub)

    # -- startup recovery ----------------------------------------------------
    def recover_interrupted(self) -> int:
        """Mark every queued/running run interrupted. Call once at process start,
        before serving requests. Never re-executes anything. Each transition and
        its event commit together."""
        db = self._session()
        try:
            ids = db.execute(select(ConversationRun.id)
                             .where(ConversationRun.status.in_(ACTIVE_STATUSES))).scalars().all()
            events = []
            for rid in ids:
                res = db.execute(
                    update(ConversationRun)
                    .where(ConversationRun.id == rid, ConversationRun.status.in_(ACTIVE_STATUSES))
                    .values(status="interrupted", error=INTERRUPTED_ERROR, finished_at=_now()))
                if res.rowcount:
                    expire_approvals_for_run(db, rid, "run_interrupted")
                    events.append(self._add_event(db, rid, "interrupted", error=INTERRUPTED_ERROR))
            db.commit()
            self._publish(events)
            return len(events)
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    # -- API operations ------------------------------------------------------
    def get_run(self, run_id: str) -> Dict[str, Any]:
        db = self._session()
        try:
            run = db.get(ConversationRun, run_id)
            if run is None:
                raise RunNotFound(run_id)
            return self._snapshot(db, run)
        finally:
            db.close()

    @staticmethod
    def _snapshot(db: Session, run: ConversationRun) -> Dict[str, Any]:
        reply = run.result_text if run.kind in (KIND_MISSION_TASK, *MISSION_STAGE_KINDS) else None
        if run.assistant_message_seq is not None:
            msg = db.get(ConversationMessage, run.assistant_message_seq)
            reply = msg.content if msg else None
        return {
            "id": run.id, "conversation_id": run.conversation_id, "engine": run.engine,
            "kind": run.kind or "conversation", "mission_id": run.mission_id,
            "mission_task_id": run.mission_task_id, "attempt": run.attempt,
            "model_id": run.model_id, "variant": run.variant, "status": run.status,
            "user_message_seq": run.user_message_seq,
            "assistant_message_seq": run.assistant_message_seq,
            "assistant_content": reply, "error": run.error,
            "skills": run_skills.public_meta(run.skills_json),
            "input_files": run_inputs.public_meta(run.input_files_json),
            "created_at": run.created_at, "started_at": run.started_at,
            "finished_at": run.finished_at,
        }

    def start_turn(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        """Synchronous variant (kept for tests/back-compat): does the blocking DB
        and file work on the CALLING thread, then schedules the task. Must be
        called from the running event loop, so it blocks that loop; HTTP code
        must use `start_turn_async`."""
        return self._finish_start(self._prepare_turn(*args, **kwargs))

    async def start_turn_async(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        """Same contract as `start_turn`, but all SQLite/skill/file work runs in
        a worker thread (own Session there; none is shared across threads). The
        task is created back on the calling loop. The prepare+schedule step is
        shielded so a client disconnect after the commit cannot strand a queued
        run with no task."""
        async def inner() -> Dict[str, Any]:
            prepared = await asyncio.to_thread(self._prepare_turn, *args, **kwargs)
            return self._finish_start(prepared)

        pending = asyncio.create_task(inner())
        self._preparations.add(pending)
        def finished(task):
            self._preparations.discard(task)
            if not task.cancelled():
                task.exception()  # retrieve errors even if the caller disconnected
        pending.add_done_callback(finished)
        return await asyncio.shield(pending)

    def _reserve_slot(self) -> None:
        with self._lock:
            if len(self._tasks) + self._reserved >= self._max_pending:
                raise TooManyRuns("Too many pending conversation runs.")
            self._reserved += 1

    def _release_slot(self) -> None:
        with self._lock:
            self._reserved = max(0, self._reserved - 1)

    def _finish_start(self, prepared: Dict[str, Any]) -> Dict[str, Any]:
        """Runs on the event loop: publish the queued event, create + register the task."""
        if not prepared.get("schedule"):
            return prepared["snap"]
        run_id = prepared["run_id"]
        try:
            self._publish([prepared["event"]])
            task = asyncio.get_running_loop().create_task(
                self._execute(run_id, prepared["engine"], prepared["model_id"], prepared["prompt"],
                              prepared["timeout"], prepared["variant"]), name=self._key(run_id))
            self._tasks[run_id] = task
            task.add_done_callback(lambda _t, rid=run_id: self._tasks.pop(rid, None))
        finally:
            self._release_slot()
        return prepared["snap"]

    def _prepare_turn(self, conversation_id: str, engine: str, model_id: str, prompt: str,
                      timeout: int = 90, variant: Optional[str] = None,
                      request_key: Optional[str] = None,
                      skills: Optional[List[Any]] = None,
                      input_files: Optional[List[str]] = None) -> Dict[str, Any]:
        """Blocking half of start_turn (thread-safe; owns its Session): persist the
        user message + queued run in one short transaction. Returns either
        {"snap"} (idempotent hit) or a dict with schedule=True, holding a pending
        slot that `_finish_start` releases.
        `skills`: optional (source_id, skill_id) pairs (or objects with those
        attributes); loaded once and frozen into the run row."""
        state = {"slot": False}
        try:
            result = self._prepare_turn_inner(state, conversation_id, engine, model_id, prompt, timeout,
                                              variant, request_key, skills, input_files)
            if result.get("schedule"):
                state["slot"] = False  # ownership passes to _finish_start
            return result
        finally:
            if state["slot"]:
                self._release_slot()

    def _prepare_turn_inner(self, state: Dict[str, bool], conversation_id: str, engine: str, model_id: str,
                            prompt: str, timeout: int, variant: Optional[str], request_key: Optional[str],
                            skills: Optional[List[Any]], input_files: Optional[List[str]]) -> Dict[str, Any]:
        if engine not in ENGINE_PROVIDERS:
            raise UnsupportedEngine(engine)
        if engine == "mock" and settings.RUNTIME_MODE != "mock":
            raise UnsupportedEngine(engine)
        wanted_refs = [tuple(r) if isinstance(r, (tuple, list)) else (r.source_id, r.skill_id)
                       for r in (skills or [])]
        wanted_files = run_inputs.normalize_paths(input_files or [])
        db = self._session()
        try:
            conversation = db.get(Conversation, conversation_id)
            if conversation is None:
                raise ConversationNotFound(conversation_id)

            def existing_request():
                if not request_key:
                    return None
                existing = db.query(ConversationRun).filter_by(conversation_id=conversation_id, request_key=request_key).first()
                if existing:
                    original = db.get(ConversationMessage, existing.user_message_seq)
                    # Skills compare by reference only (not by current file content), so a
                    # retry after a skill file changed returns the original run.
                    if (existing.engine, existing.model_id, existing.variant, existing.timeout,
                            original.content if original else None,
                            run_skills.stored_refs(existing.skills_json),
                            run_inputs.stored_refs(existing.input_files_json)) != (
                            engine, model_id, variant, timeout, prompt, wanted_refs, wanted_files):
                        raise RequestKeyConflict(request_key)
                    return self._snapshot(db, existing)
                return None

            existing = existing_request()
            if existing:
                return {"snap": existing}
            self._reserve_slot()
            state["slot"] = True
            previous = (db.query(ConversationMessage)
                        .filter_by(conversation_id=conversation_id)
                        .order_by(ConversationMessage.seq.desc()).limit(20).all())
            snapshots = run_skills.load_selected(db, wanted_refs) if wanted_refs else []
            file_snapshots = run_inputs.load_selected(db, conversation, wanted_files)
            skill_block = "\n\n".join(b for b in (run_skills.build_block(snapshots),
                                                  run_inputs.build_block(file_snapshots)) if b)
            if skill_block and run_skills.minimum_prompt_len(skill_block, prompt) > run_skills.MAX_PROMPT_CHARS:
                if file_snapshots:
                    raise run_inputs.InputFileError(
                        422, "prompt is too long to include the selected files; shorten it or select fewer")
                raise run_skills.SkillSelectionError(
                    422, "prompt is too long to include the selected skills; shorten it or select fewer skills")
            prompt_for_engine = prompt
            budget = 24000 - len(prompt) - 100 - (len(skill_block) + 2 if skill_block else 0)
            if previous and budget > 100:
                selected = []
                for item in previous:
                    candidate = [{"role": item.role, "content": item.content}, *selected]
                    encoded = json.dumps(candidate, ensure_ascii=False)
                    if len(encoded) > budget:
                        break
                    selected = candidate
                if selected:
                    history = json.dumps(selected, ensure_ascii=False)
                    prompt_for_engine = f"Previous conversation (JSON):\n{history}\n\nCurrent user message:\n{prompt}"
            if skill_block:
                if prompt_for_engine == prompt:
                    prompt_for_engine = f"{run_skills.USER_LABEL}{prompt}"
                prompt_for_engine = f"{skill_block}\n\n{prompt_for_engine}"
                assert len(prompt_for_engine) <= run_skills.MAX_PROMPT_CHARS
            try:
                message = ConversationMessage(conversation_id=conversation_id, role="user", content=prompt)
                db.add(message)
                db.flush()
                run = ConversationRun(conversation_id=conversation_id, engine=engine, model_id=model_id, variant=variant, request_key=request_key,
                                      skills_json=run_skills.dump(snapshots),
                                      input_files_json=run_inputs.dump(file_snapshots), timeout=timeout, status="queued", user_message_seq=message.seq)
                db.add(run)
                db.flush()
                queued_event = self._add_event(db, run.id, "queued")
                conversation.updated_at = _now()
                db.commit()
            except IntegrityError:
                db.rollback()
                if db.get(Conversation, conversation_id) is None:
                    raise ConversationNotFound(conversation_id)
                existing = existing_request()
                if existing:
                    return {"snap": existing}
                raise ActiveRunExists(conversation_id)
            run_id = run.id
            snap = self._snapshot(db, run)
        finally:
            db.close()
        return {"schedule": True, "snap": snap, "event": queued_event, "run_id": run_id, "engine": engine,
                "model_id": model_id, "prompt": prompt_for_engine, "timeout": timeout, "variant": variant}

    def _cancel_db(self, run_id: str):
        """Blocking half of cancel (own Session). Returns (won, prior, engine, event)."""
        db = self._session()
        try:
            run = db.get(ConversationRun, run_id)
            if run is None:
                raise RunNotFound(run_id)
            prior = run.status
            engine = run.engine
            won = False
            event = None
            if prior in ACTIVE_STATUSES:
                res = db.execute(
                    update(ConversationRun)
                    .where(ConversationRun.id == run_id, ConversationRun.status.in_(ACTIVE_STATUSES))
                    .values(status="cancelled", error="Cancelled by user.", finished_at=_now()))
                won = bool(res.rowcount)
                if won:
                    expire_approvals_for_run(db, run_id, "run_cancelled")
                    event = self._add_event(db, run_id, "cancelled", error="Cancelled by user.")
                db.commit()
            return won, prior, engine, event
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    async def cancel(self, run_id: str) -> Dict[str, Any]:
        """Persist cancellation first (so a late result can never win), then
        abort the CLI process and cancel the asyncio task. The DB work runs in a
        worker thread; shielded so a caller cancel cannot skip the abort/task
        cancel after the row is already marked cancelled."""
        return await asyncio.shield(asyncio.ensure_future(self._cancel_impl(run_id)))

    async def _cancel_impl(self, run_id: str) -> Dict[str, Any]:
        won, prior, engine, event = await asyncio.to_thread(self._cancel_db, run_id)
        if event:
            self._publish([event])
        if won:
            task = self._tasks.get(run_id)
            if prior == "running":
                try:
                    rt = self._run_runtimes.get(run_id) or self._runtime(engine)
                    await asyncio.wait_for(rt.abort_task(self._run_keys.get(run_id, self._key(run_id))),
                                           timeout=self._abort_grace)
                except Exception as exc:  # best effort; task cancel below also kills the CLI
                    logger.warning("abort_task failed for run %s: %s", run_id, type(exc).__name__)
            if task is not None and not task.done():
                task.cancel()
                await asyncio.wait({task}, timeout=self._abort_grace)
        return await asyncio.to_thread(self.get_run, run_id)

    async def shutdown(self) -> None:
        """Cancel in-flight tasks without touching DB state (rows stay active and
        are marked interrupted on next start)."""
        if self._preparations:
            await asyncio.gather(*list(self._preparations), return_exceptions=True)
        tasks = list(self._tasks.values())
        for t in tasks:
            t.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()
        self._sem = None

    # -- execution -----------------------------------------------------------
    def _semaphore(self) -> asyncio.Semaphore:
        if self._sem is None:
            self._sem = asyncio.Semaphore(self._max_concurrent)
        return self._sem

    async def _execute(self, run_id: str, engine: str, model_id: str, prompt: str, timeout: int, variant: Optional[str] = None) -> None:
        try:
            async with self._semaphore():
                if not await self._atransition(run_id, "queued", "running", started_at=_now()):
                    return  # cancelled while waiting
                task = ExecuteAgentTask(prompt=prompt, model_id=model_id, variant=variant,
                                        provider_id=ENGINE_PROVIDERS[engine], timeout=timeout,
                                        agent_id=settings.CHAT_AGENT_ID if engine == "opencode" else None,
                                        session_title=self._key(run_id))
                try:
                    result = await run_agent_task(self._runtime(engine), task, kind=KIND_CONVERSATION_RUN, run_id=run_id)
                except asyncio.CancelledError:
                    raise  # cancel() already persisted; shutdown leaves row for recovery
                except Exception as exc:
                    await self._atransition(run_id, "running", "failed",
                                            error=f"Engine request failed ({getattr(exc, 'code', 'runtime_error')}).", finished_at=_now())
                    return
                await self._acomplete(run_id, conversation_text=result.text)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("conversation run %s crashed (%s)", run_id, type(exc).__name__)
            try:
                await self._atransition(run_id, "running", "failed", error="Internal error.", finished_at=_now())
            except Exception as exc:
                logger.error("could not mark run %s failed (%s)", run_id, type(exc).__name__)

    # Sync helpers (kept for tests/back-compat) run on the calling thread.
    # `_execute` uses the `_a*` variants, which run the blocking `_*_db` part in
    # a worker thread (own Session) and publish back on the event loop.
    def _transition(self, run_id: str, from_status: str, to_status: str, **values: Any) -> bool:
        event = self._transition_db(run_id, from_status, to_status, **values)
        if event is None:
            return False
        self._publish([event])
        return True

    def _complete(self, run_id: str, conversation_text: str) -> bool:
        event = self._complete_db(run_id, conversation_text)
        if event is None:
            return False
        self._publish([event])
        return True

    async def _atransition(self, run_id: str, from_status: str, to_status: str, **values: Any) -> bool:
        event = await asyncio.to_thread(self._transition_db, run_id, from_status, to_status, **values)
        if event is None:
            return False
        self._publish([event])
        return True

    async def _acomplete(self, run_id: str, conversation_text: str) -> bool:
        event = await asyncio.to_thread(self._complete_db, run_id, conversation_text)
        if event is None:
            return False
        self._publish([event])
        return True

    # -- mission worker attempts --------------------------------------------
    # A mission task attempt is a first-class ConversationRun (kind="mission_task",
    # conversation_id NULL): same table, status machine, durable events, cancel
    # endpoint and interrupted-recovery as chat turns. Success and start are
    # conditional on the owning mission/task not being cancelled, so a committed
    # mission cancel can never be followed by a succeeded run. The caller keeps
    # its own concurrency bound (mission semaphore), plus the manager's global bound.
    @staticmethod
    def _mission_alive_clauses() -> List[Any]:
        # Worker runs (task id set) must have a live task. Mission-level stage runs
        # (planner/synthesis) have no task id; only the mission must be live.
        return [
            ConversationRun.mission_id.in_(
                select(Mission.id).where(Mission.status.in_(("queued", "planning", "running")))
            ),
            or_(
                and_(ConversationRun.kind.in_(MISSION_STAGE_KINDS), ConversationRun.mission_task_id.is_(None)),
                ConversationRun.mission_task_id.in_(
                    select(MissionTask.id).where(MissionTask.status.in_(("queued", "running")))
                ),
            ),
        ]

    def _prepare_mission_run(self, runtime: Any, mission_id: str, task_id: Optional[str], attempt: int, engine: str,
                             model_id: str, variant: Optional[str], timeout: int, key: str,
                             kind: str = KIND_MISSION_TASK) -> Dict[str, Any]:
        self._reserve_slot()
        try:
            db = self._session()
            try:
                run = ConversationRun(kind=kind, conversation_id=None, mission_id=mission_id,
                                      mission_task_id=task_id, attempt=attempt, engine=engine, model_id=model_id,
                                      variant=variant, timeout=timeout, status="queued")
                db.add(run)
                db.flush()
                event = self._add_event(db, run.id, "queued", mission_id=mission_id,
                                        mission_task_id=task_id, attempt=attempt)
                db.commit()
                run_id = run.id
            except Exception:
                db.rollback()
                raise
            finally:
                db.close()
        except Exception:
            self._release_slot()
            raise
        return {"run_id": run_id, "event": event}

    def _mission_guarded_db(self, run_id: str, from_status: str, to_status: str,
                            **values: Any) -> Optional[Dict[str, Any]]:
        """Conditional transition that also requires mission + task not cancelled."""
        db = self._session()
        try:
            res = db.execute(
                update(ConversationRun)
                .where(ConversationRun.id == run_id, ConversationRun.status == from_status,
                       *self._mission_alive_clauses())
                .values(status=to_status, **values).execution_options(synchronize_session=False))
            if not res.rowcount:
                db.rollback()
                return None
            if to_status in TERMINAL_STATUSES:
                expire_approvals_for_run(db, run_id, f"run_{to_status}")
            event = self._add_event(db, run_id, to_status, error=values.get("error"))
            db.commit()
            return event
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    async def _amission(self, run_id: str, from_status: str, to_status: str, **values: Any) -> bool:
        event = await asyncio.to_thread(self._mission_guarded_db, run_id, from_status, to_status, **values)
        if event is None:
            return False
        self._publish([event])
        return True

    async def run_mission_task(self, *, runtime: Any, mission_id: str, task_id: Optional[str], attempt: int,
                               engine: str, provider_id: str, model_id: str, variant: Optional[str],
                               prompt: str, session_title: str, timeout: int,
                               max_output_chars: int = 20000,
                               validate: Optional[Callable[[str], Any]] = None,
                               kind: str = KIND_MISSION_TASK) -> Any:
        """Run ONE mission worker attempt through a persisted run.

        `runtime` is the engine adapter the mission already selected for the
        profile (never swapped here). Returns (AgentResult, validated) where
        `validated` is `validate(text)`'s return value (None without validator).
        Raises MissionRunCancelled if the run lost to a cancel, asyncio.TimeoutError
        on timeout, or the engine/validation exception (run is then `failed`).
        Awaiting caller cancellation also cancels the run task.
        `kind` mission_planner/mission_synthesis = mission-level stage run (task_id None)."""
        if kind in MISSION_STAGE_KINDS:
            task_id = None
        elif kind != KIND_MISSION_TASK or not task_id:
            raise ValueError("worker mission runs require KIND_MISSION_TASK and a task id")
        ledger_kind = kind if kind in MISSION_STAGE_KINDS else KIND_MISSION_DELEGATE

        async def prepare():
            prepared = await asyncio.to_thread(self._prepare_mission_run, runtime, mission_id, task_id, attempt,
                                               engine, model_id, variant, timeout, session_title, kind)
            run_id = prepared["run_id"]
            try:
                self._publish([prepared["event"]])
                self._run_runtimes[run_id] = runtime
                self._run_keys[run_id] = session_title
                task = asyncio.create_task(
                    self._execute_mission_run(run_id, runtime, provider_id, model_id, variant, prompt,
                                              session_title, timeout, max_output_chars, validate, ledger_kind),
                    name=self._key(run_id))
                self._tasks[run_id] = task
                task.add_done_callback(lambda _t, rid=run_id: self._forget_run(rid))
                return run_id, task
            finally:
                self._release_slot()

        pending = asyncio.create_task(prepare())
        self._preparations.add(pending)
        pending.add_done_callback(self._preparations.discard)
        try:
            run_id, task = await asyncio.shield(pending)
        except asyncio.CancelledError:
            # A committed queued row must still be scheduled/settled if its caller
            # disappears while the worker thread is preparing the transaction.
            run_id, task = await asyncio.shield(pending)
            await self.cancel(run_id)
            raise
        try:
            await asyncio.wait({task})
        except asyncio.CancelledError:
            await self.cancel(run_id)
            raise
        if task.cancelled():
            raise MissionRunCancelled(run_id)
        return task.result()

    def _forget_run(self, run_id: str) -> None:
        self._tasks.pop(run_id, None)
        self._run_runtimes.pop(run_id, None)
        self._run_keys.pop(run_id, None)

    async def _execute_mission_run(self, run_id: str, runtime: Any, provider_id: str, model_id: str,
                                   variant: Optional[str], prompt: str, session_title: str, timeout: int,
                                   max_output_chars: int, validate: Optional[Callable[[str], Any]],
                                   ledger_kind: str = KIND_MISSION_DELEGATE) -> Any:
        async with self._semaphore():
            return await self._execute_mission_body(run_id, runtime, provider_id, model_id, variant,
                                                     prompt, session_title, timeout, max_output_chars, validate,
                                                     ledger_kind)

    async def _execute_mission_body(self, run_id: str, runtime: Any, provider_id: str, model_id: str,
                                   variant: Optional[str], prompt: str, session_title: str, timeout: int,
                                   max_output_chars: int, validate: Optional[Callable[[str], Any]],
                                   ledger_kind: str = KIND_MISSION_DELEGATE) -> Any:
        if not await self._amission(run_id, "queued", "running", started_at=_now()):
            await self._acancel_run_if_active(run_id, "Mission cancelled.")
            raise MissionRunCancelled(run_id)
        req = ExecuteAgentTask(prompt=prompt, model_id=model_id, provider_id=provider_id, variant=variant,
                               session_title=session_title, timeout=timeout)
        try:
            result = await asyncio.wait_for(
                run_agent_task(runtime, req, kind=ledger_kind, run_id=run_id), timeout=timeout + 30)
            text = (result.text or "")[:max_output_chars]
            validated = validate(text) if validate else None
        except asyncio.CancelledError:
            raise  # cancel() already persisted; shutdown leaves row for recovery
        except asyncio.TimeoutError:
            if not await self._amission(run_id, "running", "failed", error=f"Timed out after {timeout}s.",
                                        finished_at=_now()):
                raise MissionRunCancelled(run_id)
            raise
        except Exception as exc:
            if not await self._amission(run_id, "running", "failed", error=safe_error_message(exc)[:500],
                                        finished_at=_now()):
                raise MissionRunCancelled(run_id)
            raise
        event = await asyncio.to_thread(self._mission_guarded_db, run_id, "running", "succeeded",
                                        finished_at=_now(), result_text=text)
        if event is None:
            await self._acancel_run_if_active(run_id, "Mission cancelled.")
            raise MissionRunCancelled(run_id)
        self._publish([event])
        return result, validated

    async def _acancel_run_if_active(self, run_id: str, error: str) -> None:
        """Force an active run that lost a mission guard to `cancelled` (idempotent)."""
        for from_status in ACTIVE_STATUSES:
            event = await asyncio.to_thread(self._transition_db, run_id, from_status, "cancelled",
                                            error=error, finished_at=_now())
            if event is not None:
                self._publish([event])
                return

    async def cancel_mission_runs(self, mission_id: str) -> int:
        """Cancel every active run of a mission (persist first, then abort the CLI)."""
        def ids() -> List[str]:
            db = self._session()
            try:
                return list(db.execute(select(ConversationRun.id).where(
                    ConversationRun.mission_id == mission_id,
                    ConversationRun.status.in_(ACTIVE_STATUSES))).scalars().all())
            finally:
                db.close()
        run_ids = await asyncio.to_thread(ids)
        for rid in run_ids:
            await self.cancel(rid)
        return len(run_ids)

    def _transition_db(self, run_id: str, from_status: str, to_status: str, **values: Any) -> Optional[Dict[str, Any]]:
        db = self._session()
        try:
            res = db.execute(
                update(ConversationRun)
                .where(ConversationRun.id == run_id, ConversationRun.status == from_status)
                .values(status=to_status, **values))
            if not res.rowcount:
                db.rollback()
                return None
            if to_status in TERMINAL_STATUSES:
                expire_approvals_for_run(db, run_id, f"run_{to_status}")
            event = self._add_event(db, run_id, to_status, error=values.get("error"))
            db.commit()
            return event
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def _complete_db(self, run_id: str, conversation_text: str) -> Optional[Dict[str, Any]]:
        """Win running->succeeded and write the assistant message atomically."""
        db = self._session()
        try:
            res = db.execute(
                update(ConversationRun)
                .where(ConversationRun.id == run_id, ConversationRun.status == "running")
                .values(status="succeeded", finished_at=_now()))
            if not res.rowcount:
                db.rollback()
                return None
            run = db.get(ConversationRun, run_id)
            message = ConversationMessage(conversation_id=run.conversation_id, role="assistant",
                                          content=conversation_text)
            db.add(message)
            db.flush()
            run.assistant_message_seq = message.seq
            conversation = db.get(Conversation, run.conversation_id)
            if conversation is not None:
                conversation.updated_at = _now()
            expire_approvals_for_run(db, run_id, "run_succeeded")
            event = self._add_event(db, run_id, "succeeded", assistant_message_seq=message.seq)
            db.commit()
            return event
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()
