"""Bounded, persisted conversation run manager (stdlib port).

Each turn = one user message + one ``conversation_runs`` row (queued) committed
in a single short transaction; an asyncio task then sends the prompt through
the engine adapter (via the usage-ledger facade) and persists the reply.

Guarantees (identical to the legacy implementation)
- One active (queued/running) run per conversation: a partial unique index in
  the DB enforces it, so concurrent requests cannot both win.
- Every status transition is a conditional UPDATE (``WHERE status ...``): a
  cancelled run can never become succeeded, and no assistant message is written
  unless the same transaction won the transition.
- Concurrency is bounded (semaphore + cap on pending tasks).
- On process start, active runs are marked ``interrupted``; nothing re-runs.

Durable events
- Every transition inserts a ``conversation_run_events`` row in the SAME
  transaction (type == new status; ids are monotonic AUTOINCREMENT).
- ``iter_events`` serves replay + live tail: it subscribes BEFORE reading the
  DB, dedupes by id, and drops subscribers whose bounded queue overflows (the
  client reconnects with its last id and replays from the DB).
Legacy delivery remains lifecycle only. Driver envelopes are persisted alongside
lifecycle rows and exposed only through explicit normalised-v1 projection.

All database work is synchronous on the event-loop thread (single connection),
so there is no cross-thread publish and no thread-pool detour.
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict
import json
import logging
import sqlite3
from typing import Any, AsyncIterator, Callable, Dict, List, Optional, Set

from backend.pyworker.appsettings import settings
from backend.pyworker.core import run_inputs, run_skills
from backend.pyworker.core.approvals import expire_for_run as expire_approvals_for_run
from backend.pyworker.db.core import Database, iso, new_id, ts
from backend.pyworker.runtime.base import ExecuteAgentTask
from backend.pyworker.runtime.execute_driver import ExecuteTaskDriver
from backend.pyworker.runtime.engine_registry import create_engine_runtime
from backend.pyworker.runtime.errors import safe_error_message
from backend.pyworker.runtime.providers import ledger_provider
from backend.pyworker.runtime.ledger import (
    KIND_MISSION_DELEGATE,
    KIND_MISSION_PLANNER,
    KIND_MISSION_SYNTHESIS,
    run_agent_task,
)

logger = logging.getLogger("agentos.conversation_runs")

ACTIVE_STATUSES = ("queued", "running")
TERMINAL_STATUSES = ("succeeded", "failed", "cancelled", "interrupted")
INTERRUPTED_ERROR = "Run interrupted by server restart or shutdown; it was not re-executed."
KIND_CONVERSATION_RUN = "conversation_run"
KIND_MISSION_TASK = "mission_task"
MISSION_STAGE_KINDS = (KIND_MISSION_PLANNER, KIND_MISSION_SYNTHESIS)


class _Subscriber:
    """Bounded live queue for one stream. On overflow it is flagged and
    dropped: the consumer ends its stream and the client replays from the DB."""

    def __init__(self, run_id: str, maxsize: int):
        self.run_id = run_id
        self.queue: "asyncio.Queue[Dict[str, Any]]" = asyncio.Queue(maxsize=maxsize)
        self.overflowed = False

    def offer(self, event: Dict[str, Any]) -> None:
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
    """A mission worker run lost to a cancel (user, mission, or shutdown)."""


class ConversationRunManager:
    def __init__(
        self,
        db: Database,
        runtime_factory: Optional[Callable[[str], Any]] = None,
        max_concurrent: int = 2,
        max_pending: int = 32,
        abort_grace: float = 5.0,
        subscriber_queue_size: int = 64,
        max_subscribers: int = 256,
        heartbeat_seconds: float = 15.0,
    ):
        self._db = db
        self._subscriber_queue_size = max(1, subscriber_queue_size)
        self._max_subscribers = max(1, max_subscribers)
        self._heartbeat = heartbeat_seconds
        self._subscribers: Set[_Subscriber] = set()
        self._runtime_factory = runtime_factory or create_engine_runtime
        self._max_concurrent = max(1, max_concurrent)
        self._max_pending = max(1, max_pending)
        self._abort_grace = abort_grace
        self._sem: Optional[asyncio.Semaphore] = None
        self._tasks: Dict[str, asyncio.Task] = {}
        self._runtimes: Dict[str, Any] = {}      # one instance per engine so abort_task sees in-flight procs
        self._run_runtimes: Dict[str, Any] = {}  # mission runs: the exact adapter selected for the profile
        self._run_keys: Dict[str, str] = {}      # mission runs: session_title the adapter tracks the process by
        self._tool_sessions = {}
        self._computer_owner = None
        self._run_drivers = {}
        self._driver_terminals = {}

    # -- helpers -------------------------------------------------------------
    def _runtime(self, engine_id: str):
        rt = self._runtimes.get(engine_id)
        if rt is None:
            rt = self._runtimes[engine_id] = self._runtime_factory(engine_id)
        return rt

    @staticmethod
    def _key(run_id: str) -> str:
        return f"conv-run-{run_id}"  # adapters key in-flight processes by session_title

    @property
    def active_tasks(self) -> int:
        return len(self._tasks)

    # -- durable events ------------------------------------------------------
    def _add_event(self, run_id: str, type_: str, **data: Any) -> Dict[str, Any]:
        """Insert an event in the caller's transaction; caller commits then publishes."""
        if type_ in TERMINAL_STATUSES:
            terminal = self._driver_terminals.pop(run_id, None)
            if terminal is not None:
                self._insert_driver_event(terminal)
        payload = {"run_id": run_id, "status": type_, **{k: v for k, v in data.items() if v is not None}}
        cur = self._db.execute(
            "INSERT INTO conversation_run_events(run_id,type,data_json,created_at) VALUES(?,?,?,?)",
            (run_id, type_, json.dumps(payload, ensure_ascii=False), ts()))
        return {"id": cur.lastrowid, "run_id": run_id, "type": type_, "data": payload}

    def _insert_driver_event(self, event):
        payload = {"run_id": event.run_id, "status": "running", "event": asdict(event)}
        cur = self._db.execute(
            "INSERT OR IGNORE INTO conversation_run_events(run_id,type,data_json,created_at,driver_event_id) "
            "VALUES(?,?,?,?,?)", (event.run_id, "runtime", json.dumps(payload, ensure_ascii=False),
                                  event.created_at, event.event_id))
        if not cur.rowcount:
            return None
        return {"id": cur.lastrowid, "run_id": event.run_id, "type": "runtime", "data": payload}

    async def _driver_event(self, event):
        # Cancellation is durable first. Late terminals are cleanup acknowledgements.
        if self._db.scalar("SELECT status FROM conversation_runs WHERE id=?", (event.run_id,)) not in ACTIVE_STATUSES:
            return
        if event.type == "turn.completed":
            self._driver_terminals.setdefault(event.run_id, event)
            return
        with self._db.tx():
            row = self._insert_driver_event(event)
        if row is not None:
            self._publish([row])

    @staticmethod
    def _project_event(event, event_format):
        if event["type"] != "runtime":
            return event
        if event_format == "legacy":
            return None
        envelope = event["data"]["event"]
        return {**event, "type": envelope["type"], "data": envelope}

    @staticmethod
    def _check_event_format(event_format):
        if event_format not in {"legacy", "normalised-v1"}:
            raise ValueError("Unknown event format")

    @staticmethod
    def _event_dict(row: Dict[str, Any]) -> Dict[str, Any]:
        return {"id": row["id"], "run_id": row["run_id"], "type": row["type"],
                "data": json.loads(row["data_json"])}

    def _publish(self, events: List[Dict[str, Any]]) -> None:
        for sub in list(self._subscribers):
            for ev in events:
                if ev["run_id"] == sub.run_id:
                    sub.offer(ev)

    def _replay(self, run_id: str, after_id: int, limit: int = 500) -> List[Dict[str, Any]]:
        rows = self._db.all(
            "SELECT * FROM conversation_run_events WHERE run_id=? AND id>? ORDER BY id LIMIT ?",
            (run_id, after_id, limit))
        return [self._event_dict(r) for r in rows]

    def _terminal_event_id(self, run_id: str) -> Optional[int]:
        q = ",".join("?" for _ in TERMINAL_STATUSES)
        return self._db.scalar(
            f"SELECT id FROM conversation_run_events WHERE run_id=? AND type IN ({q}) ORDER BY id LIMIT 1",
            (run_id, *TERMINAL_STATUSES))

    def subscriber_capacity_exhausted(self) -> bool:
        return len(self._subscribers) >= self._max_subscribers

    def list_events(self, run_id: str, after_id: int = 0, event_format: str = "legacy") -> List[Dict[str, Any]]:
        """All persisted events of a run after ``after_id`` (raises RunNotFound)."""
        self.get_run(run_id)
        self._check_event_format(event_format)
        out: List[Dict[str, Any]] = []
        while True:
            batch = self._replay(run_id, after_id)
            out.extend(projected for ev in batch if (projected := self._project_event(ev, event_format)) is not None)
            if len(batch) < 500:
                return out
            after_id = batch[-1]["id"]

    async def iter_events(self, run_id: str, last_event_id: int = 0,
                          event_format: str = "legacy") -> AsyncIterator[Optional[Dict[str, Any]]]:
        """Replay persisted events after ``last_event_id`` then tail live ones.

        Yields event dicts, or None as a heartbeat tick. Ends after the
        terminal event was delivered, or when this client is too slow."""
        self.get_run(run_id)  # RunNotFound before subscribing
        self._check_event_format(event_format)
        sub = _Subscriber(run_id, self._subscriber_queue_size)
        if len(self._subscribers) >= self._max_subscribers:
            raise TooManyRuns("Too many event subscribers.")
        self._subscribers.add(sub)
        try:
            last = max(0, int(last_event_id))
            while True:  # replay in pages until caught up
                batch = self._replay(run_id, last)
                for ev in batch:
                    last = ev["id"]
                    projected = self._project_event(ev, event_format)
                    if projected is not None:
                        yield projected
                    if ev["type"] in TERMINAL_STATUSES:
                        return
                if len(batch) < 500:
                    break
            terminal_id = self._terminal_event_id(run_id)
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
                # Read the durable log in order instead of trusting queue order.
                while True:
                    batch = self._replay(run_id, last)
                    for persisted in batch:
                        last = persisted["id"]
                        projected = self._project_event(persisted, event_format)
                        if projected is not None:
                            yield projected
                        if persisted["type"] in TERMINAL_STATUSES:
                            return
                    if len(batch) < 500:
                        break
        finally:
            self._subscribers.discard(sub)

    # -- startup recovery ----------------------------------------------------
    def recover_interrupted(self) -> int:
        """Mark every queued/running run interrupted (once, before serving)."""
        events = []
        with self._db.tx():
            ids = [r["id"] for r in self._db.all(
                "SELECT id FROM conversation_runs WHERE status IN ('queued','running')")]
            for rid in ids:
                changed = self._db.update(
                    "conversation_runs",
                    {"status": "interrupted", "error": INTERRUPTED_ERROR, "finished_at": ts()},
                    "id=? AND status IN ('queued','running')", (rid,))
                if changed:
                    expire_approvals_for_run(self._db, rid, "run_interrupted")
                    events.append(self._add_event(rid, "interrupted", error=INTERRUPTED_ERROR))
        self._publish(events)
        return len(events)

    # -- API operations ------------------------------------------------------
    def get_run(self, run_id: str) -> Dict[str, Any]:
        run = self._db.one("SELECT * FROM conversation_runs WHERE id=?", (run_id,))
        if run is None:
            raise RunNotFound(run_id)
        return self.snapshot(run)

    def snapshot(self, run: Dict[str, Any]) -> Dict[str, Any]:
        reply = run["result_text"] if (run["kind"] in (KIND_MISSION_TASK, *MISSION_STAGE_KINDS)) else None
        if run["assistant_message_seq"] is not None:
            msg = self._db.one("SELECT content FROM conversation_messages WHERE seq=?",
                               (run["assistant_message_seq"],))
            reply = msg["content"] if msg else None
        return {
            "id": run["id"], "conversation_id": run["conversation_id"], "engine": run["engine"],
            "kind": run["kind"] or "conversation", "mission_id": run["mission_id"],
            "mission_task_id": run["mission_task_id"], "attempt": run["attempt"],
            "model_id": run["model_id"], "variant": run["variant"], "status": run["status"],
            "user_message_seq": run["user_message_seq"],
            "assistant_message_seq": run["assistant_message_seq"],
            "assistant_content": reply, "error": run["error"],
            "skills": run_skills.public_meta(run["skills_json"]),
            "input_files": run_inputs.public_meta(run["input_files_json"]),
            "tool_mode": json.loads(run.get("tool_config_json") or "{}").get("mode", "none"),
            "created_at": iso(run["created_at"]), "started_at": iso(run["started_at"]),
            "finished_at": iso(run["finished_at"]),
        }

    # -- turn start ----------------------------------------------------------
    def start_turn(self, conversation_id: str, engine: str, model_id: str, prompt: str,
                   timeout: int = 90, variant: Optional[str] = None, request_key: Optional[str] = None,
                   skills: Optional[List[Any]] = None, input_files: Optional[List[str]] = None,
                   tool_mode: str = "none", computer_window: Optional[int] = None) -> Dict[str, Any]:
        """Persist the user message + queued run, then schedule the task.
        Must run on the event loop (it creates the asyncio task)."""
        prepared = self._prepare_turn(conversation_id, engine, model_id, prompt, timeout, variant,
                                      request_key, skills, input_files, tool_mode, computer_window)
        return self._finish_start(prepared)

    def _finish_start(self, prepared: Dict[str, Any]) -> Dict[str, Any]:
        if not prepared.get("schedule"):
            return prepared["snap"]
        run_id = prepared["run_id"]
        self._publish([prepared["event"]])
        task = asyncio.get_running_loop().create_task(
            self._execute(run_id, prepared["engine"], prepared["model_id"], prepared["prompt"],
                          prepared["timeout"], prepared["variant"]), name=self._key(run_id))
        self._tasks[run_id] = task
        task.add_done_callback(lambda _t, rid=run_id: self._tasks.pop(rid, None))
        return prepared["snap"]

    def _prepare_turn(self, conversation_id: str, engine: str, model_id: str, prompt: str, timeout: int,
                      variant: Optional[str], request_key: Optional[str], skills: Optional[List[Any]],
                      input_files: Optional[List[str]], tool_mode: str = "none",
                      computer_window: Optional[int] = None) -> Dict[str, Any]:
        if ledger_provider(engine) is None:
            raise UnsupportedEngine(engine)
        if engine == "mock" and settings.RUNTIME_MODE != "mock":
            raise UnsupportedEngine(engine)
        if settings.RUNTIME_MODE == "mock" and engine != "mock":
            raise UnsupportedEngine(engine)
        db = self._db
        wanted_refs = [tuple(r) if isinstance(r, (tuple, list)) else (r.source_id, r.skill_id)
                       for r in (skills or [])]
        wanted_files = run_inputs.normalize_paths(input_files or [])
        conversation = db.one("SELECT * FROM conversations WHERE id=?", (conversation_id,))
        if conversation is None:
            raise ConversationNotFound(conversation_id)
        tool_config = None
        if tool_mode != "none":
            if tool_mode not in ("read", "write", "browser", "full", "computer") or engine != "claude-code":
                raise UnsupportedEngine("Workspace tools currently require claude-code.")
            workspace = db.one("SELECT * FROM workspaces WHERE id=?", (conversation["workspace_id"],))
            if workspace is None:
                raise run_inputs.InputFileError(422, "Workspace tools require a registered workspace.")
            tool_config = {"mode": tool_mode, "workspace_id": workspace["id"], "path": workspace["path"]}
            if computer_window is not None:
                from backend.pyworker.core.computer import windows
                selected = next((w for w in windows() if w["handle"] == computer_window), None)
                if selected is None or tool_mode not in ("computer", "full"):
                    raise run_inputs.InputFileError(422, "Select a visible Windows window for computer tools.")
                tool_config["computer_window"] = selected
            elif tool_mode == "computer":
                raise run_inputs.InputFileError(422, "computer_window is required.")
        elif computer_window is not None:
            raise run_inputs.InputFileError(422, "Computer tools require computer or full mode.")

        def existing_request():
            if not request_key:
                return None
            existing = db.one("SELECT * FROM conversation_runs WHERE conversation_id=? AND request_key=?",
                              (conversation_id, request_key))
            if existing:
                if json.loads(existing.get("tool_config_json") or "null") != tool_config:
                    raise RequestKeyConflict(request_key)
                original = db.one("SELECT content FROM conversation_messages WHERE seq=?",
                                  (existing["user_message_seq"],))
                if (existing["engine"], existing["model_id"], existing["variant"], existing["timeout"],
                        original["content"] if original else None,
                        run_skills.stored_refs(existing["skills_json"]),
                        run_inputs.stored_refs(existing["input_files_json"])) != (
                        engine, model_id, variant, timeout, prompt, wanted_refs, wanted_files):
                    raise RequestKeyConflict(request_key)
                return self.snapshot(existing)
            return None

        existing = existing_request()
        if existing:
            return {"snap": existing}
        if len(self._tasks) >= self._max_pending:
            raise TooManyRuns("Too many pending conversation runs.")
        previous = db.all(
            "SELECT * FROM conversation_messages WHERE conversation_id=? ORDER BY seq DESC LIMIT 20",
            (conversation_id,))
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
        profile_snapshot = None
        if conversation.get("profile_id"):
            from backend.pyworker.core.profiles import get_by_id
            profile_snapshot = get_by_id(db, conversation["profile_id"])
            if profile_snapshot is None:
                raise run_inputs.InputFileError(422, "Conversation profile is no longer available.")
            profile_block = "Agent instructions:\n" + profile_snapshot["instructions"]
            if profile_snapshot.get("output_schema_json"):
                profile_block += "\nOutput schema:\n" + profile_snapshot["output_schema_json"]
            skill_block = "\n\n".join(b for b in (profile_block, skill_block) if b)
            if len(skill_block) + len(prompt) + 100 > 24000:
                raise run_inputs.InputFileError(422, "Profile and inputs exceed the prompt budget.")
        budget = 24000 - len(prompt) - 100 - (len(skill_block) + 2 if skill_block else 0)
        if previous and budget > 100:
            selected: List[Dict[str, Any]] = []
            for item in previous:
                candidate = [{"role": item["role"], "content": item["content"]}, *selected]
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
        now = ts()
        try:
            with db.tx():
                cur = db.execute(
                    "INSERT INTO conversation_messages(conversation_id,role,content,created_at) VALUES(?,?,?,?)",
                    (conversation_id, "user", prompt, now))
                user_seq = cur.lastrowid
                run_id = new_id()
                db.insert("conversation_runs", {
                    "id": run_id, "conversation_id": conversation_id, "kind": "conversation",
                    "engine": engine, "model_id": model_id, "variant": variant, "request_key": request_key,
                    "skills_json": run_skills.dump(snapshots), "input_files_json": run_inputs.dump(file_snapshots),
                    "tool_config_json": json.dumps(tool_config) if tool_config else None,
                    "profile_snapshot_json": json.dumps(profile_snapshot, ensure_ascii=False) if profile_snapshot else None,
                    "timeout": timeout, "status": "queued", "user_message_seq": user_seq, "created_at": now,
                })
                queued_event = self._add_event(run_id, "queued")
                db.update("conversations", {"updated_at": now}, "id=?", (conversation_id,))
        except sqlite3.IntegrityError:
            if db.one("SELECT id FROM conversations WHERE id=?", (conversation_id,)) is None:
                raise ConversationNotFound(conversation_id)
            existing = existing_request()
            if existing:
                return {"snap": existing}
            raise ActiveRunExists(conversation_id)
        snap = self.get_run(run_id)
        return {"schedule": True, "snap": snap, "event": queued_event, "run_id": run_id, "engine": engine,
                "model_id": model_id, "prompt": prompt_for_engine, "timeout": timeout, "variant": variant}

    # -- cancel --------------------------------------------------------------
    def _cancel_db(self, run_id: str):
        """Returns (won, prior, engine, event)."""
        run = self._db.one("SELECT * FROM conversation_runs WHERE id=?", (run_id,))
        if run is None:
            raise RunNotFound(run_id)
        prior, engine = run["status"], run["engine"]
        won, event = False, None
        if prior in ACTIVE_STATUSES:
            with self._db.tx():
                changed = self._db.update(
                    "conversation_runs",
                    {"status": "cancelled", "error": "Cancelled by user.", "finished_at": ts()},
                    "id=? AND status IN ('queued','running')", (run_id,))
                won = bool(changed)
                if won:
                    expire_approvals_for_run(self._db, run_id, "run_cancelled")
                    event = self._add_event(run_id, "cancelled", error="Cancelled by user.")
        return won, prior, engine, event

    async def cancel(self, run_id: str) -> Dict[str, Any]:
        """Persist cancellation first (a late result can never win), then abort
        the CLI process and cancel the asyncio task. Shielded so a caller
        cancel cannot skip the abort after the row is already cancelled."""
        return await asyncio.shield(asyncio.ensure_future(self._cancel_impl(run_id)))

    async def _cancel_impl(self, run_id: str) -> Dict[str, Any]:
        won, prior, engine, event = self._cancel_db(run_id)
        if event:
            self._publish([event])
        if won:
            session = self._tool_sessions.get(run_id)
            if session is not None:
                session.revoke()
            task = self._tasks.get(run_id)
            if prior == "running":
                try:
                    driver = self._run_drivers.get(run_id)
                    if driver is not None:
                        await asyncio.wait_for(driver.interrupt(run_id), timeout=self._abort_grace)
                    else:
                        rt = self._run_runtimes.get(run_id) or self._runtime(engine)
                        await asyncio.wait_for(rt.abort_task(self._run_keys.get(run_id, self._key(run_id))),
                                               timeout=self._abort_grace)
                except Exception as exc:  # best effort; task cancel below also kills the CLI
                    logger.warning("abort_task failed for run %s: %s", run_id, type(exc).__name__)
            if task is not None and not task.done():
                task.cancel()
                await asyncio.wait({task}, timeout=self._abort_grace)
        return self.get_run(run_id)

    async def shutdown(self) -> None:
        """Cancel in-flight tasks WITHOUT touching DB state: rows stay active
        and are marked interrupted on next start (or by ``interrupt_active``)."""
        tasks = list(self._tasks.values())
        for t in tasks:
            t.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()
        self._sem = None

    def interrupt_active(self) -> int:
        """Graceful shutdown: persist active runs as interrupted right now."""
        return self.recover_interrupted()

    # -- execution -----------------------------------------------------------
    def _semaphore(self) -> asyncio.Semaphore:
        if self._sem is None:
            self._sem = asyncio.Semaphore(self._max_concurrent)
        return self._sem

    async def _execute(self, run_id: str, engine: str, model_id: str, prompt: str, timeout: int,
                       variant: Optional[str] = None) -> None:
        try:
            async with self._semaphore():
                if not self._transition(run_id, "queued", "running", started_at=ts()):
                    return  # cancelled while waiting
                task = ExecuteAgentTask(prompt=prompt, model_id=model_id, variant=variant,
                                        provider_id=ledger_provider(engine), timeout=timeout,
                                        agent_id=settings.CHAT_AGENT_ID if engine == "opencode" else None,
                                        session_title=self._key(run_id))
                runtime = self._runtime(engine)
                driver = self._run_drivers[run_id] = ExecuteTaskDriver(runtime, engine, legacy_task=task)
                call_options = {"driver": driver, "emit": self._driver_event, "engine": engine,
                    "scope_kind": "conversation", "scope_id": self._db.scalar(
                        "SELECT conversation_id FROM conversation_runs WHERE id=?", (run_id,))}
                try:
                    config = self._db.scalar("SELECT tool_config_json FROM conversation_runs WHERE id=?", (run_id,))
                    if config:
                        from backend.pyworker.core.tool_session import ToolSession
                        async with ToolSession(self, run_id, json.loads(config)) as session:
                            task.mcp_servers = session.servers
                            result = await run_agent_task(self._runtime(engine), task,
                                                          kind=KIND_CONVERSATION_RUN, run_id=run_id, **call_options)
                            await session.verify_completion()
                    else:
                        result = await run_agent_task(self._runtime(engine), task,
                                                      kind=KIND_CONVERSATION_RUN, run_id=run_id, **call_options)
                except asyncio.CancelledError:
                    raise  # cancel() already persisted; shutdown leaves the row for recovery
                except Exception as exc:
                    from backend.pyworker.core.tool_session import ToolVerificationFailed
                    error = (str(exc) if isinstance(exc, ToolVerificationFailed)
                             else f"Engine request failed ({getattr(exc, 'code', 'runtime_error')}).")
                    self._transition(run_id, "running", "failed",
                                     error=error,
                                     finished_at=ts())
                    return
                profile_json = self._db.scalar("SELECT profile_snapshot_json FROM conversation_runs WHERE id=?", (run_id,))
                if profile_json:
                    profile = json.loads(profile_json)
                    if profile.get("output_schema_json"):
                        from backend.pyworker.engine.output_validation import validate_output
                        try:
                            validate_output(json.loads(result.text), json.loads(profile["output_schema_json"]))
                        except (ValueError, TypeError):
                            self._transition(run_id, "running", "failed", error="Output did not match the profile schema.",
                                             finished_at=ts())
                            return
                self._complete(run_id, conversation_text=result.text)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("conversation run %s crashed (%s)", run_id, type(exc).__name__)
            try:
                self._transition(run_id, "running", "failed", error="Internal error.", finished_at=ts())
            except Exception as exc2:
                logger.error("could not mark run %s failed (%s)", run_id, type(exc2).__name__)
        finally:
            self._run_drivers.pop(run_id, None)
            self._driver_terminals.pop(run_id, None)

    def _transition_db(self, run_id: str, from_status: str, to_status: str,
                       **values: Any) -> Optional[Dict[str, Any]]:
        with self._db.tx():
            changed = self._db.update("conversation_runs", {"status": to_status, **values},
                                      "id=? AND status=?", (run_id, from_status))
            if not changed:
                return None
            if to_status in TERMINAL_STATUSES:
                expire_approvals_for_run(self._db, run_id, f"run_{to_status}")
            return self._add_event(run_id, to_status, error=values.get("error"))

    def _transition(self, run_id: str, from_status: str, to_status: str, **values: Any) -> bool:
        event = self._transition_db(run_id, from_status, to_status, **values)
        if event is None:
            return False
        self._publish([event])
        return True

    def _complete_db(self, run_id: str, conversation_text: str) -> Optional[Dict[str, Any]]:
        """Win running->succeeded and write the assistant message atomically."""
        with self._db.tx():
            now = ts()
            changed = self._db.update("conversation_runs", {"status": "succeeded", "finished_at": now},
                                      "id=? AND status='running'", (run_id,))
            if not changed:
                return None
            run = self._db.one("SELECT * FROM conversation_runs WHERE id=?", (run_id,))
            cur = self._db.execute(
                "INSERT INTO conversation_messages(conversation_id,role,content,created_at) VALUES(?,?,?,?)",
                (run["conversation_id"], "assistant", conversation_text, now))
            seq = cur.lastrowid
            self._db.update("conversation_runs", {"assistant_message_seq": seq}, "id=?", (run_id,))
            self._db.update("conversations", {"updated_at": now}, "id=?", (run["conversation_id"],))
            expire_approvals_for_run(self._db, run_id, "run_succeeded")
            return self._add_event(run_id, "succeeded", assistant_message_seq=seq)

    def _complete(self, run_id: str, conversation_text: str) -> bool:
        event = self._complete_db(run_id, conversation_text)
        if event is None:
            return False
        self._publish([event])
        return True

    # -- mission worker attempts --------------------------------------------
    # A mission attempt is a first-class run (kind="mission_task", no
    # conversation): same table, status machine, events, cancel and recovery.
    # Start and success are conditional on the owning mission/task being live.
    _MISSION_ALIVE_SQL = (
        " AND mission_id IN (SELECT id FROM missions WHERE status IN ('queued','planning','running'))"
        " AND ((kind IN ('mission_planner','mission_synthesis') AND mission_task_id IS NULL)"
        "      OR mission_task_id IN (SELECT id FROM mission_tasks WHERE status IN ('queued','running')))"
    )

    def _prepare_mission_run(self, mission_id: str, task_id: Optional[str], attempt: int, engine: str,
                             model_id: str, variant: Optional[str], timeout: int,
                             kind: str = KIND_MISSION_TASK) -> Dict[str, Any]:
        if len(self._tasks) >= self._max_pending:
            raise TooManyRuns("Too many pending conversation runs.")
        run_id = new_id()
        with self._db.tx():
            self._db.insert("conversation_runs", {
                "id": run_id, "kind": kind, "conversation_id": None, "mission_id": mission_id,
                "mission_task_id": task_id, "attempt": attempt, "engine": engine, "model_id": model_id,
                "variant": variant, "timeout": timeout, "status": "queued", "created_at": ts(),
            })
            event = self._add_event(run_id, "queued", mission_id=mission_id,
                                    mission_task_id=task_id, attempt=attempt)
        return {"run_id": run_id, "event": event}

    def _mission_guarded_db(self, run_id: str, from_status: str, to_status: str,
                            **values: Any) -> Optional[Dict[str, Any]]:
        with self._db.tx():
            changed = self._db.update(
                "conversation_runs", {"status": to_status, **values},
                "id=? AND status=?" + self._MISSION_ALIVE_SQL, (run_id, from_status))
            if not changed:
                return None
            if to_status in TERMINAL_STATUSES:
                expire_approvals_for_run(self._db, run_id, f"run_{to_status}")
            return self._add_event(run_id, to_status, error=values.get("error"))

    def _mission_transition(self, run_id: str, from_status: str, to_status: str, **values: Any) -> bool:
        event = self._mission_guarded_db(run_id, from_status, to_status, **values)
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

        Returns (AgentResult, validated). Raises MissionRunCancelled if the run
        lost to a cancel, asyncio.TimeoutError on timeout, or the engine /
        validation exception (the run is then ``failed``). Cancelling the
        awaiting caller also cancels the run."""
        if kind in MISSION_STAGE_KINDS:
            task_id = None
        elif kind != KIND_MISSION_TASK or not task_id:
            raise ValueError("worker mission runs require KIND_MISSION_TASK and a task id")
        ledger_kind = kind if kind in MISSION_STAGE_KINDS else KIND_MISSION_DELEGATE

        prepared = self._prepare_mission_run(mission_id, task_id, attempt, engine, model_id, variant,
                                             timeout, kind)
        run_id = prepared["run_id"]
        self._publish([prepared["event"]])
        self._run_runtimes[run_id] = runtime
        self._run_keys[run_id] = session_title
        task = asyncio.get_running_loop().create_task(
            self._execute_mission_run(run_id, runtime, provider_id, model_id, variant, prompt,
                                      session_title, timeout, max_output_chars, validate, ledger_kind),
            name=self._key(run_id))
        self._tasks[run_id] = task
        task.add_done_callback(lambda _t, rid=run_id: self._forget_run(rid))
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
        self._run_drivers.pop(run_id, None)
        self._driver_terminals.pop(run_id, None)

    async def _execute_mission_run(self, run_id, runtime, provider_id, model_id, variant, prompt,
                                   session_title, timeout, max_output_chars, validate, ledger_kind) -> Any:
        async with self._semaphore():
            return await self._execute_mission_body(run_id, runtime, provider_id, model_id, variant, prompt,
                                                    session_title, timeout, max_output_chars, validate,
                                                    ledger_kind)

    async def _execute_mission_body(self, run_id, runtime, provider_id, model_id, variant, prompt,
                                    session_title, timeout, max_output_chars, validate,
                                    ledger_kind=KIND_MISSION_DELEGATE) -> Any:
        if not self._mission_transition(run_id, "queued", "running", started_at=ts()):
            self._cancel_run_if_active(run_id, "Mission cancelled.")
            raise MissionRunCancelled(run_id)
        req = ExecuteAgentTask(prompt=prompt, model_id=model_id, provider_id=provider_id, variant=variant,
                               session_title=session_title, timeout=timeout)
        driver = self._run_drivers[run_id] = ExecuteTaskDriver(runtime, self._db.scalar(
            "SELECT engine FROM conversation_runs WHERE id=?", (run_id,)), legacy_task=req)
        try:
            result = await asyncio.wait_for(
                run_agent_task(runtime, req, kind=ledger_kind, run_id=run_id, driver=driver,
                               emit=self._driver_event, scope_kind="mission_attempt", scope_id=run_id), timeout=timeout + 30)
            text = (result.text or "")[:max_output_chars]
            validated = validate(text) if validate else None
        except asyncio.CancelledError:
            raise  # cancel() already persisted; shutdown leaves the row for recovery
        except asyncio.TimeoutError:
            if not self._mission_transition(run_id, "running", "failed", error=f"Timed out after {timeout}s.",
                                            finished_at=ts()):
                raise MissionRunCancelled(run_id)
            raise
        except Exception as exc:
            if not self._mission_transition(run_id, "running", "failed", error=safe_error_message(exc)[:500],
                                            finished_at=ts()):
                raise MissionRunCancelled(run_id)
            raise
        event = self._mission_guarded_db(run_id, "running", "succeeded", finished_at=ts(), result_text=text)
        if event is None:
            self._cancel_run_if_active(run_id, "Mission cancelled.")
            raise MissionRunCancelled(run_id)
        self._publish([event])
        return result, validated

    def _cancel_run_if_active(self, run_id: str, error: str) -> None:
        """Force an active run that lost a mission guard to ``cancelled`` (idempotent)."""
        for from_status in ACTIVE_STATUSES:
            event = self._transition_db(run_id, from_status, "cancelled", error=error, finished_at=ts())
            if event is not None:
                self._publish([event])
                return

    async def cancel_mission_runs(self, mission_id: str) -> int:
        """Cancel every active run of a mission (persist first, then abort the CLI)."""
        run_ids = [r["id"] for r in self._db.all(
            "SELECT id FROM conversation_runs WHERE mission_id=? AND status IN ('queued','running')",
            (mission_id,))]
        for rid in run_ids:
            await self.cancel(rid)
        return len(run_ids)
