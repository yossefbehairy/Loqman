"""CEO mission runner: real end-to-end orchestration (stdlib port).

Flow: persist (queued) -> planning (real model) -> dispatch tasks (dependency
levels, bounded concurrency) -> synthesis (real model). Every state is
persisted; failures are recorded, never faked.

Every transition below is a single UPDATE whose WHERE clause carries the
expected current state (compare-and-set), so the database decides the winner:
a committed "cancelled" can never be overwritten and a task of a cancelled
mission can never start/finish/fail.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from typing import Any, Dict, List, Optional

from backend.pyworker.appsettings import settings
from backend.pyworker.core import missions as mission_repo
from backend.pyworker.core import profiles as profile_repo
from backend.pyworker.core.conversation_runs import MissionRunCancelled
from backend.pyworker.db.core import Database, ts
from backend.pyworker.engine.events import event_hub
from backend.pyworker.orchestration.planner import (
    build_planning_prompt,
    build_synthesis_prompt,
    parse_and_validate_plan,
)
from backend.pyworker.runtime.base import ExecuteAgentTask
from backend.pyworker.runtime.errors import safe_error_message
from backend.pyworker.runtime.ledger import KIND_MISSION_PLANNER, KIND_MISSION_SYNTHESIS

logger = logging.getLogger("agentos.missions")


def _reject_non_json_constant(value: str) -> None:
    raise ValueError(f"Invalid JSON constant: {value}")


_mission_cancel: Dict[str, bool] = {}
# mission_id -> run manager owning its worker runs (for cancel/abort).
_mission_managers: Dict[str, Any] = {}

MISSION_NON_TERMINAL = ("queued", "planning", "running")
TASK_NON_TERMINAL = ("queued", "running")


def cancel_mission(mission_id: str) -> None:
    _mission_cancel[mission_id] = True
    manager = _mission_managers.get(mission_id)
    if manager is not None:
        try:
            asyncio.get_running_loop().create_task(_abort_quietly(manager, mission_id))
        except RuntimeError:
            pass  # no running loop


async def _abort_quietly(manager, mission_id: str) -> None:
    try:
        await manager.cancel_mission_runs(mission_id)
    except Exception as exc:
        logger.warning("abort of mission %s runs failed: %s", mission_id, type(exc).__name__)


def is_mission_cancelled(mission_id: str) -> bool:
    return _mission_cancel.get(mission_id, False)


def mission_was_cancelled(db: Database, mission_id: str) -> bool:
    """True if cancellation was requested in-process OR persisted in the DB.
    Must be called after every await on a runtime: a late provider response
    must never overwrite a terminal cancelled state."""
    if is_mission_cancelled(mission_id):
        return True
    return db.scalar("SELECT status FROM missions WHERE id=?", (mission_id,)) == "cancelled"


def _clear_cancel(mission_id: str) -> None:
    _mission_cancel.pop(mission_id, None)


def _parse_worker_json(text: str) -> Any:
    """Strict JSON validator for worker output (runs inside the persisted run)."""
    try:
        return json.loads(text, parse_constant=_reject_non_json_constant)
    except (json.JSONDecodeError, ValueError) as exc:
        raise ValueError("Mission task returned invalid JSON") from exc


def _marks(values) -> str:
    return ",".join("?" for _ in values)


def transition_mission(db: Database, mission_id: str, from_states, **values) -> bool:
    """Atomically set ``values`` iff the mission is currently in ``from_states``."""
    states = tuple(from_states)
    values = {**values, "updated_at": ts()}
    return db.update("missions", values, f"id=? AND status IN ({_marks(states)})", (mission_id, *states)) == 1


def transition_task(db: Database, task_id: str, from_states, *, guard_mission: bool = True, **values) -> bool:
    """Atomically set ``values`` iff the task is in ``from_states``; with
    ``guard_mission`` also refused when the owning mission is cancelled."""
    states = tuple(from_states)
    where = f"id=? AND status IN ({_marks(states)})"
    if guard_mission:
        where += " AND mission_id IN (SELECT id FROM missions WHERE status != 'cancelled')"
    return db.update("mission_tasks", values, where, (task_id, *states)) == 1


def cancel_mission_rows(db: Database, mission_id: str) -> bool:
    """Atomically cancel a non-terminal mission and its unfinished tasks in one
    transaction. True iff this call moved the mission to cancelled."""
    now = ts()
    with db.tx():
        won = transition_mission(db, mission_id, MISSION_NON_TERMINAL, status="cancelled", finished_at=now)
        if won:
            db.execute(
                "UPDATE mission_tasks SET status='cancelled', finished_at=COALESCE(finished_at, ?) "
                f"WHERE mission_id=? AND status IN ({_marks(TASK_NON_TERMINAL)})",
                (now, mission_id, *TASK_NON_TERMINAL))
    return won


def idempotency_key(objective: str, roles: List[str], model_id: str) -> str:
    h = hashlib.sha256(f"{objective.strip()}|{','.join(sorted(roles))}|{model_id}".encode()).hexdigest()
    return h[:48]


async def _broadcast(mission_id: str, event_type: str, data: Dict[str, Any]) -> None:
    payload = dict(data)
    payload.setdefault("missionId", mission_id)
    await event_hub.broadcast(mission_id, event_type, payload)


def _levels(tasks: List[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
    """Topological levels for bounded-concurrency execution."""
    by_key = {t["task_key"]: t for t in tasks}
    indeg = {t["task_key"]: 0 for t in tasks}
    children: Dict[str, List[str]] = {t["task_key"]: [] for t in tasks}
    for t in tasks:
        try:
            deps = json.loads(t["depends_on_json"] or "[]")
        except Exception:
            deps = []
        for d in deps:
            if d in by_key:
                indeg[t["task_key"]] += 1
                children[d].append(t["task_key"])
    levels: List[List[Dict[str, Any]]] = []
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
    leftover = [t for t in tasks if t["task_key"] not in seen]
    if leftover:
        levels.append(leftover)
    return levels


async def _finalize_task_cancelled(db: Database, mission_id: str, t: Dict[str, Any], *,
                                   run_cancelled: bool = False) -> None:
    """Only record cancellation when it was actually requested, not on any lost CAS."""
    task_id, task_key = t["id"], t["task_key"]
    cancelled = run_cancelled or mission_was_cancelled(db, mission_id)
    current = db.one("SELECT * FROM mission_tasks WHERE id=?", (task_id,))
    if current is None:
        return
    if cancelled:
        transition_task(db, task_id, TASK_NON_TERMINAL, guard_mission=False, status="cancelled",
                        finished_at=ts(), error_message="Cancelled by user.")
        current = db.one("SELECT * FROM mission_tasks WHERE id=?", (task_id,))
    if current:
        await _broadcast(mission_id, "task.status.changed", {"taskKey": task_key, "status": current["status"]})


async def _finalize_mission_cancelled(db: Database, mission_id: str) -> Dict[str, Any]:
    """Force mission + unfinished tasks to cancelled; never store late output."""
    cancel_mission_rows(db, mission_id)
    await _broadcast(mission_id, "mission.status.changed", {"status": "cancelled"})
    return {"status": "cancelled"}


ENGINE_PROVIDERS = {"opencode": "opencode", "claude-code": "anthropic", "codex": "openai",
                    "antigravity": "antigravity", "mock": "mock"}
_ENGINE_PROVIDERS = ENGINE_PROVIDERS  # legacy name


def _default_runtime_identity() -> tuple:
    """The configured default engine and provider labels for ledger/runs."""
    return {
        "real": ("opencode", "opencode"),
        "mock": ("mock", "mock"),
        "claude": ("claude-code", "anthropic"),
        "codex": ("codex", "openai"),
        # RUNTIME_MODE=agy selects the separate experimental print adapter; the
        # canonical Antigravity ACP adapter is selected explicitly by engine id.
        "agy": ("agy-legacy-cli", "antigravity"),
    }.get(settings.RUNTIME_MODE, ("opencode", "opencode"))


class EngineSelectionError(Exception):
    """A profile's engine preference cannot be honoured. Never silently falls back."""


def _default_engine_runtime_factory(engine_id: str):
    if engine_id == "opencode":
        from backend.pyworker.runtime.opencode_runtime import RealOpenCodeRuntime
        return RealOpenCodeRuntime()
    if engine_id == "claude-code":
        from backend.pyworker.runtime.claude_runtime import ClaudeCodeRuntime
        return ClaudeCodeRuntime()
    if engine_id == "codex":
        from backend.pyworker.runtime.codex_runtime import CodexCliRuntime
        return CodexCliRuntime()
    if engine_id == "antigravity":
        from backend.pyworker.runtime.agy_acp_runtime import AgyAcpRuntime
        return AgyAcpRuntime()
    raise EngineSelectionError(f"Unsupported engine '{engine_id}'.")


async def _select_task_runtime(profile, default_runtime, factory, cache, model_id, variant):
    """Return (runtime, provider_id, engine_label) for one worker task.

    No profile engine -> the server default runtime (historical behaviour). An
    explicit engine gets its own adapter; for non-OpenCode engines the
    model/variant are checked against that adapter's catalog (no model call).
    OpenCode free-only enforcement stays inside the OpenCode adapter."""
    engine = ((profile.get("engine") if profile else None) or "").strip() or None
    if engine is None:
        return default_runtime, _default_runtime_identity()[1], None
    if engine not in ENGINE_PROVIDERS:
        raise EngineSelectionError(f"Unsupported engine '{engine}'.")
    if engine == "mock":
        if settings.RUNTIME_MODE != "mock":
            raise EngineSelectionError("Engine 'mock' is only available in explicit mock mode.")
        return default_runtime, ENGINE_PROVIDERS[engine], engine
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
                f"Model '{model_id}' is not offered by engine '{engine}'; set an engine-specific model on the profile.")
        if variant not in (None, "", "default") and variant not in (entry.variants or []):
            raise EngineSelectionError(
                f"Variant '{variant}' is not supported by engine '{engine}' for model '{model_id}'.")
    return rt, ENGINE_PROVIDERS[engine], engine


async def _execute_single_task(
    db: Database,
    runtime,
    mission: Dict[str, Any],
    task: Dict[str, Any],
    dep_outputs: Dict[str, Any],
    sem: asyncio.Semaphore,
    engine_runtime_factory,
    engine_cache: Optional[Dict[str, Any]],
    manager,
) -> None:
    default_engine_label, _ = _default_runtime_identity()
    t = db.one("SELECT * FROM mission_tasks WHERE id=?", (task["id"],))
    if not t:
        return
    mission_id = mission["id"]
    try:
        deps = json.loads(t["depends_on_json"] or "[]")
    except Exception:
        deps = []
    dep_failed = False
    for d in deps:
        dep_task = db.one("SELECT status FROM mission_tasks WHERE mission_id=? AND task_key=?", (mission_id, d))
        if not dep_task or dep_task["status"] != "succeeded":
            dep_failed = True
            break
    task_id, task_key = t["id"], t["task_key"]
    if dep_failed or mission_was_cancelled(db, mission_id):
        cancelled = mission_was_cancelled(db, mission_id)
        new_status = "cancelled" if cancelled else "skipped"
        applied = transition_task(
            db, task_id, ("queued",), guard_mission=not cancelled, status=new_status, finished_at=ts(),
            error_message="Skipped: upstream dependency did not succeed." if not cancelled else "Cancelled by user.")
        if not applied and not cancelled:
            await _finalize_task_cancelled(db, mission_id, t)
            return
        await _broadcast(mission_id, "task.status.changed", {"taskKey": task_key, "status": new_status})
        return

    # Resolve employee config (role instructions + model).
    profile = None
    if t["agent_profile_id"]:
        profile = profile_repo.get_by_id(db, t["agent_profile_id"])
    if not profile:
        for p in profile_repo.list_all(db):
            if (p["role_type"] or "").lower() == (t["employee_role"] or "").lower():
                profile = p
                break
    instructions = (profile["instructions"] if profile else t["instructions"]) or t["instructions"]
    model_id = t["model_id"] or (profile["default_model_id"] if profile else None) or settings.DEFAULT_MODEL_ID
    variant = t["variant"] or (profile["default_variant_id"] if profile else None)

    context_parts = [t["input_context"] or ""]
    for d in deps:
        out = dep_outputs.get(d)
        if out is not None:
            excerpt = json.dumps(out, ensure_ascii=False)[:2000]
            context_parts.append(f"[Dependency {d} output]: {excerpt}")
    full_prompt = (
        f"You are the '{t['employee_role']}' employee. {instructions}\n"
        f"Task: {t['title']}\nDetails: {t['instructions']}\n"
        f"Mission context: {(t['input_context'] or '')[:4000]}\n"
        + ("\n".join(context_parts[1:]) if len(context_parts) > 1 else "")
        + "\nReturn STRICT JSON only matching the expected output. No markdown fences."
    )[: settings.MISSION_MAX_PROMPT_CHARS]
    output_schema_json = profile.get("output_schema_json") if profile else None
    if output_schema_json:
        full_prompt += "\nRequired output schema:\n" + output_schema_json

    def validate_worker(text):
        from backend.pyworker.engine.output_validation import validate_output
        parsed = _parse_worker_json(text)
        output_schema = json.loads(output_schema_json) if output_schema_json else None
        validate_output(parsed, output_schema)
        if t["employee_role"] == "reviewer" and isinstance(parsed, dict) and parsed.get("approved") is False:
            raise ValueError("Quality review rejected the deliverable.")
        return parsed

    try:
        task_runtime, task_provider, task_engine = await _select_task_runtime(
            profile, runtime, engine_runtime_factory or _default_engine_runtime_factory,
            engine_cache if engine_cache is not None else {}, model_id, variant)
    except EngineSelectionError as exc:
        msg = f"Engine selection failed: {str(exc)[:300]}"
        if not transition_task(db, task_id, ("queued", "running"), status="failed", error_message=msg,
                               finished_at=ts()):
            await _finalize_task_cancelled(db, mission_id, t)
            return
        await _broadcast(mission_id, "task.status.changed", {"taskKey": task_key, "status": "failed"})
        return

    async with sem:
        last_error = ""
        for attempt in range(1, settings.MISSION_MAX_RETRIES + 2):
            start_values: Dict[str, Any] = {"status": "running", "attempt_count": attempt}
            if attempt == 1:
                start_values["started_at"] = ts()
            # Atomic start: refused if the mission was cancelled or the task already left queued/running.
            if is_mission_cancelled(mission_id) or not transition_task(
                    db, task_id, ("queued", "running"), **start_values):
                await _finalize_task_cancelled(db, mission_id, t)
                return
            await _broadcast(mission_id, "task.status.changed",
                             {"taskKey": task_key, "status": "running", "attempt": attempt})
            try:
                # Every attempt is a persisted run on the engine selected above.
                # Invalid structured output fails the run (retry/failure); it
                # never becomes a succeeded run.
                res, parsed = await manager.run_mission_task(
                    runtime=task_runtime, mission_id=mission_id, task_id=task_id, attempt=attempt,
                    engine=task_engine or default_engine_label, provider_id=task_provider,
                    model_id=model_id, variant=variant, prompt=full_prompt,
                    session_title=f"mission-{mission_id[:8]}-{task_key}",
                    timeout=settings.MISSION_TASK_TIMEOUT, max_output_chars=settings.MISSION_MAX_OUTPUT_CHARS,
                    validate=validate_worker)
                if mission_was_cancelled(db, mission_id):
                    await _finalize_task_cancelled(db, mission_id, t)
                    return
                output_json = json.dumps(
                    {"result": parsed, "model": res.model_id, "variant": res.variant_id,
                     "session_id": res.session_id, "duration_ms": res.duration_ms,
                     **({"engine": task_engine} if task_engine else {})}, ensure_ascii=False)
                # Atomic CAS: only a still-running task of a non-cancelled mission may succeed.
                if not transition_task(
                    db, task_id, ("running",), output_json=output_json, session_id=res.session_id,
                    duration_ms=res.duration_ms, model_id=res.model_id, status="succeeded", finished_at=ts(),
                ):
                    await _finalize_task_cancelled(db, mission_id, t)
                    return
                dep_outputs[task_key] = parsed
                await _broadcast(mission_id, "task.status.changed",
                                 {"taskKey": task_key, "status": "succeeded", "duration_ms": res.duration_ms})
                return
            except MissionRunCancelled:
                await _finalize_task_cancelled(db, mission_id, t, run_cancelled=True)
                return
            except asyncio.TimeoutError:
                if mission_was_cancelled(db, mission_id):
                    await _finalize_task_cancelled(db, mission_id, t)
                    return
                last_error = f"Task timed out after {settings.MISSION_TASK_TIMEOUT}s (attempt {attempt})."
            except Exception as e:
                if mission_was_cancelled(db, mission_id):
                    await _finalize_task_cancelled(db, mission_id, t)
                    return
                last_error = safe_error_message(e)
                logger.warning("Mission %s task %s attempt %d failed: %s", mission_id, task_key, attempt,
                               last_error[:200])
            if not transition_task(db, task_id, ("running",), error_message=last_error):
                await _finalize_task_cancelled(db, mission_id, t)
                return
            await _broadcast(mission_id, "task.attempt.failed",
                             {"taskKey": task_key, "attempt": attempt, "error": last_error[:300]})
        # Exhausted retries: only a still-running task of a live mission may fail.
        if not transition_task(db, task_id, ("running",), status="failed", finished_at=ts()):
            await _finalize_task_cancelled(db, mission_id, t)
            return
        await _broadcast(mission_id, "task.status.changed", {"taskKey": task_key, "status": "failed"})


def _status_of(db: Database, mission_id: str) -> Dict[str, Any]:
    row = db.scalar("SELECT status FROM missions WHERE id=?", (mission_id,))
    return {"status": row or "failed"}


async def run_mission_background(mission_id: str, db: Database, runtime, manager,
                                 engine_runtime_factory=None) -> Dict[str, Any]:
    """Background worker: planning -> dispatch -> synthesis. Never fabricates success.

    Worker attempts, planning and synthesis all run through the persisted run
    manager (kinds mission_task / mission_planner / mission_synthesis)."""
    rt = runtime
    _mission_managers[mission_id] = manager
    stage_engine, stage_provider = _default_runtime_identity()
    try:
        mission = mission_repo.get(db, mission_id)
        if not mission:
            return {"status": "failed", "error": "mission not found"}
        if mission["status"] not in ("queued",):
            return {"status": mission["status"]}

        # Atomic claim: only one runner, and never over a cancel that just landed.
        if not transition_mission(db, mission_id, ("queued",), status="planning", started_at=ts()):
            return _status_of(db, mission_id)
        await _broadcast(mission_id, "mission.status.changed", {"status": "planning"})

        # ---- CEO planning (real model) ----
        try:
            roles = sorted({(p["role_type"] or "").lower() for p in profile_repo.list_all(db) if p["role_type"]})
            plan_prompt = build_planning_prompt(mission["objective"], roles)
            plan_model = mission["model_id"] or settings.DEFAULT_MODEL_ID
            plan_req = ExecuteAgentTask(
                prompt=plan_prompt[: settings.MISSION_MAX_PROMPT_CHARS], model_id=plan_model,
                provider_id=stage_provider, variant=mission["variant"],
                session_title=f"mission-{mission_id[:8]}-planning", timeout=settings.MISSION_TASK_TIMEOUT)
            objective = mission["objective"]
            # Persisted stage run: output is validated BEFORE the run may become succeeded.
            _plan_res, plan = await manager.run_mission_task(
                runtime=rt, mission_id=mission_id, task_id=None, attempt=1, engine=stage_engine,
                provider_id=plan_req.provider_id, model_id=plan_req.model_id, variant=plan_req.variant,
                prompt=plan_req.prompt, session_title=plan_req.session_title, timeout=plan_req.timeout,
                max_output_chars=settings.MISSION_MAX_OUTPUT_CHARS,
                validate=lambda text: parse_and_validate_plan(text, objective), kind=KIND_MISSION_PLANNER)
            if mission_was_cancelled(db, mission_id):
                return await _finalize_mission_cancelled(db, mission_id)
        except Exception as e:
            if mission_was_cancelled(db, mission_id):
                return await _finalize_mission_cancelled(db, mission_id)
            err = f"Planning failed: {safe_error_message(e)}"
            if not transition_mission(db, mission_id, ("planning", "running"), status="failed",
                                      error_summary=err, finished_at=ts()):
                if mission_was_cancelled(db, mission_id):
                    return await _finalize_mission_cancelled(db, mission_id)
                return _status_of(db, mission_id)
            await _broadcast(mission_id, "mission.status.changed", {"status": "failed", "error": err[:300]})
            return {"status": "failed", "error": err}

        plan_json = json.dumps({"objective": plan.objective, "tasks": [t.model_dump() for t in plan.tasks]},
                               ensure_ascii=False)
        if not transition_mission(db, mission_id, ("planning",), plan_json=plan_json, status="running"):
            if mission_was_cancelled(db, mission_id):
                return await _finalize_mission_cancelled(db, mission_id)
            return _status_of(db, mission_id)
        await _broadcast(mission_id, "mission.status.changed", {"status": "running", "tasks": len(plan.tasks)})

        # ---- persist tasks + dispatch with dependencies ----
        by_role_profile: Dict[str, Any] = {}
        for p in profile_repo.list_all(db):
            by_role_profile.setdefault((p["role_type"] or "").lower(), p)
        created: List[Dict[str, Any]] = []
        for pt in plan.tasks:
            prof = by_role_profile.get(pt.employee_role)
            created.append(mission_repo.create_task(
                db, mission_id=mission_id, task_key=pt.key, employee_role=pt.employee_role, title=pt.title,
                instructions=pt.instructions, input_context=mission["objective"], depends_on=pt.depends_on,
                agent_profile_id=prof["id"] if prof else None,
                model_id=(prof["default_model_id"] if prof else None) or mission["model_id"] or settings.DEFAULT_MODEL_ID,
                variant=(prof["default_variant_id"] if prof else None) or mission["variant"]))
        await _broadcast(mission_id, "mission.tasks.created", {"tasks": [c["task_key"] for c in created]})

        sem = asyncio.Semaphore(max(1, settings.MISSION_MAX_CONCURRENCY))
        dep_outputs: Dict[str, Any] = {}
        engine_cache: Dict[str, Any] = {}
        for level in _levels(created):
            if is_mission_cancelled(mission_id):
                break
            await asyncio.gather(*[
                _execute_single_task(db, rt, mission, t, dep_outputs, sem, engine_runtime_factory,
                                     engine_cache, manager)
                for t in level])

        mission = mission_repo.get(db, mission_id)
        tasks = mission_repo.tasks_for(db, mission_id)
        if mission_was_cancelled(db, mission_id):
            return await _finalize_mission_cancelled(db, mission_id)

        ok = [t for t in tasks if t["status"] == "succeeded"]
        bad = [t for t in tasks if t["status"] in ("failed", "skipped", "cancelled")]

        # ---- CEO synthesis (real model, best-effort) ----
        summary_text = ""
        try:
            results = []
            for t in tasks:
                try:
                    out = json.loads(t["output_json"] or "{}")
                    excerpt = json.dumps(out.get("result", out), ensure_ascii=False)[:1500]
                except Exception:
                    excerpt = (t["output_json"] or "")[:1500]
                results.append({"task_key": t["task_key"], "title": t["title"],
                                "employee_role": t["employee_role"], "status": t["status"],
                                "output_excerpt": excerpt})
            syn_req = ExecuteAgentTask(
                prompt=build_synthesis_prompt(mission["objective"], results)[: settings.MISSION_MAX_PROMPT_CHARS],
                model_id=mission["model_id"] or settings.DEFAULT_MODEL_ID, provider_id=stage_provider,
                variant=mission["variant"], session_title=f"mission-{mission_id[:8]}-synthesis",
                timeout=settings.MISSION_TASK_TIMEOUT)
            syn_res, _ = await manager.run_mission_task(
                runtime=rt, mission_id=mission_id, task_id=None, attempt=1, engine=stage_engine,
                provider_id=syn_req.provider_id, model_id=syn_req.model_id, variant=syn_req.variant,
                prompt=syn_req.prompt, session_title=syn_req.session_title, timeout=syn_req.timeout,
                max_output_chars=settings.MISSION_MAX_OUTPUT_CHARS, kind=KIND_MISSION_SYNTHESIS)
            if mission_was_cancelled(db, mission_id):
                return await _finalize_mission_cancelled(db, mission_id)
            summary_text = (syn_res.text or "")[: settings.MISSION_MAX_OUTPUT_CHARS]
        except Exception as e:
            if mission_was_cancelled(db, mission_id):
                return await _finalize_mission_cancelled(db, mission_id)
            summary_text = json.dumps({"summary": f"Synthesis unavailable: {safe_error_message(e)}",
                                       "completed": [t["task_key"] for t in ok],
                                       "failed": [t["task_key"] for t in bad],
                                       "unverified": [t["task_key"] for t in ok]})

        final_values: Dict[str, Any] = {"final_summary": summary_text, "finished_at": ts()}
        if ok and not bad:
            final_status = "succeeded"
        elif ok and bad:
            final_status = "partially_succeeded"
        else:
            final_status = "failed"
            if not mission["error_summary"]:
                final_values["error_summary"] = "; ".join([(t["error_message"] or t["task_key"]) for t in bad])[:1000]
        # Atomic: terminal result only from a live mission; a committed cancel is never overwritten.
        if not transition_mission(db, mission_id, ("planning", "running"), status=final_status, **final_values):
            if mission_was_cancelled(db, mission_id):
                return await _finalize_mission_cancelled(db, mission_id)
            return _status_of(db, mission_id)
        await _broadcast(mission_id, "mission.status.changed",
                         {"status": final_status, "succeeded": len(ok), "failed": len(bad)})
        return {"status": final_status, "succeeded": len(ok), "failed": len(bad)}
    finally:
        _clear_cancel(mission_id)
        _mission_managers.pop(mission_id, None)
