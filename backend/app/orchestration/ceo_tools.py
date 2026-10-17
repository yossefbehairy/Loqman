"""
CEO tool registry (v1, closed set).

Read tools execute immediately inside the reasoning loop and return real
database excerpts. Write tools NEVER mutate: they insert exactly one row into
pending_actions with status='pending' and return its id.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import timezone
from typing import Any, Callable, Dict, List, Optional

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.app.db.models import Mission, MissionTask, PendingAction
from backend.app.db.repository import AgentProfileRepository, MissionRepository
from backend.app.orchestration.planner import KNOWN_ROLES

EXCERPT_CHARS = 1500


def _excerpt(text: Any, limit: int = EXCERPT_CHARS) -> str:
    s = text if isinstance(text, str) else json.dumps(text, ensure_ascii=False, default=str)
    s = s or ""
    return s if len(s) <= limit else s[:limit] + "…[truncated]"


# -- arg schemas ------------------------------------------------------------

class ListMissionsArgs(BaseModel):
    status: Optional[str] = None
    limit: int = Field(default=5, ge=1, le=20)


class GetMissionArgs(BaseModel):
    mission_id: str = Field(min_length=1, max_length=36)


class ListProfilesArgs(BaseModel):
    pass


class GetProfileArgs(BaseModel):
    identifier: str = Field(min_length=1, max_length=128)


ALLOWED_CONTRACT_FIELDS = {
    "name", "description", "instructions", "default_model_id",
    "default_variant_id", "task_depth", "settings_json",
}


class ProposeUpdateContractArgs(BaseModel):
    profile: str = Field(min_length=1, max_length=128)
    changes: Dict[str, Any] = Field(min_length=1)


class ProposeNewProfileArgs(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    role: str = Field(min_length=1, max_length=64)
    description: Optional[str] = None
    instructions: str = Field(min_length=1)
    default_model_id: Optional[str] = None
    default_variant_id: Optional[str] = None


class TeamMemberArgs(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    role_type: str = Field(min_length=1, max_length=64)
    description: Optional[str] = None
    instructions: str = Field(min_length=1)
    default_model_id: Optional[str] = None
    default_variant_id: Optional[str] = None


class ProposeNewTeamArgs(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    members: List[TeamMemberArgs] = Field(min_length=1, max_length=8)


# -- handlers (db: Session, args) -> observation str -------------------------

def _handle_list_missions(db: Session, args: ListMissionsArgs) -> str:
    repo = MissionRepository(db)
    missions = repo.list(limit=args.limit * 2)
    if args.status:
        missions = [m for m in missions if m.status == args.status]
    missions = missions[:args.limit]
    if not missions:
        return "No missions found."
    lines = [f"- {m.id} | {m.title} | status={m.status} | model={m.model_id or '?'}"
             for m in missions]
    return f"{len(missions)} mission(s):\n" + "\n".join(lines)


def _handle_get_mission(db: Session, args: GetMissionArgs) -> str:
    repo = MissionRepository(db)
    mission = repo.get(args.mission_id)
    if not mission:
        return f"Mission '{args.mission_id}' not found."
    tasks = db.query(MissionTask).filter(MissionTask.mission_id == mission.id).all()
    lines = [f"Mission {mission.id} | {mission.title} | status={mission.status}"]
    for t in tasks:
        lines.append(f"- [{t.task_key}] {t.employee_role}: {t.status}")
    if mission.final_summary:
        lines.append("Summary: " + _excerpt(mission.final_summary))
    return "\n".join(lines)


def _handle_list_profiles(db: Session, args: ListProfilesArgs) -> str:
    profiles = AgentProfileRepository(db).list_all()
    lines = [f"- {p.id} | {p.name} | role={p.role_type} | model={p.default_model_id} "
             f"| variant={p.default_variant_id}" for p in profiles]
    return f"{len(lines)} profile(s):\n" + "\n".join(lines)


def _handle_get_profile(db: Session, args: GetProfileArgs) -> str:
    repo = AgentProfileRepository(db)
    key = args.identifier.strip().lower()
    prof = repo.get_by_id(args.identifier.strip()) or repo.get_by_id(key)
    if not prof:
        for p in repo.list_all():
            if (p.role_type or "").lower() == key or (p.name or "").lower() == key:
                prof = p
                break
    if not prof:
        return f"Profile '{args.identifier}' not found."
    return (f"{prof.id} | {prof.name} | role={prof.role_type} | model={prof.default_model_id} "
            f"| variant={prof.default_variant_id} | depth={prof.task_depth}\n"
            f"Instructions: {_excerpt(prof.instructions or '')}")


def _handle_propose_update_contract(db: Session, args: ProposeUpdateContractArgs) -> str:
    repo = AgentProfileRepository(db)
    key = args.profile.strip()
    prof = repo.get_by_id(key) or repo.get_by_id(key.lower())
    if not prof:
        for p in repo.list_all():
            if (p.role_type or "").lower() == key.lower():
                prof = p
                break
    if not prof:
        return f"ERROR: profile '{args.profile}' not found — proposal NOT recorded."
    unknown = [k for k in args.changes if k not in ALLOWED_CONTRACT_FIELDS]
    if unknown:
        return f"ERROR: unsupported contract fields {unknown} — proposal NOT recorded."
    expected_ts = prof.updated_at
    if expected_ts is not None and expected_ts.tzinfo is None:
        expected_ts = expected_ts.replace(tzinfo=timezone.utc)
    payload = {
        "profile_id": prof.id,
        "changes": args.changes,
        "expected_updated_at": expected_ts.isoformat() if expected_ts else None,
    }
    action = PendingAction(
        kind="update_contract",
        payload_json=json.dumps(payload, ensure_ascii=False),
        summary=f"Update {prof.name}: " + ", ".join(f"{k}={v!r}" for k, v in args.changes.items()),
        status="pending",
        created_by="ceo-loop",
    )
    db.add(action)
    db.commit()
    db.refresh(action)
    return (f"Proposal recorded with id={action.id} and status=pending. "
            f"Do NOT call this tool again for the same change. "
            f"Tell the user the proposal awaits approval.")


def _handle_propose_new_profile(db: Session, args: ProposeNewProfileArgs) -> str:
    role = args.role.strip().lower()
    if role not in KNOWN_ROLES:
        return f"ERROR: unknown role '{args.role}' — proposal NOT recorded."
    payload = {
        "name": args.name.strip(),
        "role_type": role,
        "description": args.description,
        "instructions": args.instructions,
        "default_model_id": args.default_model_id,
        "default_variant_id": args.default_variant_id,
    }
    action = PendingAction(
        kind="new_profile",
        payload_json=json.dumps(payload, ensure_ascii=False),
        summary=f"New profile '{args.name.strip()}' with role={role}",
        status="pending",
        created_by="ceo-loop",
    )
    db.add(action)
    db.commit()
    db.refresh(action)
    return (f"Proposal recorded with id={action.id} and status=pending. "
            f"Do NOT call this tool again for the same profile. "
            f"Tell the user the proposal awaits approval.")


def _handle_propose_new_team(db: Session, args: ProposeNewTeamArgs) -> str:
    """Record a whole-team proposal. Never creates profiles inside the loop."""
    unknown = sorted({m.role_type.strip().lower() for m in args.members} - KNOWN_ROLES)
    if unknown:
        return f"ERROR: unknown role(s) {unknown} — proposal NOT recorded."
    members = [
        {
            "name": m.name.strip(),
            "role_type": m.role_type.strip().lower(),
            "description": m.description,
            "instructions": m.instructions,
            "default_model_id": m.default_model_id,
            "default_variant_id": m.default_variant_id,
        }
        for m in args.members
    ]
    payload = {"name": args.name.strip(), "members": members}
    roles = ", ".join(m["role_type"] for m in members)
    action = PendingAction(
        kind="new_team",
        payload_json=json.dumps(payload, ensure_ascii=False),
        summary=f"New team '{args.name.strip()}' with {len(members)} member(s): {roles}",
        status="pending",
        created_by="ceo-loop",
    )
    db.add(action)
    db.commit()
    db.refresh(action)
    return (f"Proposal recorded with id={action.id} and status=pending. "
            f"Do NOT call this tool again for the same team. "
            f"Tell the user the proposal awaits approval.")


@dataclass
class ToolSpec:
    name: str
    kind: str  # "read" | "write"
    args_model: type
    handler: Callable[[Session, Any], str]
    description: str


TOOLS: Dict[str, ToolSpec] = {
    "list_missions": ToolSpec("list_missions", "read", ListMissionsArgs, _handle_list_missions,
                              "List recent missions, optionally filtered by status."),
    "get_mission": ToolSpec("get_mission", "read", GetMissionArgs, _handle_get_mission,
                            "Mission details, task statuses, and summary excerpt."),
    "list_profiles": ToolSpec("list_profiles", "read", ListProfilesArgs, _handle_list_profiles,
                              "All employee profiles with roles and models."),
    "get_profile": ToolSpec("get_profile", "read", GetProfileArgs, _handle_get_profile,
                            "One profile by id, alias, or role."),
    "propose_update_contract": ToolSpec("propose_update_contract", "write", ProposeUpdateContractArgs,
                                        _handle_propose_update_contract,
                                        "PROPOSAL ONLY — never executes. Records a contract change for approval."),
    "propose_new_profile": ToolSpec("propose_new_profile", "write", ProposeNewProfileArgs,
                                    _handle_propose_new_profile,
                                    "PROPOSAL ONLY — never executes. Records a new profile for approval."),
    "propose_new_team": ToolSpec("propose_new_team", "write", ProposeNewTeamArgs,
                                 _handle_propose_new_team,
                                 "PROPOSAL ONLY — never executes. Records a new team of 1-8 member "
                                 "profiles (name, role_type, instructions each) for approval."),
}


def tool_catalog_prompt() -> str:
    lines = []
    for spec in TOOLS.values():
        lines.append(f"- {spec.name} ({spec.kind}): {spec.description}")
    return "\n".join(lines)


def run_tool(db: Session, name: str, args: Dict[str, Any]) -> str:
    """Validate + dispatch one tool call. Returns the observation string."""
    spec = TOOLS.get(name)
    if spec is None:
        return f"ERROR: unknown tool '{name}'. Available: {sorted(TOOLS)}."
    try:
        parsed = spec.args_model(**(args or {}))
    except Exception as e:
        return f"ERROR: invalid args for '{name}': {str(e)[:300]}"
    try:
        return spec.handler(db, parsed)
    except Exception as e:
        return f"ERROR: tool '{name}' failed: {str(e)[:300]}"


def pending_action_ids(db: Session) -> List[str]:
    return [a.id for a in db.query(PendingAction).filter(PendingAction.status == "pending").all()]
