"""CEO missions API: real end-to-end AI execution.

``missions.create`` persists (queued) and starts the background worker; it never
fakes success. Greetings get one natural reply instead of the employee pipeline.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import sqlite3
import time as _time
from typing import Any, Dict, List, Optional

from backend.pyworker.appsettings import settings
from backend.pyworker.core import missions as mission_repo
from backend.pyworker.core import preferences as prefs_mod
from backend.pyworker.core import profiles as profile_repo
from backend.pyworker.db.core import Database, ts
from backend.pyworker.errors import api_error
from backend.pyworker.orchestration import routing
from backend.pyworker.orchestration.ceo import (
    MISSION_NON_TERMINAL,
    EngineSelectionError,
    _default_runtime_identity,
    _mission_managers,
    _select_task_runtime,
    cancel_mission,
    cancel_mission_rows,
    mission_was_cancelled,
    run_mission_background,
    transition_mission,
    transition_task,
)
from backend.pyworker.runtime.base import ExecuteAgentTask
from backend.pyworker.runtime.engine_registry import create_engine_runtime
from backend.pyworker.runtime.errors import RuntimeErrorBase, safe_error_message
from backend.pyworker.runtime.providers import ledger_provider
from backend.pyworker.runtime.ledger import KIND_GREETING, run_agent_task
from backend.pyworker.validate import bad, check_keys, opt_int, opt_list, opt_str, pagination, req_str

from .registry import method

logger = logging.getLogger("agentos.missions")

# Greetings/small-talk openers (exact match after normalization) are answered
# conversationally with a single reply instead of launching the pipeline.
_GREETING_OBJECTIVES = frozenset({
    "عامل ايه", "عامل اي", "عامل إيه", "عامل ايه؟", "عامل إيه؟",
    "ازيك", "إزيك", "ازيك؟", "إزيك؟", "اخبارك", "أخبارك", "اخبارك ايه",
    "ايه الاخبار", "إيه الأخبار", "سلام", "سلام عليكم", "السلام عليكم",
    "صباح الخير", "مساء الخير", "اهلا", "أهلا", "اهلا بيك", "هلا", "هاي",
    "hi", "hey", "hello", "yo", "sup", "how are you", "what's up", "whats up",
})


def _normalize_greeting_candidate(objective: str) -> str:
    text = (objective or "").strip().lower()
    text = text.strip("؟?!.,…—- ").strip()
    return " ".join(text.split())


def is_greeting_objective(objective: str) -> bool:
    """True only for exact known greetings — never for real task descriptions."""
    return _normalize_greeting_candidate(objective) in _GREETING_OBJECTIVES


def _out(db: Database, mission: Dict[str, Any]) -> Dict[str, Any]:
    return mission_repo.mission_out(mission, mission_repo.tasks_for(db, mission["id"]))


async def preflight_model(ctx, runtime, model_id: str, variant: Optional[str], *, profile: Any = None):
    """Provider-aware model/variant validation BEFORE any DB write (catalog
    listing only, never a model call). Returns (runtime, provider_id,
    engine_label_or_None, catalog_entry). A profile with an explicit engine is checked on that engine; anything
    else on the default engine. "Free only" is the engine's own policy."""
    engine = ((profile.get("engine") if profile else None) or "").strip() or None
    if engine is not None:
        if ledger_provider(engine) is None:
            raise api_error(400, f"Unsupported engine '{engine}'.")
        if engine == "mock":
            if settings.RUNTIME_MODE != "mock":
                raise api_error(400, "Engine 'mock' is only available in explicit mock mode.")
            rt = runtime
        else:
            try:
                rt = ctx.engine_factory(engine)
            except EngineSelectionError as exc:
                raise api_error(400, str(exc)[:300])
            except Exception:
                raise api_error(503, f"Engine '{engine}' is unavailable.")
        label, provider, checked = engine, ledger_provider(engine), engine
    else:
        checked = prefs_mod.default_engine_id()
        rt, label, provider = runtime, None, _default_runtime_identity()[1]
    models = await routing.load_catalog(rt, checked)
    entry = routing.check_model(models, checked, model_id, variant)
    return rt, provider, label, entry


async def _run_mission_task(ctx, mission_id: str, runtime) -> None:
    try:
        await run_mission_background(mission_id, ctx.db, runtime, ctx.runs,
                                     engine_runtime_factory=ctx.engine_factory)
    except Exception:
        logger.exception("mission %s runner crashed", mission_id)


async def _run_explicit_delegation(ctx, mission_id: str, task_id: str, profile_id: str, runtime, resolved) -> None:
    db, manager = ctx.db, ctx.runs
    _mission_managers[mission_id] = manager
    try:
        mission = mission_repo.get(db, mission_id)
        task = db.one("SELECT * FROM mission_tasks WHERE id=? AND mission_id=?", (task_id, mission_id))
        profile = profile_repo.get_by_id(db, profile_id)
        if not mission or not task or not profile:
            return
        now = ts()
        # Atomic start in ONE transaction: a queued delegation cancelled before
        # this point must never start (the task CAS also refuses cancelled missions).
        with db.tx():
            started = (transition_mission(db, mission_id, MISSION_NON_TERMINAL, status="running",
                                          started_at=mission["started_at"] or now)
                       and transition_task(db, task_id, ("queued",), status="running", started_at=now,
                                           attempt_count=1))
            if not started:
                raise _Abort()
        prompt = (
            f"You are the {profile['role_type']} employee ({profile['name']}).\n"
            f"Role instructions: {profile['instructions']}\n\n"
            f"Assignment: {task['title']}\n{task['instructions']}\n"
            f"Input/context: {task['input_context'] or '(none supplied)'}\n"
            f"Expected deliverable: {task['expected_output'] or '(use a clear, useful format)'}\n"
            "Complete the assignment and return the actual deliverable directly.")
        model_id = task["model_id"] or prefs_mod.default_model_id()
        task_runtime, provider_id, engine = resolved or await _select_task_runtime(
            profile, runtime, ctx.engine_factory or create_engine_runtime, {}, model_id,
            task["variant"])
        result, _ = await manager.run_mission_task(
            runtime=task_runtime, mission_id=mission_id, task_id=task_id, attempt=1,
            engine=engine or _default_runtime_identity()[0], provider_id=provider_id, model_id=model_id,
            variant=task["variant"], prompt=prompt, session_title=f"agentos-delegated-{mission_id[:8]}",
            timeout=settings.MISSION_TASK_TIMEOUT, max_output_chars=settings.MISSION_MAX_OUTPUT_CHARS,
            kind="mission_task")  # plain-text deliverable: no strict-JSON validator
        # A cancel may have landed while the provider was working: never flip it to success.
        if mission_was_cancelled(db, mission_id):
            cancel_mission_rows(db, mission_id)
            return
        output_json = json.dumps({"result": result.text, "model": result.model_id,
                                  "variant": result.variant_id, "session_id": result.session_id,
                                  "duration_ms": result.duration_ms}, ensure_ascii=False)
        finished = ts()
        try:
            with db.tx():
                done = (transition_task(db, task_id, ("running",), output_json=output_json,
                                        session_id=result.session_id, duration_ms=result.duration_ms,
                                        model_id=result.model_id, status="succeeded", finished_at=finished)
                        and transition_mission(db, mission_id, MISSION_NON_TERMINAL, status="succeeded",
                                               final_summary=json.dumps({"result": result.text},
                                                                        ensure_ascii=False),
                                               finished_at=finished))
                if not done:
                    raise _Abort()
        except _Abort:
            cancel_mission_rows(db, mission_id)
    except _Abort:
        return
    except Exception as exc:
        if mission_was_cancelled(db, mission_id):
            cancel_mission_rows(db, mission_id)
            return
        error = str(exc)[:500] if isinstance(exc, EngineSelectionError) else safe_error_message(exc)
        now = ts()
        try:
            with db.tx():
                failed = (transition_task(db, task_id, ("queued", "running"), status="failed",
                                          error_message=error, finished_at=now)
                          and transition_mission(db, mission_id, MISSION_NON_TERMINAL, status="failed",
                                                 error_summary=error, finished_at=now))
                if not failed:
                    raise _Abort()
        except _Abort:
            if mission_was_cancelled(db, mission_id):
                cancel_mission_rows(db, mission_id)
    finally:
        _mission_managers.pop(mission_id, None)


class _Abort(Exception):
    """Internal: roll back the surrounding transaction."""


async def _create_greeting_mission(ctx, objective, model_id, provider_id, variant, title, runtime):
    prompt = (
        "You are Loqman, CEO of the company — a warm, natural conversationalist.\n"
        f"The user just said: {objective.strip()}\n"
        "Reply once, naturally and briefly, in the user's own language and dialect "
        "(Egyptian Arabic if they write Egyptian Arabic). "
        "Plain text only — no JSON, no mention of missions, tasks, or employees.")
    t0 = _time.time()
    try:
        result = await run_agent_task(runtime, ExecuteAgentTask(
            prompt=prompt, model_id=model_id, provider_id=provider_id, variant=variant,
            session_title="greeting-reply", timeout=90), kind=KIND_GREETING)
        reply = (result.text or "").strip()
        if not reply:
            raise RuntimeErrorBase("Empty greeting reply from runtime.")
        try:
            parsed = json.loads(reply)
            if isinstance(parsed, dict):
                for key in ("reply", "guidance", "summary", "text"):
                    if isinstance(parsed.get(key), str) and parsed[key].strip():
                        reply = parsed[key].strip()
                        break
        except Exception:
            pass
    except Exception:
        raise api_error(502, "Greeting reply failed.")
    duration_ms = int((_time.time() - t0) * 1000)
    now = ts()
    db = ctx.db
    with db.tx():
        mission = mission_repo.create_mission(db, objective=objective, title=title or objective[:80],
                                              model_id=model_id, variant=variant)
        task = mission_repo.create_task(db, mission_id=mission["id"], task_key="greeting_reply",
                                        employee_role="general_manager", title="Greeting reply",
                                        instructions=objective, model_id=result.model_id,
                                        variant=result.variant_id)
        db.update("mission_tasks", {"status": "succeeded", "attempt_count": 1,
                                   "output_json": json.dumps(reply), "session_id": result.session_id,
                                   "duration_ms": duration_ms, "started_at": now, "finished_at": now},
                  "id=?", (task["id"],))
        db.update("missions", {"status": "succeeded", "final_summary": reply, "started_at": now,
                               "finished_at": now, "updated_at": now}, "id=?", (mission["id"],))
    return _out(db, mission_repo.get(db, mission["id"]))


@method("missions.create")
async def missions_create(ctx, params):
    check_keys(params, {"objective"}, ("title", "employee_roles", "model_id", "variant", "request_key"))
    objective_raw = req_str(params, "objective", 1, 4000)
    title = opt_str(params, "title", 0, 255)
    roles_in = opt_list(params, "employee_roles", 8, lambda r: req_str({"r": r}, "r", 0)) or []
    model_id_in = opt_str(params, "model_id")
    variant = opt_str(params, "variant")
    request_key = opt_str(params, "request_key", 1, 100)
    objective = objective_raw.strip()
    if not objective:
        raise api_error(400, "Objective must not be empty.")
    roles = [r.strip().lower() for r in roles_in if r.strip()] or ["planner", "reviewer"]
    model_id = model_id_in or prefs_mod.default_model_id()
    runtime = ctx.get_runtime()
    _, _, _, target = await preflight_model(ctx, runtime, model_id, variant)

    if is_greeting_objective(objective):
        if request_key:
            raise api_error(422, "Request keys are not supported for greeting replies")
        return await _create_greeting_mission(ctx, objective, model_id, target.provider_id, variant, title, runtime)

    db = ctx.db
    key = f"client:{request_key}" if request_key else None
    canonical = json.dumps({"objective": objective, "roles": sorted(roles), "model": model_id,
                            "variant": variant, "title": title}, sort_keys=True, ensure_ascii=False)
    fingerprint = hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def existing_request():
        row = db.one("SELECT * FROM missions WHERE idempotency_key=?", (key,)) if key else None
        if row and row["request_fingerprint"] != fingerprint:
            raise api_error(409, "Request key already used for different input")
        return row

    existing = existing_request()
    if existing:
        return _out(db, existing)
    try:
        mission = mission_repo.create_mission(db, objective=objective, title=title or objective[:80],
                                              model_id=model_id, variant=variant, idempotency_key=key,
                                              request_fingerprint=fingerprint if key else None)
    except sqlite3.IntegrityError:
        existing = existing_request()
        if not existing:
            raise
        return _out(db, existing)
    # Real background worker (no fake success: status stays queued/planning/running).
    ctx.spawn(_run_mission_task(ctx, mission["id"], runtime), "mission-run")
    return mission_repo.mission_out(mission, [])


@method("missions.delegate")
async def missions_delegate(ctx, params):
    """Create and execute exactly one user-selected employee assignment; skip CEO planning."""
    check_keys(params, {"conversation_id", "employee_role", "title", "instructions"},
               ("agent_profile_id", "input_context", "expected_output", "variant", "model_id"))
    conversation_id = req_str(params, "conversation_id", 1, 128)
    employee_role = req_str(params, "employee_role", 1, 64)
    agent_profile_id = opt_str(params, "agent_profile_id", 0, 128)
    title = req_str(params, "title", 1, 255)
    instructions = req_str(params, "instructions", 1, 8000)
    input_context = opt_str(params, "input_context", 0, 12000)
    expected_output = opt_str(params, "expected_output", 0, 2000)
    model_in = opt_str(params, "model_id", 1, 128)
    variant = opt_str(params, "variant", 0, 64)
    db = ctx.db
    profile = None
    if agent_profile_id:
        profile = profile_repo.get_by_id(db, agent_profile_id)
        if not profile:
            raise api_error(404, "Selected employee profile was not found.")
    else:
        profile = next((p for p in profile_repo.list_all(db)
                        if p["id"].lower() == employee_role.lower() or p["role_type"].lower() == employee_role.lower()),
                       None)
    if not profile:
        raise api_error(404, "Selected employee role was not found.")
    runtime = ctx.get_runtime()
    model_id = model_in
    if not model_id:
        # No model named: the worker resolves it. An agent bound to an engine uses its own model; any other
        # agent uses its stored model when the default engine offers it, else the default model.
        stored = profile.get("default_model_id")
        if (profile.get("engine") or "").strip():
            model_id = stored or prefs_mod.default_model_id()
        else:
            model_id = await routing.usable_model(runtime, prefs_mod.default_engine_id(), stored,
                                                  prefs_mod.default_model_id())
    resolved_rt, resolved_provider, resolved_engine, _ = await preflight_model(
        ctx, runtime, model_id, variant, profile=profile)
    objective = instructions.strip()
    with db.tx():
        mission = mission_repo.create_mission(db, objective=objective, title=title.strip(), model_id=model_id,
                                              variant=variant, source_conversation_id=conversation_id)
        task = mission_repo.create_task(db, mission_id=mission["id"], task_key="explicit_assignment",
                                        employee_role=profile["role_type"], title=title.strip(),
                                        instructions=objective, input_context=input_context,
                                        agent_profile_id=profile["id"], model_id=model_id, variant=variant,
                                        expected_output=expected_output)
    ctx.spawn(_run_explicit_delegation(ctx, mission["id"], task["id"], profile["id"], runtime,
                                       (resolved_rt, resolved_provider, resolved_engine)), "mission-delegate")
    return mission_repo.mission_out(mission, [task])


@method("missions.list")
async def missions_list(ctx, params):
    check_keys(params, optional=("skip", "limit"))
    skip, limit = pagination(params, 50, 1000)
    return {"missions": [_out(ctx.db, m) for m in mission_repo.list_missions(ctx.db, skip, limit)]}


@method("missions.get")
async def missions_get(ctx, params):
    check_keys(params, {"mission_id"})
    m = mission_repo.get(ctx.db, req_str(params, "mission_id", 1, 64))
    if not m:
        raise api_error(404, "Mission not found")
    return _out(ctx.db, m)


@method("missions.cancel")
async def missions_cancel(ctx, params):
    check_keys(params, {"mission_id"})
    mission_id = req_str(params, "mission_id", 1, 64)
    m = mission_repo.get(ctx.db, mission_id)
    if not m:
        raise api_error(404, "Mission not found")
    if m["status"] in ("succeeded", "partially_succeeded", "failed", "cancelled"):
        return _out(ctx.db, m)
    # Atomic compare-and-set: a concurrent completion wins over a late cancel;
    # a committed cancel wins over everything afterwards.
    if cancel_mission_rows(ctx.db, mission_id):
        cancel_mission(mission_id)  # also schedules persisted-run cancel + engine abort
    return _out(ctx.db, mission_repo.get(ctx.db, mission_id))


@method("missions.runs")
async def missions_runs(ctx, params):
    check_keys(params, {"mission_id"})
    mission_id = req_str(params, "mission_id", 1, 64)
    if mission_repo.get(ctx.db, mission_id) is None:
        raise api_error(404, "Mission not found")
    runs = ctx.db.all("SELECT * FROM conversation_runs WHERE mission_id=? ORDER BY created_at, id LIMIT 100",
                      (mission_id,))
    return {"runs": [ctx.runs.snapshot(r) for r in runs]}
