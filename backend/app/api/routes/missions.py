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
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.app.config import settings
from backend.app.db.models import Mission, MissionTask
from backend.app.db.repository import AgentProfileRepository, MissionRepository
from backend.app.db.session import SessionLocal, get_db
from backend.app.engine.events import event_hub
from backend.app.orchestration.ceo import (
    cancel_mission,
    idempotency_key,
    run_mission_background,
)
from backend.app.runtime import get_runtime, AgentRuntime
from backend.app.runtime.base import ExecuteAgentTask

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


async def _run_explicit_delegation(mission_id: str, task_id: str, profile_id: str,
                                   session_factory=None, runtime=None):
    from backend.app.runtime import get_runtime as _gr

    factory = session_factory or SessionLocal
    rt = runtime or _gr()
    db = factory()
    try:
        mission = db.query(Mission).filter_by(id=mission_id).first()
        task = db.query(MissionTask).filter_by(id=task_id, mission_id=mission_id).first()
        profile = AgentProfileRepository(db).get_by_id(profile_id)
        if not mission or not task or not profile:
            return
        now = datetime.now(timezone.utc)
        mission.status = "running"
        mission.started_at = mission.started_at or now
        task.status = "running"
        task.started_at = now
        task.attempt_count = 1
        db.commit()
        prompt = (
            f"You are the {profile.role_type} employee ({profile.name}).\n"
            f"Role instructions: {profile.instructions}\n\n"
            f"Assignment: {task.title}\n{task.instructions}\n"
            f"Input/context: {task.input_context or '(none supplied)'}\n"
            f"Expected deliverable: {task.expected_output or '(use a clear, useful format)'}\n"
            "Complete the assignment and return the actual deliverable directly."
        )
        result = await rt.execute_task(ExecuteAgentTask(
            prompt=prompt,
            model_id=task.model_id or settings.DEFAULT_MODEL_ID,
            provider_id="opencode",
            variant=task.variant,
            session_title=f"agentos-delegated-{mission.id[:8]}",
            timeout=settings.MISSION_TASK_TIMEOUT,
        ))
        task.output_json = json.dumps({
            "result": result.text,
            "model": result.model_id,
            "variant": result.variant_id,
            "session_id": result.session_id,
            "duration_ms": result.duration_ms,
        }, ensure_ascii=False)
        task.session_id = result.session_id
        task.duration_ms = result.duration_ms
        task.model_id = result.model_id
        task.status = "succeeded"
        task.finished_at = datetime.now(timezone.utc)
        mission.status = "succeeded"
        mission.final_summary = json.dumps({"result": result.text}, ensure_ascii=False)
        mission.finished_at = task.finished_at
        db.commit()
    except Exception as exc:
        db.rollback()
        mission = db.query(Mission).filter_by(id=mission_id).first()
        task = db.query(MissionTask).filter_by(id=task_id, mission_id=mission_id).first()
        error = str(exc)[:800]
        now = datetime.now(timezone.utc)
        if task:
            task.status = "failed"
            task.error_message = error
            task.finished_at = now
        if mission:
            mission.status = "failed"
            mission.error_summary = error
            mission.finished_at = now
        db.commit()
    finally:
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
        "You are the General Manager (المدير العام) — a warm, natural conversationalist.\n"
        f"The user just said: {objective.strip()}\n"
        "Reply once, naturally and briefly, in the user's own language and dialect "
        "(Egyptian Arabic if they write Egyptian Arabic). "
        "Plain text only — no JSON, no mention of missions, tasks, or employees."
    )
    t0 = _time.time()
    try:
        result = await runtime.execute_task(ExecuteAgentTask(
            prompt=prompt,
            model_id=model_id,
            provider_id=provider_id,
            variant=variant,
            session_title="greeting-reply",
            timeout=90,
        ))
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
        raise HTTPException(status_code=502, detail=f"Greeting reply failed: {str(e)[:300]}")
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

    # Revalidate model BEFORE persisting (free-only, no paid fallback).
    model_id = payload.model_id or settings.DEFAULT_MODEL_ID
    try:
        free_models = await runtime.list_available_models(free_only=True)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"OpenCode runtime unreachable: {str(e)[:300]}")
    target = next((m for m in free_models if m.id == model_id), None)
    if not target:
        available = [m.id for m in free_models[:20]]
        raise HTTPException(
            status_code=400,
            detail=f"Model '{model_id}' is not confirmed free/active. Available free models include: {available}",
        )
    # Validate requested variant against the live catalog (truthful reasoning selector).
    if payload.variant and target.variants and payload.variant not in target.variants:
        raise HTTPException(
            status_code=400,
            detail=f"Variant '{payload.variant}' is not supported by model '{model_id}'. Supported: {target.variants}",
        )
    if not target.variants:
        # Model exposes no configurable reasoning parameter — ignore selector silently is lying;
        # we keep the request but the worker records variant 'default'.
        pass

    # Greetings get a single natural reply — never the employee pipeline.
    if is_greeting_objective(objective):
        return await _create_greeting_mission(
            objective=objective, model_id=model_id, provider_id=target.provider_id,
            variant=payload.variant, title=payload.title, db=db, runtime=runtime,
        )

    # Duplicate protection: same objective+roles+model within 10 minutes returns existing.
    key = idempotency_key(objective, roles, model_id)
    repo = MissionRepository(db)
    # Only dedupe recent queued/running missions with the same key.
    existing = None
    if key:
        from backend.app.db.models import Mission as _M
        from datetime import timedelta
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=10)
        existing = (
            db.query(_M)
            .filter(_M.idempotency_key == key, _M.status.in_(("queued", "planning", "running")))
            .filter(_M.created_at >= cutoff)
            .first()
        )
    if existing:
        tasks = db.query(MissionTask).filter(MissionTask.mission_id == existing.id).all()
        return _mission_to_out(existing, tasks)

    mission = repo.create_mission(
        objective=objective,
        title=payload.title or objective[:80],
        model_id=model_id,
        variant=payload.variant,
        idempotency_key=key,
    )
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
    models = await runtime.list_available_models(free_only=True)
    model = next((m for m in models if m.id == payload.model_id), None)
    if not model:
        raise HTTPException(status_code=400, detail="Selected model is not verified as free and active.")
    if payload.variant and model.variants and payload.variant not in model.variants:
        raise HTTPException(status_code=400, detail="Selected reasoning variant is not supported by this model.")

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
    background_tasks.add_task(_run_explicit_delegation, mission.id, task.id, profile.id, session_factory, runtime)
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
    cancel_mission(mission_id)
    m.status = "cancelled"
    m.finished_at = datetime.now(timezone.utc)
    for t in db.query(MissionTask).filter(MissionTask.mission_id == mission_id).all():
        if t.status in ("queued", "running"):
            t.status = "cancelled"
            t.finished_at = t.finished_at or datetime.now(timezone.utc)
    db.commit()
    tasks = db.query(MissionTask).filter(MissionTask.mission_id == mission_id).all()
    return _mission_to_out(m, tasks)


@router.get("/{mission_id}/events")
async def mission_events(mission_id: str, db: Session = Depends(get_db)):
    repo = MissionRepository(db)
    m = repo.get(mission_id)
    if not m:
        raise HTTPException(status_code=404, detail="Mission not found")
    tasks = db.query(MissionTask).filter(MissionTask.mission_id == mission_id).all()

    past: List[Dict[str, Any]] = [
        {"event": "mission.status.changed", "data": json.dumps({"missionId": m.id, "status": m.status})}
    ]
    for t in tasks:
        past.append({"event": "task.status.changed",
                     "data": json.dumps({"missionId": m.id, "taskKey": t.task_key, "status": t.status})})

    async def gen():
        for ev in past:
            yield f"event: {ev['event']}\ndata: {ev['data']}\n\n"
        q = event_hub.subscribe(mission_id)
        try:
            while True:
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=15.0)
                    yield f"event: {ev['event']}\ndata: {json.dumps(ev['data'])}\n\n"
                    if ev["event"] in ("mission.status.changed",) and ev["data"].get("status") in (
                        "succeeded", "partially_succeeded", "failed", "cancelled"):
                        # Give the client a beat, then close only on terminal broadcast.
                        # (Task-level events keep streaming until mission terminal.)
                        pass
                    # Terminal close handled by client; keep alive otherwise.
                except asyncio.TimeoutError:
                    yield "event: ping\ndata: {}\n\n"
        finally:
            event_hub.unsubscribe(mission_id, q)

    return StreamingResponse(gen(), media_type="text/event-stream")
