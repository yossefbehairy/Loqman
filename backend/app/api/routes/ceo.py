"""
CEO control plane: ask (read-only reasoning loop) + pending-action approvals.

POST /api/ceo/ask            -> bounded tool loop, {reply, actions_proposed, iterations}
GET  /api/ceo/actions        -> pending action inbox (default status=pending)
POST /api/ceo/actions/{id}/approve|deny
    - require header X-AgentOS-Confirm: true (else 403): local UI confirmation,
      so external/unauthenticated callers cannot auto-approve proposals.
    - only pending actions transition; anything else is 409 (no double execution).
    - approve for update_contract rechecks expected_updated_at (optimistic
      concurrency): a newer contract edit wins, the proposal fails instead.
    - approve for new_profile / new_team creates profiles (a team is created
      atomically: full validation happens before the first write).
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import func, update
from sqlalchemy.orm import Session

from backend.app.config import settings
from backend.app.db.models import AgentProfile, CeoMessage, PendingAction
from backend.app.db.repository import AgentProfileRepository
from backend.app.db.session import get_db
from backend.app.engine.events import event_hub
from backend.app.orchestration.agent_loop import run_ceo_ask, run_ceo_fast
from backend.app.runtime import AgentRuntime, get_runtime

router = APIRouter(prefix="/api/ceo", tags=["ceo"])

CONFIRM_HEADER = "true"

# Conversation memory: how many recent turns (user+assistant pairs) feed the
# prompt, and where they are recorded. Kept on the BACKEND — the renderer
# never sends history, restarts change nothing.
CEO_HISTORY_TURNS = 8


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    model_id: Optional[str] = None
    variant: Optional[str] = None
    # Optional client-generated id: while the sync ask runs, the client can poll
    # GET /api/ceo/ask-progress/{request_id} to show a live stage in the UI.
    request_id: Optional[str] = Field(default=None, max_length=64)


class ProposedActionOut(BaseModel):
    id: str
    kind: str
    summary: str


class AskResponse(BaseModel):
    reply: str
    actions_proposed: List[ProposedActionOut] = Field(default_factory=list)
    iterations: int
    # True when the answer came from the single-call small-talk fast path
    # (no tool loop, no company data, plain text only).
    fast: bool = False


class CeoHistoryOut(BaseModel):
    id: str
    role: str
    content: str
    fast: bool
    created_at: str


class CreatedProfileOut(BaseModel):
    id: str
    name: str
    role_type: str


class AskProgressOut(BaseModel):
    stage: str
    detail: str = ""
    iteration: int = 0
    started_at: Optional[str] = None
    updated_at: Optional[str] = None
    done: bool = False


# Live progress registry for synchronous CEO asks. The desktop app is a single
# backend process and every client polls only while its own POST is in flight,
# so a small bounded dict is enough (no Redis, no background workers).
_ASK_PROGRESS: Dict[str, Dict[str, Any]] = {}
_ASK_PROGRESS_LIMIT = 100

# ---------------------------------------------------------------------------
# Fast-path classification: SMALL TALK ONLY, fail-closed to the deep loop.
# A question counts as small talk only when it is short, hits a known
# greeting/chit-chat pattern, and contains zero data-intent words. Anything
# unrecognized (or anything touching missions/team/mutations) goes to the
# ReAct loop, where the system prompt forbids inventing data.
# ---------------------------------------------------------------------------
_SMALLTALK_PATTERNS = (
    "صباح الخير", "مساء الخير", "اهلا", "أهلا", "هلا", "اهلاً", "أهلاً",
    "ازيك", "إزيك", "عامل ايه", "عاملة ايه", "عامل إيه", "عاملة إيه",
    "تمام", "شكرا", "شكراً", "شكرًا", "متشكر", "مرحبا", "سلام",
    "مين انت", "مين إنت", "انت مين", "إنت مين", "بتعمل ايه", "بتعمل إيه",
    "hello", "hi", "hey", "good morning", "good evening", "good night",
    "thanks", "thank you", "مرحبتين",
)
_DATA_INTENT_PATTERNS = (
    "مهمة", "مهمه", "مهام", "mission", "task", "فريق", "team",
    "موظف", "employee", "profile", "شخص", "تقرير", "report", "summary", "ملخص",
    "اعرض", "أعرض", "انشئ", "أنشئ", "انشاء", "أضف", "اضف", "جدول", "timestamp",
    "عدل", "عدّل", "تعديل", "احذف", "امسح", "approve", "موافقة", "موافقه",
    "حالة", "الحاله", "status", "بيانات", "data", "أعطني", "اعطني", "give me",
    "احكيلي عن", "ايه اللي", "إيه اللي", "القائم", "قائمة", "قائمتك",
)
_SMALLTALK_MAX_CHARS = 120


def _word_safe(pattern: str) -> str:
    """ASCII small-talk words must not match inside other words ('hi' in 'this')."""
    return rf"\b{re.escape(pattern)}\b" if pattern.isascii() else re.escape(pattern)


_SMALLTALK_RE = re.compile("|".join(_word_safe(p) for p in _SMALLTALK_PATTERNS), re.IGNORECASE)
_DATA_INTENT_RE = re.compile("|".join(re.escape(p) for p in _DATA_INTENT_PATTERNS))


def _is_smalltalk(question: str) -> bool:
    q = question.strip().lower()
    if not q or len(q) > _SMALLTALK_MAX_CHARS:
        return False
    if _DATA_INTENT_RE.search(q):
        return False
    return _SMALLTALK_RE.search(q) is not None


def _progress_write(request_id: str, stage: str, detail: str = "",
                    iteration: Optional[int] = None, done: bool = False) -> None:
    record = _ASK_PROGRESS.get(request_id)
    if record is None:
        return
    record["stage"] = stage
    record["detail"] = (detail or "")[:200]
    if iteration is not None:
        record["iteration"] = iteration
    record["done"] = done
    record["updated_at"] = datetime.now(timezone.utc).isoformat()


class ActionOut(BaseModel):
    id: str
    kind: str
    summary: str
    status: str
    created_by: Optional[str] = None
    decided_at: Optional[str] = None
    decision_note: Optional[str] = None
    # Populated on approval so the UI can materialize the executed hire/team
    # locally without a second lookup. Informational only: the DB is the source
    # of truth and list/deny/failed responses keep the empty defaults.
    created_profiles: List[CreatedProfileOut] = Field(default_factory=list)
    team_name: Optional[str] = None


def _action_to_out(
    a: PendingAction,
    created_profiles: Optional[List[CreatedProfileOut]] = None,
    team_name: Optional[str] = None,
) -> ActionOut:
    return ActionOut(
        id=a.id,
        kind=a.kind,
        summary=a.summary,
        status=a.status,
        created_by=a.created_by,
        decided_at=a.decided_at.isoformat() if a.decided_at else None,
        decision_note=a.decision_note,
        created_profiles=created_profiles or [],
        team_name=team_name,
    )


@router.post("/ask", response_model=AskResponse)
async def ceo_ask(
    payload: AskRequest,
    db: Session = Depends(get_db),
    runtime: AgentRuntime = Depends(get_runtime),
):
    question = (payload.question or "").strip()
    if not question:
        raise HTTPException(status_code=400, detail="Question must not be empty.")
    model_id = payload.model_id or settings.DEFAULT_MODEL_ID
    try:
        free_models = await runtime.list_available_models(free_only=True)
    except Exception as e:
        raise HTTPException(status_code=502, detail="OpenCode runtime unreachable.")
    target = next((m for m in free_models if m.id == model_id), None)
    if not target:
        available = [m.id for m in free_models[:20]]
        raise HTTPException(
            status_code=400,
            detail=f"Model '{model_id}' is not confirmed free/active. Available free models include: {available}",
        )
    if payload.variant and target.variants and payload.variant not in target.variants:
        raise HTTPException(
            status_code=400,
            detail=f"Variant '{payload.variant}' is not supported by model '{model_id}'.",
        )
    progress_id = (payload.request_id or "").strip()
    # Fast path: small talk -> exactly one plain-text call, no tool loop.
    is_fast = _is_smalltalk(question)
    fast_variant: Optional[str] = payload.variant
    if is_fast and fast_variant is None:
        # Cheap by intent: prefer the model's "low" reasoning effort for chit-chat.
        fast_variant = "low" if (target.variants and "low" in target.variants) else None
    if progress_id:
        now_iso = datetime.now(timezone.utc).isoformat()
        _ASK_PROGRESS[progress_id] = {
            "stage": "fast" if is_fast else "starting", "detail": "",
            "iteration": 1 if is_fast else 0,
            "started_at": now_iso, "updated_at": now_iso, "done": False,
        }
        # Bounded registry: drop the oldest entries first.
        if len(_ASK_PROGRESS) > _ASK_PROGRESS_LIMIT:
            for stale in list(_ASK_PROGRESS)[:-_ASK_PROGRESS_LIMIT]:
                _ASK_PROGRESS.pop(stale, None)

    def _report(stage: str, detail: str, iteration: int) -> None:
        if progress_id:
            _progress_write(progress_id, stage, detail, iteration)

    # Conversation memory (backend-owned): recent turns feed every prompt and
    # every exchange is recorded, so continuity survives asks AND app restarts.
    # The renderer NEVER sends history — this is the single source of truth.
    recent = (
        db.query(CeoMessage)
        .order_by(CeoMessage.seq.desc(), CeoMessage.id.desc())
        .limit(2 * CEO_HISTORY_TURNS)
        .all()
    )
    history = [{"role": m.role, "content": m.content} for m in reversed(recent)]
    next_seq = (db.query(func.max(CeoMessage.seq)).scalar() or 0) + 1
    record_cap = 12000
    db.add(CeoMessage(role="user", content=question[:record_cap], fast=is_fast, seq=next_seq))
    db.commit()

    try:
        if is_fast:
            result = await run_ceo_fast(
                question=question,
                model_id=model_id,
                provider_id=target.provider_id,
                variant=fast_variant,
                runtime=runtime,
                history=history,
            )
        else:
            result = await run_ceo_ask(
                question=question,
                model_id=model_id,
                provider_id=target.provider_id,
                variant=payload.variant,
                runtime=runtime,
                db=db,
                progress=_report if progress_id else None,
                history=history,
            )
    except Exception as e:
        if progress_id:
            _progress_write(progress_id, "error", "CEO ask failed.", done=True)
        raise HTTPException(status_code=502, detail="CEO ask failed.")
    db.add(
        CeoMessage(
            role="assistant",
            content=result["reply"][:record_cap],
            fast=is_fast,
            seq=next_seq + 1,
        )
    )
    db.commit()
    if progress_id:
        _progress_write(progress_id, "done", "", result["iterations"], done=True)
    return AskResponse(
        reply=result["reply"],
        actions_proposed=[ProposedActionOut(**a) for a in result["actions_proposed"]],
        iterations=result["iterations"],
        fast=is_fast,
    )


@router.get("/ask-progress/{request_id}", response_model=AskProgressOut)
def get_ask_progress(request_id: str):
    """Live stage of an in-flight (or just finished) CEO ask."""
    record = _ASK_PROGRESS.get(request_id.strip())
    if record is None:
        raise HTTPException(status_code=404, detail="Unknown ask request id.")
    return AskProgressOut(**record)


@router.get("/history", response_model=List[CeoHistoryOut])
def ceo_history(limit: int = 200, db: Session = Depends(get_db)):
    """Loqman's (the CEO's) ongoing conversation, oldest → last.

    The backend owns this memory: it survives asks, restarts and renderer
    reloads. The UI renders it; it never has to send history back.
    """
    limit = max(1, min(limit, 500))
    rows = (
        db.query(CeoMessage)
        .order_by(CeoMessage.seq.desc(), CeoMessage.id.desc())
        .limit(limit)
        .all()
    )
    return [
        CeoHistoryOut(
            id=r.id or "",
            role=r.role or "user",
            content=r.content or "",
            fast=bool(r.fast),
            created_at=(r.created_at.isoformat() if r.created_at else ""),
        )
        for r in reversed(rows)
    ]


@router.get("/actions", response_model=List[ActionOut])
def list_actions(status: str = "pending", db: Session = Depends(get_db)):
    if status not in ("pending", "approved", "denied", "failed"):
        raise HTTPException(status_code=400, detail="Unknown action status.")
    rows = (
        db.query(PendingAction)
        .filter(PendingAction.status == status)
        .order_by(PendingAction.created_at.desc())
        .all()
    )
    return [_action_to_out(a) for a in rows]


def _require_confirm(x_agentos_confirm: Optional[str]) -> None:
    if (x_agentos_confirm or "").lower() != CONFIRM_HEADER:
        raise HTTPException(
            status_code=403,
            detail="Deciding actions requires local UI confirmation "
                   "(header X-AgentOS-Confirm: true).",
        )


def _decide(action_id: str, approve: bool, note: Optional[str],
            db: Session, x_agentos_confirm: Optional[str]) -> ActionOut:
    _require_confirm(x_agentos_confirm)
    action = db.query(PendingAction).filter(PendingAction.id == action_id).first()
    if not action:
        raise HTTPException(status_code=404, detail=f"Action '{action_id}' not found.")
    # Claim inside the same transaction as the action's database changes.
    claimed = db.execute(update(PendingAction).where(
        PendingAction.id == action_id, PendingAction.status == "pending"
    ).values(status="executing").execution_options(synchronize_session=False))
    if not claimed.rowcount:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Action already decided or being processed.",
        )
    db.refresh(action)
    now = datetime.now(timezone.utc)
    if not approve:
        action.status = "denied"
        action.decided_at = now
        action.decision_note = note
        db.commit()
        db.refresh(action)
        return _action_to_out(action)

    # Approve: execute via the existing repositories (no duplicated validation).
    created_profiles: List[CreatedProfileOut] = []
    team_name: Optional[str] = None
    savepoint = db.begin_nested()
    try:
        payload = json.loads(action.payload_json or "{}")
        if action.kind == "update_contract":
            _apply_update_contract(db, payload)
        elif action.kind == "new_profile":
            prof = _apply_new_profile(db, payload)
            created_profiles = [CreatedProfileOut(
                id=prof.id, name=prof.name, role_type=prof.role_type)]
        elif action.kind == "new_team":
            rows, team_name = _apply_new_team(db, payload)
            created_profiles = [
                CreatedProfileOut(id=p.id, name=p.name, role_type=p.role_type)
                for p in rows
            ]
        else:
            raise ValueError(f"Unknown action kind '{action.kind}'.")
        savepoint.commit()
    except HTTPException:
        savepoint.rollback()
        db.rollback()
        raise
    except Exception as e:
        savepoint.rollback()
        action.status = "failed"
        action.decided_at = now
        message = str(e)
        if isinstance(e, ValueError) and "has unknown role" in message:
            action.decision_note = "Team member has unknown role."
        elif isinstance(e, ValueError) and "requires name and instructions" in message:
            action.decision_note = "Team member requires name and instructions."
        else:
            action.decision_note = "Action failed."
        db.commit()
        db.refresh(action)
        return _action_to_out(action)
    action.status = "approved"
    action.decided_at = now
    action.decision_note = note
    db.commit()
    db.refresh(action)
    return _action_to_out(action, created_profiles=created_profiles, team_name=team_name)


def _apply_update_contract(db: Session, payload: Dict[str, Any]) -> None:
    from backend.app.orchestration.ceo_tools import ALLOWED_CONTRACT_FIELDS

    repo = AgentProfileRepository(db)
    prof = repo.get_by_id(payload.get("profile_id", ""))
    if not prof:
        raise ValueError("Target profile no longer exists.")
    changes = {k: v for k, v in (payload.get("changes") or {}).items()
               if k in ALLOWED_CONTRACT_FIELDS}
    if not changes:
        raise ValueError("No applicable contract changes.")
    # Optimistic concurrency: a newer contract edit wins over this proposal.
    expected = payload.get("expected_updated_at")
    if expected and prof.updated_at:
        try:
            current = prof.updated_at
            if current.tzinfo is None:
                current = current.replace(tzinfo=timezone.utc)
            if current.isoformat() > str(expected):
                raise ValueError(
                    "Contract changed after this proposal (newer edit wins) — refusing overwrite.")
        except ValueError:
            raise
        except Exception:
            pass
    for key, value in changes.items():
        if value is not None:
            setattr(prof, key, value)
    prof.updated_at = datetime.now(timezone.utc)
    db.flush()


def _apply_new_profile(db: Session, payload: Dict[str, Any]) -> AgentProfile:
    from backend.app.orchestration.ceo_tools import KNOWN_ROLES  # noqa

    repo = AgentProfileRepository(db)
    role = str(payload.get("role_type", "")).lower()
    if role not in KNOWN_ROLES:
        raise ValueError(f"Unknown role '{role}'.")
    import uuid as _uuid
    data = {
        "id": f"custom-{_uuid.uuid4().hex[:8]}",
        "name": payload.get("name"),
        "role_type": role,
        "description": payload.get("description"),
        "instructions": payload.get("instructions"),
        "default_model_id": payload.get("default_model_id"),
        "default_variant_id": payload.get("default_variant_id"),
    }
    if not data["name"] or not data["instructions"]:
        raise ValueError("New profile requires name and instructions.")
    profile = AgentProfile(**data)
    db.add(profile)
    db.flush()
    return profile


def _apply_new_team(db: Session, payload: Dict[str, Any]) -> Tuple[List[AgentProfile], str]:
    """Create all team member profiles in one decision.

    Partial-failure semantics: the WHOLE batch is validated (team name,
    1..8 members, known role, name + instructions per member) BEFORE the
    first write, so invalid proposals fail with zero profiles created.
    If an unexpected DB error still hits midway, already-created members
    are deleted before re-raising — a failed team leaves no zombie rows.
    """
    from backend.app.orchestration.ceo_tools import KNOWN_ROLES  # noqa

    team_name = str(payload.get("name") or "").strip()
    if not team_name:
        raise ValueError("New team requires a name.")
    members = payload.get("members")
    if not isinstance(members, list) or not (1 <= len(members) <= 8):
        raise ValueError("New team requires 1..8 members.")
    planned: List[Dict[str, Any]] = []
    for i, member in enumerate(members, start=1):
        if not isinstance(member, dict):
            raise ValueError(f"Team member #{i} must be an object.")
        role = str(member.get("role_type", "")).strip().lower()
        if role not in KNOWN_ROLES:
            raise ValueError(f"Team member #{i} has unknown role '{member.get('role_type')}'.")
        name = str(member.get("name") or "").strip()
        if not name or not member.get("instructions"):
            raise ValueError(f"Team member #{i} requires name and instructions.")
        planned.append({
            "name": name,
            "role_type": role,
            "description": member.get("description"),
            "instructions": member.get("instructions"),
            "default_model_id": member.get("default_model_id"),
            "default_variant_id": member.get("default_variant_id"),
        })
    import uuid as _uuid
    created = []
    for member_data in planned:
        profile = AgentProfile(id=f"custom-{_uuid.uuid4().hex[:8]}", **member_data)
        db.add(profile)
        created.append(profile)
    db.flush()
    return created, team_name


@router.post("/actions/{action_id}/approve", response_model=ActionOut)
async def approve_action(
    action_id: str,
    db: Session = Depends(get_db),
    x_agentos_confirm: Optional[str] = Header(default=None),
):
    out = _decide(action_id, True, None, db, x_agentos_confirm)
    await event_hub.broadcast(action_id, "ceo.action.decided",
                              {"actionId": action_id, "status": out.status})
    return out


@router.post("/actions/{action_id}/deny", response_model=ActionOut)
async def deny_action(
    action_id: str,
    db: Session = Depends(get_db),
    x_agentos_confirm: Optional[str] = Header(default=None),
):
    out = _decide(action_id, False, None, db, x_agentos_confirm)
    await event_hub.broadcast(action_id, "ceo.action.decided",
                              {"actionId": action_id, "status": out.status})
    return out
