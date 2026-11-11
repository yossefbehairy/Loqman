"""
CEO missions API: real end-to-end AI execution.

POST /api/missions        -> persist (queued) + start background worker, return id+status
GET  /api/missions        -> list
GET  /api/missions/{id}   -> mission + tasks + deliverables
POST /api/missions/{id}/cancel
GET  /api/missions/{id}/events (SSE: replay from DB + live)
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from backend.app.config import settings
from backend.app.db.models import Mission, MissionTask, ConversationRun
from backend.app.api.routes.conversation_runs import RunOut
from backend.app.core.conversation_runs import ConversationRunManager, manager_for_factory
from backend.app.db.repository import AgentProfileRepository, MissionRepository
from backend.app.db.session import SessionLocal, get_db
from backend.app.engine.events import event_hub
from backend.app.orchestration.ceo import (
    MISSION_NON_TERMINAL,
    EngineSelectionError,
    _ENGINE_PROVIDERS,
    _default_engine_runtime_factory,
    _default_runtime_identity,
    _mission_managers,
    _select_task_runtime,
    cancel_mission,
    cancel_mission_rows,
    mission_was_cancelled,
    transition_mission,
    transition_task,
    run_mission_background,
)
from backend.app.runtime import get_runtime, AgentRuntime
from backend.app.runtime.base import ExecuteAgentTask
from backend.app.runtime.ledger import run_agent_task, KIND_MISSION_DELEGATE, KIND_GREETING

router = APIRouter(prefix="/api/missions", tags=["missions"])

MISSION_STATUSES = {"queued", "planning", "running", "succeeded", "partially_succeeded", "failed", "cancelled"}
TASK_STATUSES = {"queued", "running", "succeeded", "failed", "skipped", "cancelled"}

# Greetings and small-talk openers (exact match after normalization) are answered
# conversationally with a single reply instead of launching the employee pipeline.
# This list is deliberately narrow: anything else still becomes a real mission.
_GREETING_OBJECTIVES = frozenset({
    # Egyptian Arabic
    "عامل ايه", "عامل اي", "عامل إيه", "عامل ايه؟", "عامل إيه؟",
    "ازيك", "إزيك", "ازيك؟", "إزيك؟", "اخبارك", "أخبارك", "اخبارك ايه",
    "ايه الاخبار", "إيه الأخبار", "سلام", "سلام عليكم", "السلام عليكم",
    "صباح الخير", "مساء الخير", "اهلا", "أهلا", "اهلا بيك", "هلا", "هاي",
    # English
    "hi", "hey", "hello", "yo", "sup", "how are you", "what's up", "whats up",
})


def _normalize_greeting_candidate(objective: str) -> str:
    text = (objective or "").strip().lower()
    text = text.strip("؟?!.,…—- ").strip()
    text = " ".join(text.split())
    return text


def is_greeting_objective(objective: str) -> bool:
    """True only for exact known greetings — never for real task descriptions."""
    return _normalize_greeting_candidate(objective) in _GREETING_OBJECTIVES


class MissionCreate(BaseModel):
    objective: str = Field(min_length=1, max_length=4000)
    title: Optional[str] = Field(default=None, max_length=255)
    employee_roles: Optional[List[str]] = Field(default=None, max_length=8)
    model_id: Optional[str] = None
    variant: Optional[str] = None
    request_key: Optional[str] = Field(default=None, min_length=1, max_length=100)


class DelegatedTaskCreate(BaseModel):
    conversation_id: str = Field(min_length=1, max_length=128)
    employee_role: str = Field(min_length=1, max_length=64)
    # Precise profile pin: when set, resolution uses it instead of the
    # role string (which may match several profiles sharing a role_type).
    agent_profile_id: Optional[str] = Field(default=None, max_length=128)
    title: str = Field(min_length=1, max_length=255)
    instructions: str = Field(min_length=1, max_length=8000)
    input_context: Optional[str] = Field(default=None, max_length=12000)
    expected_output: Optional[str] = Field(default=None, max_length=2000)
    model_id: str = Field(min_length=1, max_length=128)
    variant: Optional[str] = Field(default=None, max_length=64)


class TaskOut(BaseModel):
    id: str
    task_key: str
    employee_role: str
    title: str
    status: str
    model_id: Optional[str] = None
    variant: Optional[str] = None
    attempt_count: int = 0
    output: Optional[Any] = None
    error_message: Optional[str] = None
    duration_ms: Optional[int] = None
    session_id: Optional[str] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None


class MissionOut(BaseModel):
    id: str
    title: str
    objective: str
    status: str
    model_id: Optional[str] = None
    variant: Optional[str] = None
    plan: Optional[Any] = None
    final_summary: Optional[Any] = None
    error_summary: Optional[str] = None
    tasks: List[TaskOut] = Field(default_factory=list)
    created_at: Optional[str] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None


def _parse_json_safe(text: Optional[str]) -> Optional[Any]:
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        return text


def _mission_to_out(mission, tasks: List) -> MissionOut:
    outs: List[TaskOut] = []
    for t in tasks:
        outs.append(TaskOut(
            id=t.id,
            task_key=t.task_key,
            employee_role=t.employee_role,
            title=t.title,
            status=t.status,
            model_id=t.model_id,
            variant=t.variant,
            attempt_count=t.attempt_count or 0,
            output=_parse_json_safe(t.output_json),
            error_message=t.error_message,
            duration_ms=t.duration_ms,
            session_id=t.session_id,
            started_at=t.started_at.isoformat() if t.started_at else None,
            finished_at=t.finished_at.isoformat() if t.finished_at else None,
        ))
    return MissionOut(
        id=mission.id,
        title=mission.title,
        objective=mission.objective,
        status=mission.status,
        model_id=mission.model_id,
        variant=mission.variant,
        plan=_parse_json_safe(mission.plan_json),
        final_summary=_parse_json_safe(mission.final_summary),
        error_summary=mission.error_summary,
        tasks=outs,
        created_at=mission.created_at.isoformat() if mission.created_at else None,
        started_at=mission.started_at.isoformat() if mission.started_at else None,
        finished_at=mission.finished_at.isoformat() if mission.finished_at else None,
    )


def get_mission_session_factory():
    return SessionLocal


async def _run_mission_task(mission_id: str, session_factory=None, runtime=None):
    from backend.app.runtime import get_runtime as _gr

    factory = session_factory or SessionLocal
    rt = runtime or _gr()
    db = factory()
    try:
        await run_mission_background(mission_id, db_factory=factory, runtime=rt)
    finally:
        db.close()


def _force_delegation_cancelled(db: Session, mission_id: str, task_id: str) -> None:
    """Pin mission/task to cancelled, discarding any late output."""
    cancel_mission_rows(db, mission_id)


async def _run_explicit_delegation(mission_id: str, task_id: str, profile_id: str,
                                   session_factory=None, runtime=None,
                                   engine_runtime_factory=None, run_manager=None, resolved=None):
    from backend.app.runtime import get_runtime as _gr

    factory = session_factory or SessionLocal
    rt = runtime or _gr()
    manager = run_manager or manager_for_factory(session_factory)
    # Lets cancel_mission() persist-cancel the run and abort its adapter.
    _mission_managers[mission_id] = (manager, asyncio.get_running_loop())
    db = factory()
    try:
        mission = db.query(Mission).filter_by(id=mission_id).first()
        task = db.query(MissionTask).filter_by(id=task_id, mission_id=mission_id).first()
        profile = AgentProfileRepository(db).get_by_id(profile_id)
        if not mission or not task or not profile:
            return
        now = datetime.now(timezone.utc)
        # Atomic start in ONE transaction: a queued delegation cancelled before
        # this point must never start (the task CAS also refuses cancelled missions).
        mission_started = transition_mission(
            db, mission_id, MISSION_NON_TERMINAL, commit=False,
            status="running", started_at=mission.started_at or now,
        )
        task_started = mission_started and transition_task(
            db, task_id, ("queued",), commit=False,
            status="running", started_at=now, attempt_count=1,
        )
        if not (mission_started and task_started):
            db.rollback()
            return
        db.commit()
        prompt = (
            f"You are the {profile.role_type} employee ({profile.name}).\n"
            f"Role instructions: {profile.instructions}\n\n"
            f"Assignment: {task.title}\n{task.instructions}\n"
            f"Input/context: {task.input_context or '(none supplied)'}\n"
            f"Expected deliverable: {task.expected_output or '(use a clear, useful format)'}\n"
            "Complete the assignment and return the actual deliverable directly."
        )
        model_id = task.model_id or settings.DEFAULT_MODEL_ID
        # Same engine/catalog resolution as standard mission workers; an explicit
        # engine that cannot be honoured fails the delegation (never falls back).
        # `resolved` = adapter already chosen + catalog-checked by the HTTP preflight (no repeat probe).
        task_runtime, provider_id, engine = resolved or await _select_task_runtime(
            profile, rt, engine_runtime_factory or _default_engine_runtime_factory, {}, model_id, task.variant)
        result, _ = await manager.run_mission_task(
            runtime=task_runtime, mission_id=mission_id, task_id=task_id, attempt=1,
            engine=engine or _default_runtime_identity()[0], provider_id=provider_id,
            model_id=model_id, variant=task.variant, prompt=prompt,
            session_title=f"agentos-delegated-{mission.id[:8]}",
            timeout=settings.MISSION_TASK_TIMEOUT,
            max_output_chars=settings.MISSION_MAX_OUTPUT_CHARS, kind="mission_task",
        )  # plain-text deliverable: no strict-JSON validator
        # A cancel may have landed while the provider was working. The late
        # response must not flip a cancelled mission/task to success.
        if mission_was_cancelled(db, mission_id):
            _force_delegation_cancelled(db, mission_id, task_id)
            return
        output_json = json.dumps({
            "result": result.text,
            "model": result.model_id,
            "variant": result.variant_id,
            "session_id": result.session_id,
            "duration_ms": result.duration_ms,
        }, ensure_ascii=False)
        finished = datetime.now(timezone.utc)
        # Task + mission complete together or not at all; both writes are
        # conditional so a committed cancel can never be overwritten.
        done = transition_task(
            db, task_id, ("running",), commit=False,
            output_json=output_json, session_id=result.session_id,
            duration_ms=result.duration_ms, model_id=result.model_id,
            status="succeeded", finished_at=finished,
        ) and transition_mission(
            db, mission_id, MISSION_NON_TERMINAL, commit=False,
            status="succeeded", final_summary=json.dumps({"result": result.text}, ensure_ascii=False),
            finished_at=finished,
        )
        if not done:
            db.rollback()
            _force_delegation_cancelled(db, mission_id, task_id)
            return
        db.commit()
    except Exception as exc:
        db.rollback()
        if mission_was_cancelled(db, mission_id):
            _force_delegation_cancelled(db, mission_id, task_id)
            return
        from backend.app.runtime.errors import safe_error_message
        error = str(exc)[:500] if isinstance(exc, EngineSelectionError) else safe_error_message(exc)
        now = datetime.now(timezone.utc)
        failed = transition_task(
            db, task_id, ("queued", "running"), commit=False,
            status="failed", error_message=error, finished_at=now,
        ) and transition_mission(
            db, mission_id, MISSION_NON_TERMINAL, commit=False,
            status="failed", error_summary=error, finished_at=now,
        )
        if failed:
            db.commit()
        else:
            db.rollback()
            if mission_was_cancelled(db, mission_id):
                _force_delegation_cancelled(db, mission_id, task_id)
    finally:
        _mission_managers.pop(mission_id, None)
        db.close()


async def _create_greeting_mission(
    objective: str,
    model_id: str,
    provider_id: str,
    variant: Optional[str],
    title: Optional[str],
    db: Session,
    runtime: AgentRuntime,
) -> MissionOut:
    """Answer a greeting with one natural reply; persist it as an already-succeeded mission.

    Keeps the MissionOut contract (201 + terminal status) so existing clients that
    poll and read the inbox keep working — but no employee pipeline ever runs.
    """
    import time as _time

    from backend.app.runtime.errors import RuntimeErrorBase

    prompt = (
        "You are Loqman, CEO of the company — a warm, natural conversationalist.\n"
        f"The user just said: {objective.strip()}\n"
        "Reply once, naturally and briefly, in the user's own language and dialect "
        "(Egyptian Arabic if they write Egyptian Arabic). "
        "Plain text only — no JSON, no mention of missions, tasks, or employees."
    )
    t0 = _time.time()
    try:
        result = await run_agent_task(runtime, ExecuteAgentTask(
            prompt=prompt,
            model_id=model_id,
            provider_id=provider_id,
            variant=variant,
            session_title="greeting-reply",
            timeout=90,
        ), kind=KIND_GREETING)
        reply = (result.text or "").strip()
        if not reply:
            raise RuntimeErrorBase("Empty greeting reply from runtime.")
        # Tolerate structured replies (e.g. {"reply": "..."}) — keep only the human text.
        try:
            parsed = json.loads(reply)
            if isinstance(parsed, dict):
                for key in ("reply", "guidance", "summary", "text"):
                    if isinstance(parsed.get(key), str) and parsed[key].strip():
                        reply = parsed[key].strip()
                        break
        except Exception:
            pass
    except Exception as e:
        raise HTTPException(status_code=502, detail="Greeting reply failed.")
    duration_ms = int((_time.time() - t0) * 1000)
    now = datetime.now(timezone.utc)

    repo = MissionRepository(db)
    mission = repo.create_mission(
        objective=objective,
        title=title or objective[:80],
        model_id=model_id,
        variant=variant,
    )
    task = repo.create_task(
        mission_id=mission.id,
        task_key="greeting_reply",
        employee_role="general_manager",
        title="Greeting reply",
        instructions=objective,
        model_id=result.model_id,
        variant=result.variant_id,
    )
    task.status = "succeeded"
    task.attempt_count = 1
    task.output_json = json.dumps(reply)
    task.session_id = result.session_id
    task.duration_ms = duration_ms
    task.started_at = now
    task.finished_at = now
    mission.status = "succeeded"
    mission.final_summary = reply
    mission.started_at = now
    mission.finished_at = now
    db.commit()
    db.refresh(mission)
    return _mission_to_out(mission, [task])


def get_engine_runtime_factory():
    """Adapter factory for explicit profile engines (overridable in tests)."""
    return _default_engine_runtime_factory


def _variant_ok(entry, variant: Optional[str], strict: bool) -> bool:
    if not variant or (strict and variant == "default"):
        return True
    if entry.variants:
        return variant in entry.variants
    return not strict  # strict (account engines): no exposed variants -> none supported


async def _catalog(rt: Any, free_only: bool, label: str):
    try:
        return await rt.list_available_models(free_only=free_only)
    except Exception:
        raise HTTPException(status_code=502, detail=f"{label} runtime unreachable.")


async def _preflight_model(runtime: AgentRuntime, model_id: str, variant: Optional[str], *,
                           profile: Any = None, factory: Any = None):
    """Provider-aware model/variant validation BEFORE any DB write (catalog listing only,
    never a model call). Returns (runtime, provider_id, engine_label_or_None, catalog_entry).

    Explicit profile.engine -> that engine's own adapter/catalog, never the default
    runtime (OpenCode: free-only; Claude/Codex/Antigravity: account catalog, billing
    unknown). No engine -> the configured default runtime: free-only for OpenCode
    (RUNTIME_MODE=real) and mock; account catalog for claude/codex/agy modes."""
    engine = ((getattr(profile, "engine", None) if profile else None) or "").strip() or None
    if engine is not None:
        if engine not in _ENGINE_PROVIDERS:
            raise HTTPException(status_code=400, detail=f"Unsupported engine '{engine}'.")
        if engine == "mock":
            if settings.RUNTIME_MODE != "mock":
                raise HTTPException(status_code=400, detail="Engine 'mock' is only available in explicit mock mode.")
            rt, free_only = runtime, True
        else:
            try:
                rt = (factory or _default_engine_runtime_factory)(engine)
            except EngineSelectionError as exc:
                raise HTTPException(status_code=400, detail=str(exc)[:300])
            except Exception:
                raise HTTPException(status_code=503, detail=f"Engine '{engine}' is unavailable.")
            free_only = engine == "opencode"
        strict = engine not in ("opencode", "mock")
        label, provider = engine, _ENGINE_PROVIDERS[engine]
    else:
        mode = settings.RUNTIME_MODE
        rt, free_only, strict = runtime, mode in ("real", "mock"), mode not in ("real", "mock")
        label, provider = None, _default_runtime_identity()[1]
    models = await _catalog(rt, free_only, "OpenCode" if free_only else (engine or settings.RUNTIME_MODE))
    entry = next((m for m in models if m.id == model_id), None)
    if entry is None:
        if free_only:
            raise HTTPException(
                status_code=400,
                detail=f"Model '{model_id}' is not verified as free and active. "
                       f"Available free models include: {[m.id for m in models[:20]]}")
        raise HTTPException(
            status_code=400,
            detail=f"Model '{model_id}' is not offered by {'engine ' + repr(engine) if engine else 'the configured runtime'}; "
                   f"available models include: {[m.id for m in models[:20]]}")
    if not _variant_ok(entry, variant, strict):
        raise HTTPException(
            status_code=400,
            detail=f"Variant '{variant}' is not supported by model '{model_id}'. Supported: {entry.variants}")
    return rt, provider, label, entry


@router.post("", response_model=MissionOut, status_code=status.HTTP_201_CREATED)
async def create_mission(
    payload: MissionCreate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    runtime: AgentRuntime = Depends(get_runtime),
    session_factory=Depends(get_mission_session_factory),
):
    objective = payload.objective.strip()
    if not objective:
        raise HTTPException(status_code=400, detail="Objective must not be empty.")

    # Resolve employee roles (default: planner + reviewer -> guarantees >=2 tasks).
    roles = [r.strip().lower() for r in (payload.employee_roles or []) if r.strip()]
    if not roles:
        roles = ["planner", "reviewer"]

    model_id = payload.model_id or settings.DEFAULT_MODEL_ID
    # Revalidate model BEFORE persisting. Planner/synthesis use the configured default runtime, so validate against ITS catalog
    # (free-only for OpenCode/mock; account catalog for claude/codex/agy modes).
    _, _, _, target = await _preflight_model(runtime, model_id, payload.variant)

    # Greetings get a single natural reply — never the employee pipeline.
    if is_greeting_objective(objective):
        if payload.request_key:
            raise HTTPException(status_code=422, detail="Request keys are not supported for greeting replies")
        return await _create_greeting_mission(
            objective=objective, model_id=model_id, provider_id=target.provider_id,
            variant=payload.variant, title=payload.title, db=db, runtime=runtime,
        )

    key = f"client:{payload.request_key}" if payload.request_key else None
    canonical = json.dumps({"objective": objective, "roles": sorted(roles), "model": model_id,
                            "variant": payload.variant, "title": payload.title}, sort_keys=True, ensure_ascii=False)
    fingerprint = hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def existing_request():
        row = db.query(Mission).filter(Mission.idempotency_key == key).first() if key else None
        if row and row.request_fingerprint != fingerprint:
            raise HTTPException(status_code=409, detail="Request key already used for different input")
        return row

    existing = existing_request()
    if existing:
        tasks = db.query(MissionTask).filter(MissionTask.mission_id == existing.id).all()
        return _mission_to_out(existing, tasks)
    mission = Mission(objective=objective, title=payload.title or objective[:80], status="queued",
                      model_id=model_id, variant=payload.variant, idempotency_key=key,
                      request_fingerprint=fingerprint if key else None)
    db.add(mission)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = existing_request()
        if not existing:
            raise
        tasks = db.query(MissionTask).filter(MissionTask.mission_id == existing.id).all()
        return _mission_to_out(existing, tasks)
    db.refresh(mission)
    # Start the real background worker (no fake success: status stays queued/planning/running).
    background_tasks.add_task(_run_mission_task, mission.id, session_factory, runtime)
    return _mission_to_out(mission, [])


@router.post("/delegate", response_model=MissionOut, status_code=status.HTTP_201_CREATED)
async def create_delegated_task(
    payload: DelegatedTaskCreate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    runtime: AgentRuntime = Depends(get_runtime),
    session_factory=Depends(get_mission_session_factory),
    engine_factory=Depends(get_engine_runtime_factory),
):
    """Create and execute exactly one user-selected employee assignment; skip CEO planning."""
    profiles = AgentProfileRepository(db).list_all()
    profile = None
    if payload.agent_profile_id:
        profile = AgentProfileRepository(db).get_by_id(payload.agent_profile_id)
        if not profile:
            raise HTTPException(status_code=404, detail="Selected employee profile was not found.")
    else:
        profile = next((p for p in profiles if p.id.lower() == payload.employee_role.lower()
                        or p.role_type.lower() == payload.employee_role.lower()), None)
    if not profile:
        raise HTTPException(status_code=404, detail="Selected employee role was not found.")
    # Resolve the profile's engine + validate model/variant on ITS catalog before any DB write.
    resolved_rt, resolved_provider, resolved_engine, _ = await _preflight_model(
        runtime, payload.model_id, payload.variant, profile=profile, factory=engine_factory)

    objective = payload.instructions.strip()
    mission = MissionRepository(db).create_mission(
        objective=objective,
        title=payload.title.strip(),
        model_id=payload.model_id,
        variant=payload.variant,
    )
    mission.source_conversation_id = payload.conversation_id
    mission.status = "queued"
    db.commit()
    task = MissionRepository(db).create_task(
        mission_id=mission.id,
        task_key="explicit_assignment",
        employee_role=profile.role_type,
        title=payload.title.strip(),
        instructions=objective,
        input_context=payload.input_context,
        agent_profile_id=profile.id,
        model_id=payload.model_id,
        variant=payload.variant,
    )
    task.expected_output = payload.expected_output
    db.commit()
    background_tasks.add_task(
        _run_explicit_delegation, mission.id, task.id, profile.id, session_factory, runtime,
        resolved=(resolved_rt, resolved_provider, resolved_engine))
    return _mission_to_out(mission, [task])


@router.get("", response_model=List[MissionOut])
def list_missions(skip: int = 0, limit: int = 50, db: Session = Depends(get_db)):
    repo = MissionRepository(db)
    missions = repo.list(skip=skip, limit=limit)
    out: List[MissionOut] = []
    for m in missions:
        tasks = db.query(MissionTask).filter(MissionTask.mission_id == m.id).all()
        out.append(_mission_to_out(m, tasks))
    return out


@router.get("/{mission_id}", response_model=MissionOut)
def get_mission(mission_id: str, db: Session = Depends(get_db)):
    repo = MissionRepository(db)
    m = repo.get(mission_id)
    if not m:
        raise HTTPException(status_code=404, detail="Mission not found")
    tasks = db.query(MissionTask).filter(MissionTask.mission_id == mission_id).all()
    return _mission_to_out(m, tasks)


@router.post("/{mission_id}/cancel", response_model=MissionOut)
def cancel_mission_endpoint(mission_id: str, db: Session = Depends(get_db)):
    repo = MissionRepository(db)
    m = repo.get(mission_id)
    if not m:
        raise HTTPException(status_code=404, detail="Mission not found")
    if m.status in ("succeeded", "partially_succeeded", "failed", "cancelled"):
        tasks = db.query(MissionTask).filter(MissionTask.mission_id == mission_id).all()
        return _mission_to_out(m, tasks)
    # Atomic compare-and-set: mission + unfinished tasks flip in one transaction,
    # and only if the mission is still non-terminal (a concurrent completion wins
    # over a late cancel; a committed cancel wins over everything afterwards).
    if cancel_mission_rows(db, mission_id):
        # Also schedules persisted-run cancel + engine abort on the mission's loop.
        cancel_mission(mission_id)
    m = repo.get(mission_id)
    tasks = db.query(MissionTask).filter(MissionTask.mission_id == mission_id).all()
    return _mission_to_out(m, tasks)


@router.get("/{mission_id}/runs", response_model=List[RunOut])
def mission_runs(mission_id: str, db: Session = Depends(get_db)):
    if db.get(Mission, mission_id) is None:
        raise HTTPException(status_code=404, detail="Mission not found")
    runs = (db.query(ConversationRun).filter(ConversationRun.mission_id == mission_id)
            .order_by(ConversationRun.created_at, ConversationRun.id).limit(100).all())
    return [ConversationRunManager._snapshot(db, run) for run in runs]


@router.get("/{mission_id}/events")
async def mission_events(mission_id: str, db: Session = Depends(get_db)):
    repo = MissionRepository(db)
    m = repo.get(mission_id)
    if not m:
        raise HTTPException(status_code=404, detail="Mission not found")
    async def gen():
        q = event_hub.subscribe(mission_id)
        try:
            # Subscribe before reading the snapshot, so events arriving while
            # the snapshot is sent are buffered for the live stream.
            db.expire_all()
            current = repo.get(mission_id)
            if not current:
                return
            tasks = db.query(MissionTask).filter(MissionTask.mission_id == mission_id).all()
            for ev in _mission_replay_events(current, tasks):
                yield f"event: {ev['event']}\ndata: {json.dumps(ev['data'])}\n\n"
            while True:
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=15.0)
                    if ev["event"] == "stream.overflow":
                        break
                    yield f"event: {ev['event']}\ndata: {json.dumps(ev['data'])}\n\n"
                    # Terminal close handled by client; keep alive otherwise.
                except asyncio.TimeoutError:
                    yield "event: ping\ndata: {}\n\n"
        finally:
            event_hub.unsubscribe(mission_id, q)

    return StreamingResponse(gen(), media_type="text/event-stream")


def _mission_replay_events(m: Mission, tasks: List[MissionTask]) -> List[Dict[str, Any]]:
    """Snapshot events a fresh SSE client must receive BEFORE live broadcasts."""
    past: List[Dict[str, Any]] = [
        {"event": "mission.status.changed", "data": {"missionId": m.id, "status": m.status}}
    ]
    for t in tasks:
        past.append({"event": "task.status.changed",
                     "data": {"missionId": m.id, "taskKey": t.task_key, "status": t.status}})
    return past
