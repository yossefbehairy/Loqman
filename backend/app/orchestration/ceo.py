"""
CEO mission runner: real end-to-end orchestration.

Flow: persist (queued) -> planning (real model) -> dispatch tasks
(dependency levels, bounded concurrency) -> synthesis (real model).
Every state is persisted; failures are recorded, never faked.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import func, select, update

from backend.app.config import settings
from backend.app.core.conversation_runs import MissionRunCancelled, manager_for_factory
from backend.app.db.models import Mission, MissionTask
from backend.app.db.repository import AgentProfileRepository, MissionRepository
from backend.app.engine.events import event_hub
from backend.app.orchestration.planner import (
    build_planning_prompt,
    build_synthesis_prompt,
    parse_and_validate_plan,
)
from backend.app.runtime.base import ExecuteAgentTask
from backend.app.runtime.errors import safe_error_message
from backend.app.runtime.ledger import (
    run_agent_task,
    KIND_MISSION_DELEGATE,
    KIND_MISSION_PLANNER,
    KIND_MISSION_SYNTHESIS,
)

logger = logging.getLogger("agentos.missions")


def _reject_non_json_constant(value: str) -> None:
    raise ValueError(f"Invalid JSON constant: {value}")

_mission_cancel: Dict[str, bool] = {}
# mission_id -> (run manager, event loop) owning its worker runs, for cancel/abort.
_mission_managers: Dict[str, Any] = {}


def cancel_mission(mission_id: str) -> None:
    _mission_cancel[mission_id] = True
    # Mission rows are already cancelled by the caller (late run successes are
    # refused by the DB guard); also cancel + abort the active worker runs on the
    # loop that owns them. Safe from any thread; no-op if the mission isn't running here.
    owner = _mission_managers.get(mission_id)
    if owner is not None:
        manager, loop = owner
        try:
            loop.call_soon_threadsafe(
                lambda: loop.create_task(_abort_quietly(manager, mission_id)))
        except RuntimeError:  # loop closed
            pass


async def _abort_quietly(manager, mission_id: str) -> None:
    try:
        await manager.cancel_mission_runs(mission_id)
    except Exception as exc:
        logger.warning("abort of mission %s runs failed: %s", mission_id, type(exc).__name__)


def is_mission_cancelled(mission_id: str) -> bool:
    return _mission_cancel.get(mission_id, False)


def mission_was_cancelled(db, mission_id: str) -> bool:
    """True if cancellation was requested in-process OR persisted in the DB.

    Must be called after every await on a runtime: a late provider response
    must never overwrite a terminal cancelled state. Reads fresh DB state
    because the cancel endpoint commits from a different session.
    """
    if is_mission_cancelled(mission_id):
        return True
    db.expire_all()
    row = db.query(Mission.status).filter(Mission.id == mission_id).first()
    return bool(row and row[0] == "cancelled")


def _clear_cancel(mission_id: str) -> None:
    _mission_cancel.pop(mission_id, None)


def _parse_worker_json(text: str) -> Any:
    """Strict JSON validator for worker output (runs inside the persisted run)."""
    try:
        return json.loads(text, parse_constant=_reject_non_json_constant)
    except (json.JSONDecodeError, ValueError) as exc:
        raise ValueError("Mission task returned invalid JSON") from exc




# ---------------------------------------------------------------------------
# Atomic, conditional state transitions.
#
# A check-then-write across DB sessions races: a cancel can commit between the
# check and the write. Every transition below is a single UPDATE whose WHERE
# clause carries the expected current state (compare-and-set), so the database
# decides the winner. A committed "cancelled" can never be overwritten, and a
# task of a cancelled mission can never start/finish/fail.
# ---------------------------------------------------------------------------
MISSION_NON_TERMINAL = ("queued", "planning", "running")
TASK_NON_TERMINAL = ("queued", "running")


def transition_mission(db, mission_id: str, from_states, *, commit: bool = True, **values) -> bool:
    """Atomically set `values` iff the mission is currently in `from_states`."""
    res = db.execute(
        update(Mission)
        .where(Mission.id == mission_id, Mission.status.in_(tuple(from_states)))
        .values(**values)
        .execution_options(synchronize_session=False)
    )
    if commit:
        db.commit()
    db.expire_all()
    return res.rowcount == 1


def transition_task(db, task_id: str, from_states, *, commit: bool = True,
                    guard_mission: bool = True, **values) -> bool:
    """Atomically set `values` iff the task is in `from_states`.

    With `guard_mission` (default) the write is also refused when the owning
    mission is already cancelled, so cancel always wins over start/finish/fail.
    """
    stmt = update(MissionTask).where(
        MissionTask.id == task_id, MissionTask.status.in_(tuple(from_states))
    )
    if guard_mission:
        stmt = stmt.where(
            MissionTask.mission_id.in_(select(Mission.id).where(Mission.status != "cancelled"))
        )
    res = db.execute(stmt.values(**values).execution_options(synchronize_session=False))
    if commit:
        db.commit()
    db.expire_all()
    return res.rowcount == 1


def cancel_mission_rows(db, mission_id: str) -> bool:
    """Atomically cancel a non-terminal mission and its unfinished tasks.

    Both UPDATEs commit in one transaction. Returns True iff this call moved
    the mission to cancelled (False: unknown or already terminal).
    """
    now = datetime.now(timezone.utc)
    try:
        won = transition_mission(
            db, mission_id, MISSION_NON_TERMINAL, commit=False,
            status="cancelled", finished_at=now,
        )
        if won:
            db.execute(
                update(MissionTask)
                .where(MissionTask.mission_id == mission_id, MissionTask.status.in_(TASK_NON_TERMINAL))
                .values(status="cancelled", finished_at=func.coalesce(MissionTask.finished_at, now))
                .execution_options(synchronize_session=False)
            )
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.expire_all()
    return won


def idempotency_key(objective: str, roles: List[str], model_id: str) -> str:
    h = hashlib.sha256(f"{objective.strip()}|{','.join(sorted(roles))}|{model_id}".encode()).hexdigest()
    return h[:48]


async def _broadcast(mission_id: str, event_type: str, data: Dict[str, Any]) -> None:
    payload = dict(data)
    payload.setdefault("missionId", mission_id)
    await event_hub.broadcast(mission_id, event_type, payload)


def _levels(tasks: List[MissionTask]) -> List[List[MissionTask]]:
    """Topological levels for bounded-concurrency execution."""
    by_key = {t.task_key: t for t in tasks}
    indeg = {t.task_key: 0 for t in tasks}
    children: Dict[str, List[str]] = {t.task_key: [] for t in tasks}
    for t in tasks:
        try:
            deps = json.loads(t.depends_on_json or "[]")
        except Exception:
            deps = []
        for d in deps:
            if d in by_key:
                indeg[t.task_key] += 1
                children[d].append(t.task_key)
    levels: List[List[MissionTask]] = []
    ready = [k for k, d in indeg.items() if d == 0]
    seen = set()
    while ready:
        levels.append([by_key[k] for k in ready])
        nxt: List[str] = []
        for k in ready:
            seen.add(k)
            for c in children[k]:
                indeg[c] -= 1
                if indeg[c] == 0:
                    nxt.append(c)
        ready = nxt
    # Any leftover (cycle shouldn't happen post-validation) runs last.
    leftover = [t for t in tasks if t.task_key not in seen]
    if leftover:
        levels.append(leftover)
    return levels


async def _finalize_task_cancelled(db, mission_id: str, t: MissionTask, *, run_cancelled: bool = False) -> None:
    """Only record cancellation when it was actually requested, not on any lost CAS."""
    task_id, task_key = t.id, t.task_key
    cancelled = run_cancelled or mission_was_cancelled(db, mission_id)
    db.expire_all()
    current = db.get(MissionTask, task_id)
    if current is None:
        return
    if cancelled:
        transition_task(
            db, task_id, TASK_NON_TERMINAL, guard_mission=False, status="cancelled",
            finished_at=datetime.now(timezone.utc), error_message="Cancelled by user.",
        )
        db.expire_all()
        current = db.get(MissionTask, task_id)
    if current:
        await _broadcast(mission_id, "task.status.changed", {"taskKey": task_key, "status": current.status})


async def _finalize_mission_cancelled(db, mission_id: str) -> Dict[str, Any]:
    """Force mission + unfinished tasks to cancelled; never store late output."""
    cancel_mission_rows(db, mission_id)
    await _broadcast(mission_id, "mission.status.changed", {"status": "cancelled"})
    return {"status": "cancelled"}


_ENGINE_PROVIDERS = {"opencode": "opencode", "claude-code": "anthropic", "codex": "openai", "antigravity": "antigravity", "mock": "mock"}


def _default_runtime_identity() -> tuple[str, str]:
    """Return the configured default engine and provider labels for ledger/runs."""
    return {
        "real": ("opencode", "opencode"),
        "mock": ("mock", "mock"),
        "claude": ("claude-code", "anthropic"),
        "codex": ("codex", "openai"),
        # RUNTIME_MODE=agy still selects the separate experimental print adapter;
        # the canonical Antigravity ACP adapter is selected explicitly by engine id.
        "agy": ("agy-legacy-cli", "antigravity"),
    }.get(settings.RUNTIME_MODE, ("opencode", "opencode"))


class EngineSelectionError(Exception):
    """A profile's engine preference cannot be honoured. Never silently falls back."""


def _default_engine_runtime_factory(engine_id: str):
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
    raise EngineSelectionError(f"Unsupported engine '{engine_id}'.")


async def _select_task_runtime(profile, default_runtime, factory, cache, model_id, variant):
    """Return (runtime, provider_id, engine_label) for one worker task.

    No profile engine -> the server default runtime with provider "opencode"
    (exactly the historical behaviour). An explicit engine gets its own
    adapter; for non-OpenCode engines the model/variant are checked against
    that adapter's catalog (static listing, no model call). Those catalogs are
    unverified, so this never asserts a model is free. OpenCode free-only
    enforcement stays inside the OpenCode adapter's execute path.
    """
    engine = ((getattr(profile, "engine", None) if profile else None) or "").strip() or None
    if engine is None:
        return default_runtime, _default_runtime_identity()[1], None
    if engine not in _ENGINE_PROVIDERS:
        raise EngineSelectionError(f"Unsupported engine '{engine}'.")
    if engine == "mock":
        if settings.RUNTIME_MODE != "mock":
            raise EngineSelectionError("Engine 'mock' is only available in explicit mock mode.")
        return default_runtime, _ENGINE_PROVIDERS[engine], engine
    rt = cache.get(engine)
    if rt is None:
        try:
            rt = cache[engine] = factory(engine)
        except EngineSelectionError:
            raise
        except Exception as exc:
            raise EngineSelectionError(f"Engine '{engine}' is unavailable: {safe_error_message(exc)}") from exc
    if engine != "opencode":
        try:
            catalog = await rt.list_available_models(free_only=False)
        except Exception as exc:
            raise EngineSelectionError(f"Engine '{engine}' model catalog failed: {safe_error_message(exc)}") from exc
        entry = next((m for m in catalog if m.id == model_id), None)
        if entry is None:
            raise EngineSelectionError(
                f"Model '{model_id}' is not offered by engine '{engine}'; set an engine-specific model on the profile."
            )
        if variant not in (None, "", "default") and variant not in (entry.variants or []):
            raise EngineSelectionError(f"Variant '{variant}' is not supported by engine '{engine}' for model '{model_id}'.")
    return rt, _ENGINE_PROVIDERS[engine], engine


async def _execute_single_task(
    db_factory,
    runtime,
    mission: Mission,
    task: MissionTask,
    dep_outputs: Dict[str, Any],
    sem: asyncio.Semaphore,
    engine_runtime_factory=None,
    engine_cache: Optional[Dict[str, Any]] = None,
    run_manager=None,
) -> None:
    from backend.app.db.session import SessionLocal as _SL  # local to avoid cycles

    manager = run_manager or manager_for_factory(db_factory)
    # Run-row label for the server-default runtime (profile has no engine).
    default_engine_label, _ = _default_runtime_identity()

    factory = db_factory or _SL
    db = factory()
    try:
        t = db.query(MissionTask).filter(MissionTask.id == task.id).first()
        if not t:
            return
        # Dependency gate: skip if any dependency did not succeed.
        try:
            deps = json.loads(t.depends_on_json or "[]")
        except Exception:
            deps = []
        dep_failed = False
        for d in deps:
            dep_task = db.query(MissionTask).filter(
                MissionTask.mission_id == mission.id, MissionTask.task_key == d
            ).first()
            if not dep_task or dep_task.status != "succeeded":
                dep_failed = True
                break
        task_id, task_key = t.id, t.task_key
        if dep_failed or mission_was_cancelled(db, mission.id):
            cancelled = mission_was_cancelled(db, mission.id)
            new_status = "cancelled" if cancelled else "skipped"
            applied = transition_task(
                db, task_id, ("queued",), guard_mission=not cancelled, status=new_status,
                finished_at=datetime.now(timezone.utc),
                error_message="Skipped: upstream dependency did not succeed." if not cancelled else "Cancelled by user.",
            )
            if not applied and not cancelled:
                # Mission got cancelled between the check and the write.
                await _finalize_task_cancelled(db, mission.id, t)
                return
            await _broadcast(mission.id, "task.status.changed", {"taskKey": task_key, "status": new_status})
            return

        # Resolve employee config (role instructions + model).
        profiles = AgentProfileRepository(db)
        profile = None
        if t.agent_profile_id:
            profile = profiles.get_by_id(t.agent_profile_id)
        if not profile:
            # Fallback: first profile matching role_type.
            for p in profiles.list_all():
                if (p.role_type or "").lower() == (t.employee_role or "").lower():
                    profile = p
                    break
        instructions = (profile.instructions if profile else t.instructions) or t.instructions
        model_id = t.model_id or (profile.default_model_id if profile else None) or settings.DEFAULT_MODEL_ID
        variant = t.variant or (profile.default_variant_id if profile else None)

        context_parts = [t.input_context or ""]
        for d in deps:
            out = dep_outputs.get(d)
            if out is not None:
                excerpt = json.dumps(out, ensure_ascii=False)[:2000]
                context_parts.append(f"[Dependency {d} output]: {excerpt}")
        full_prompt = (
            f"You are the '{t.employee_role}' employee. {instructions}\n"
            f"Task: {t.title}\nDetails: {t.instructions}\n"
            f"Mission context: {(t.input_context or '')[:4000]}\n"
            + ("\n".join(context_parts[1:]) if len(context_parts) > 1 else "")
            + "\nReturn STRICT JSON only matching the expected output. No markdown fences."
        )[: settings.MISSION_MAX_PROMPT_CHARS]

        try:
            task_runtime, task_provider, task_engine = await _select_task_runtime(
                profile, runtime, engine_runtime_factory or _default_engine_runtime_factory,
                engine_cache if engine_cache is not None else {}, model_id, variant,
            )
        except EngineSelectionError as exc:
            # Honest, non-retried failure: the preference cannot be honoured.
            msg = f"Engine selection failed: {str(exc)[:300]}"  # text built above; wrapped causes already sanitized
            if not transition_task(db, task_id, ("queued", "running"), status="failed",
                                   error_message=msg, finished_at=datetime.now(timezone.utc)):
                await _finalize_task_cancelled(db, mission.id, t)
                return
            await _broadcast(mission.id, "task.status.changed", {"taskKey": task_key, "status": "failed"})
            return

        async with sem:
            for attempt in range(1, settings.MISSION_MAX_RETRIES + 2):
                start_values: Dict[str, Any] = {"status": "running", "attempt_count": attempt}
                if attempt == 1:
                    start_values["started_at"] = datetime.now(timezone.utc)
                # Atomic start: refused if the mission was cancelled (even from
                # another session/process) or the task already left queued/running.
                if is_mission_cancelled(mission.id) or not transition_task(
                    db, task_id, ("queued", "running"), **start_values
                ):
                    await _finalize_task_cancelled(db, mission.id, t)
                    return
                await _broadcast(mission.id, "task.status.changed", {"taskKey": task_key, "status": "running", "attempt": attempt})
                try:
                    # Every attempt is a persisted run (state, events, cancel) on the
                    # engine selected above. Invalid structured output fails the run
                    # (-> retry/failure), it never becomes a succeeded run.
                    res, parsed = await manager.run_mission_task(
                        runtime=task_runtime, mission_id=mission.id, task_id=task_id, attempt=attempt,
                        engine=task_engine or default_engine_label, provider_id=task_provider,
                        model_id=model_id, variant=variant, prompt=full_prompt,
                        session_title=f"mission-{mission.id[:8]}-{t.task_key}",
                        timeout=settings.MISSION_TASK_TIMEOUT,
                        max_output_chars=settings.MISSION_MAX_OUTPUT_CHARS,
                        validate=_parse_worker_json,
                    )
                    if mission_was_cancelled(db, mission.id):
                        await _finalize_task_cancelled(db, mission.id, t)
                        return
                    output_json = json.dumps(
                        {"result": parsed, "model": res.model_id, "variant": res.variant_id,
                         "session_id": res.session_id, "duration_ms": res.duration_ms,
                         **({"engine": task_engine} if task_engine else {})},
                        ensure_ascii=False,
                    )
                    # Atomic CAS: only a still-running task of a non-cancelled
                    # mission may succeed; a committed cancel always wins.
                    # model_id: keep the *recorded* model.
                    if not transition_task(
                        db, task_id, ("running",),
                        output_json=output_json, session_id=res.session_id,
                        duration_ms=res.duration_ms, model_id=res.model_id,
                        status="succeeded", finished_at=datetime.now(timezone.utc),
                    ):
                        await _finalize_task_cancelled(db, mission.id, t)
                        return
                    dep_outputs[task_key] = parsed
                    await _broadcast(mission.id, "task.status.changed",
                                     {"taskKey": t.task_key, "status": "succeeded", "duration_ms": res.duration_ms})
                    return
                except MissionRunCancelled:
                    await _finalize_task_cancelled(db, mission.id, t, run_cancelled=True)
                    return
                except asyncio.TimeoutError:
                    if mission_was_cancelled(db, mission.id):
                        await _finalize_task_cancelled(db, mission.id, t)
                        return
                    last_error = f"Task timed out after {settings.MISSION_TASK_TIMEOUT}s (attempt {attempt})."
                except Exception as e:
                    if mission_was_cancelled(db, mission.id):
                        await _finalize_task_cancelled(db, mission.id, t)
                        return
                    last_error = safe_error_message(e)
                    # Normalize transient vs permanent for logging clarity.
                    logger.warning("Mission %s task %s attempt %d failed: %s", mission.id, t.task_key, attempt, last_error[:200])
                if not transition_task(db, task_id, ("running",), error_message=last_error):
                    await _finalize_task_cancelled(db, mission.id, t)
                    return
                await _broadcast(mission.id, "task.attempt.failed",
                                 {"taskKey": task_key, "attempt": attempt, "error": last_error[:300]})
            # Exhausted retries: only a still-running task of a live mission may fail.
            if not transition_task(db, task_id, ("running",), status="failed",
                                   finished_at=datetime.now(timezone.utc)):
                await _finalize_task_cancelled(db, mission.id, t)
                return
            await _broadcast(mission.id, "task.status.changed", {"taskKey": task_key, "status": "failed"})
    finally:
        db.close()


async def run_mission_background(mission_id: str, db_factory=None, runtime=None,
                                 engine_runtime_factory=None, run_manager=None) -> Dict[str, Any]:
    """Background worker: planning -> dispatch -> synthesis. Never fabricates success.

    Worker attempts, planning and synthesis all run through the persisted run
    manager (kinds mission_task / mission_planner / mission_synthesis)."""
    from backend.app.db.session import SessionLocal as _SL
    from backend.app.runtime import get_runtime as _get_runtime

    factory = db_factory or _SL
    rt = runtime or _get_runtime()
    manager = run_manager or manager_for_factory(db_factory)
    _mission_managers[mission_id] = (manager, asyncio.get_running_loop())
    stage_engine, stage_provider = _default_runtime_identity()
    db = factory()
    try:
        repo = MissionRepository(db)
        mission = repo.get(mission_id)
        if not mission:
            return {"status": "failed", "error": "mission not found"}
        if mission.status not in ("queued",):
            return {"status": mission.status}

        # Atomic claim: only one runner, and never over a cancel that just landed.
        if not transition_mission(db, mission_id, ("queued",), status="planning",
                                  started_at=datetime.now(timezone.utc)):
            row = db.query(Mission.status).filter(Mission.id == mission_id).first()
            return {"status": row[0] if row else "failed"}
        await _broadcast(mission_id, "mission.status.changed", {"status": "planning"})

        # ---- Step B: CEO planning (real model) ----
        try:
            profiles = AgentProfileRepository(db)
            roles = sorted({(p.role_type or "").lower() for p in profiles.list_all() if p.role_type})
            plan_prompt = (
                __import__("backend.app.orchestration.planner", fromlist=["build_planning_prompt"])
                .build_planning_prompt(mission.objective, roles)
            )
            plan_model = mission.model_id or settings.DEFAULT_MODEL_ID
            plan_req = ExecuteAgentTask(
                prompt=plan_prompt[: settings.MISSION_MAX_PROMPT_CHARS],
                model_id=plan_model,
                provider_id=stage_provider,
                variant=mission.variant,
                session_title=f"mission-{mission.id[:8]}-planning",
                timeout=settings.MISSION_TASK_TIMEOUT,
            )
            from backend.app.orchestration.planner import parse_and_validate_plan as _parse

            objective = mission.objective
            # Persisted stage run: the output is validated BEFORE the run may become
            # succeeded; a parse/validation error fails the run (and the mission).
            _plan_res, plan = await manager.run_mission_task(
                runtime=rt, mission_id=mission_id, task_id=None, attempt=1,
                engine=stage_engine, provider_id=plan_req.provider_id,
                model_id=plan_req.model_id, variant=plan_req.variant, prompt=plan_req.prompt,
                session_title=plan_req.session_title, timeout=plan_req.timeout,
                max_output_chars=settings.MISSION_MAX_OUTPUT_CHARS,
                validate=lambda text: _parse(text, objective), kind=KIND_MISSION_PLANNER,
            )
            if mission_was_cancelled(db, mission_id):
                return await _finalize_mission_cancelled(db, mission_id)
        except Exception as e:
            if mission_was_cancelled(db, mission_id):
                return await _finalize_mission_cancelled(db, mission_id)
            err =f"Planning failed: {safe_error_message(e)}"
            if not transition_mission(db, mission_id, ("planning", "running"), status="failed",
                                      error_summary=err, finished_at=datetime.now(timezone.utc)):
                if mission_was_cancelled(db, mission_id):
                    return await _finalize_mission_cancelled(db, mission_id)
                row = db.query(Mission.status).filter(Mission.id == mission_id).first()
                return {"status": row[0] if row else "failed"}
            await _broadcast(mission_id, "mission.status.changed", {"status": "failed", "error": err[:300]})
            return {"status": "failed", "error": err}

        plan_json = json.dumps({"objective": plan.objective, "tasks": [t.model_dump() for t in plan.tasks]}, ensure_ascii=False)
        # Atomic: planning -> running only if no cancel landed meanwhile.
        if not transition_mission(db, mission_id, ("planning",), plan_json=plan_json, status="running"):
            if mission_was_cancelled(db, mission_id):
                return await _finalize_mission_cancelled(db, mission_id)
            row = db.query(Mission.status).filter(Mission.id == mission_id).first()
            return {"status": row[0] if row else "failed"}
        await _broadcast(mission.id, "mission.status.changed", {"status": "running", "tasks": len(plan.tasks)})

        # ---- Step C/D: persist tasks + dispatch with dependencies ----
        profiles = AgentProfileRepository(db)
        by_role_profile: Dict[str, Any] = {}
        for p in profiles.list_all():
            by_role_profile.setdefault((p.role_type or "").lower(), p)
        created: List[MissionTask] = []
        for pt in plan.tasks:
            prof = by_role_profile.get(pt.employee_role)
            created.append(repo.create_task(
                mission_id=mission.id,
                task_key=pt.key,
                employee_role=pt.employee_role,
                title=pt.title,
                instructions=pt.instructions,
                input_context=mission.objective,
                depends_on=pt.depends_on,
                agent_profile_id=prof.id if prof else None,
                model_id=(prof.default_model_id if prof else None) or mission.model_id or settings.DEFAULT_MODEL_ID,
                variant=(prof.default_variant_id if prof else None) or mission.variant,
            ))
        await _broadcast(mission.id, "mission.tasks.created", {"tasks": [c.task_key for c in created]})

        sem = asyncio.Semaphore(max(1, settings.MISSION_MAX_CONCURRENCY))
        dep_outputs: Dict[str, Any] = {}
        engine_cache: Dict[str, Any] = {}
        for level in _levels(created):
            if is_mission_cancelled(mission.id):
                break
            await asyncio.gather(*[
                _execute_single_task(factory, rt, mission, t, dep_outputs, sem,
                                     engine_runtime_factory, engine_cache, manager)
                for t in level
            ])

        # Refresh statuses.
        db.expire_all()
        mission = repo.get(mission_id)
        tasks = db.query(MissionTask).filter(MissionTask.mission_id == mission_id).all()
        if mission_was_cancelled(db, mission.id):
            return await _finalize_mission_cancelled(db, mission_id)

        ok = [t for t in tasks if t.status == "succeeded"]
        bad = [t for t in tasks if t.status in ("failed", "skipped", "cancelled")]

        # ---- Step F: CEO synthesis (real model, best-effort) ----
        summary_text = ""
        try:
            results = []
            for t in tasks:
                try:
                    out = json.loads(t.output_json or "{}")
                    excerpt = json.dumps(out.get("result", out), ensure_ascii=False)[:1500]
                except Exception:
                    excerpt = (t.output_json or "")[:1500]
                results.append({"task_key": t.task_key, "title": t.title, "employee_role": t.employee_role,
                                "status": t.status, "output_excerpt": excerpt})
            from backend.app.orchestration.planner import build_synthesis_prompt as _syn

            syn_req = ExecuteAgentTask(
                prompt=_syn(mission.objective, results)[: settings.MISSION_MAX_PROMPT_CHARS],
                model_id=mission.model_id or settings.DEFAULT_MODEL_ID,
                provider_id=stage_provider,
                variant=mission.variant,
                session_title=f"mission-{mission.id[:8]}-synthesis",
                timeout=settings.MISSION_TASK_TIMEOUT,
            )
            syn_res, _ = await manager.run_mission_task(
                runtime=rt, mission_id=mission_id, task_id=None, attempt=1,
                engine=stage_engine, provider_id=syn_req.provider_id,
                model_id=syn_req.model_id, variant=syn_req.variant, prompt=syn_req.prompt,
                session_title=syn_req.session_title, timeout=syn_req.timeout,
                max_output_chars=settings.MISSION_MAX_OUTPUT_CHARS, kind=KIND_MISSION_SYNTHESIS,
            )
            if mission_was_cancelled(db, mission_id):
                return await _finalize_mission_cancelled(db, mission_id)
            summary_text = (syn_res.text or "")[: settings.MISSION_MAX_OUTPUT_CHARS]
        except Exception as e:
            if mission_was_cancelled(db, mission_id):
                return await _finalize_mission_cancelled(db, mission_id)
            summary_text = json.dumps({"summary": f"Synthesis unavailable: {safe_error_message(e)}",
                                       "completed": [t.task_key for t in ok],
                                       "failed": [t.task_key for t in bad],
                                       "unverified": [t.task_key for t in ok]})

        final_values: Dict[str, Any] = {"final_summary": summary_text,
                                        "finished_at": datetime.now(timezone.utc)}
        if ok and not bad:
            final_status = "succeeded"
        elif ok and bad:
            final_status = "partially_succeeded"
        else:
            final_status = "failed"
            if not mission.error_summary:
                final_values["error_summary"] = "; ".join([(t.error_message or t.task_key) for t in bad])[:1000]
        # Atomic: terminal result only from a live (running) mission, so a
        # committed cancel can never be overwritten by completion/failure.
        if not transition_mission(db, mission_id, ("planning", "running"), status=final_status, **final_values):
            if mission_was_cancelled(db, mission_id):
                return await _finalize_mission_cancelled(db, mission_id)
            row = db.query(Mission.status).filter(Mission.id == mission_id).first()
            return {"status": row[0] if row else "failed"}
        await _broadcast(mission_id, "mission.status.changed",
                         {"status": final_status, "succeeded": len(ok), "failed": len(bad)})
        return {"status": final_status, "succeeded": len(ok), "failed": len(bad)}
    finally:
        _clear_cancel(mission_id)
        _mission_managers.pop(mission_id, None)
        db.close()
