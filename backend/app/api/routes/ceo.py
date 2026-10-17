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
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.app.config import settings
from backend.app.db.models import AgentProfile, PendingAction
from backend.app.db.repository import AgentProfileRepository
from backend.app.db.session import get_db
from backend.app.engine.events import event_hub
from backend.app.orchestration.agent_loop import run_ceo_ask
from backend.app.runtime import AgentRuntime, get_runtime

router = APIRouter(prefix="/api/ceo", tags=["ceo"])

CONFIRM_HEADER = "true"


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    model_id: Optional[str] = None
    variant: Optional[str] = None


class ProposedActionOut(BaseModel):
    id: str
    kind: str
    summary: str


class AskResponse(BaseModel):
    reply: str
    actions_proposed: List[ProposedActionOut] = Field(default_factory=list)
    iterations: int


class CreatedProfileOut(BaseModel):
    id: str
    name: str
    role_type: str


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
        raise HTTPException(status_code=502, detail=f"OpenCode runtime unreachable: {str(e)[:300]}")
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
    try:
        result = await run_ceo_ask(
            question=question,
            model_id=model_id,
            provider_id=target.provider_id,
            variant=payload.variant,
            runtime=runtime,
            db=db,
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"CEO ask failed: {str(e)[:300]}")
    return AskResponse(
        reply=result["reply"],
        actions_proposed=[ProposedActionOut(**a) for a in result["actions_proposed"]],
        iterations=result["iterations"],
    )


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
    # Single-transition guard: only pending actions move (no double execution).
    if action.status != "pending":
        raise HTTPException(
            status_code=409,
            detail=f"Action already decided (status={action.status}).",
        )
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
    except HTTPException:
        raise
    except Exception as e:
        action.status = "failed"
        action.decided_at = now
        action.decision_note = str(e)[:500]
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
    repo.update(prof.id, **changes)


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
    return repo.create(data)


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
    repo = AgentProfileRepository(db)
    created = []
    try:
        for member_data in planned:
            created.append(repo.create({
                "id": f"custom-{_uuid.uuid4().hex[:8]}",
                **member_data,
            }))
    except Exception:
        # Best-effort cleanup: a failed team must not leave partial members.
        for prof in created:
            db.delete(prof)
        db.commit()
        raise
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
