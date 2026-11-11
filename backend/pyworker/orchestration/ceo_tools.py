"""CEO tool registry (v1, closed set).

Read tools execute immediately inside the reasoning loop and return real
database excerpts. Write tools NEVER mutate: they insert exactly one row into
``pending_actions`` with status='pending' and return its id.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import timezone
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional

from backend.pyworker.core import missions as mission_repo
from backend.pyworker.core import profiles as profile_repo
from backend.pyworker.db.core import Database, iso_utc, new_id, ts
from backend.pyworker.orchestration.planner import KNOWN_ROLES

EXCERPT_CHARS = 1500


def _excerpt(text: Any, limit: int = EXCERPT_CHARS) -> str:
    s = text if isinstance(text, str) else json.dumps(text, ensure_ascii=False, default=str)
    s = s or ""
    return s if len(s) <= limit else s[:limit] + "…[truncated]"


# -- arg validators (raise ValueError) ---------------------------------------

def _s(d: dict, name: str, lo: int, hi: Optional[int]) -> str:
    v = d.get(name)
    if not isinstance(v, str) or len(v) < lo or (hi is not None and len(v) > hi):
        raise ValueError(name)
    return v


def _opt_s(d: dict, name: str) -> Optional[str]:
    v = d.get(name)
    if v is not None and not isinstance(v, str):
        raise ValueError(name)
    return v


def parse_list_missions(a: dict) -> SimpleNamespace:
    limit = a.get("limit", 5)
    if isinstance(limit, bool) or not isinstance(limit, int) or not (1 <= limit <= 20):
        raise ValueError("limit")
    return SimpleNamespace(status=_opt_s(a, "status"), limit=limit)


def parse_get_mission(a: dict) -> SimpleNamespace:
    return SimpleNamespace(mission_id=_s(a, "mission_id", 1, 36))


def parse_none(a: dict) -> SimpleNamespace:
    return SimpleNamespace()


def parse_get_profile(a: dict) -> SimpleNamespace:
    return SimpleNamespace(identifier=_s(a, "identifier", 1, 128))


ALLOWED_CONTRACT_FIELDS = {
    "name", "description", "instructions", "default_model_id",
    "default_variant_id", "task_depth", "settings_json",
}


def parse_update_contract(a: dict) -> SimpleNamespace:
    changes = a.get("changes")
    if not isinstance(changes, dict) or not changes:
        raise ValueError("changes")
    return SimpleNamespace(profile=_s(a, "profile", 1, 128), changes=changes)


def parse_new_profile(a: dict) -> SimpleNamespace:
    return SimpleNamespace(
        name=_s(a, "name", 1, 255), role=_s(a, "role", 1, 64), description=_opt_s(a, "description"),
        instructions=_s(a, "instructions", 1, None), default_model_id=_opt_s(a, "default_model_id"),
        default_variant_id=_opt_s(a, "default_variant_id"))


def _member(m: Any) -> SimpleNamespace:
    if not isinstance(m, dict):
        raise ValueError("member")
    return SimpleNamespace(
        name=_s(m, "name", 1, 255), role_type=_s(m, "role_type", 1, 64), description=_opt_s(m, "description"),
        instructions=_s(m, "instructions", 1, None), default_model_id=_opt_s(m, "default_model_id"),
        default_variant_id=_opt_s(m, "default_variant_id"))


def parse_new_team(a: dict) -> SimpleNamespace:
    members = a.get("members")
    if not isinstance(members, list) or not (1 <= len(members) <= 8):
        raise ValueError("members")
    return SimpleNamespace(name=_s(a, "name", 1, 255), members=[_member(m) for m in members])


# -- handlers (db, args) -> observation str -----------------------------------

def _handle_list_missions(db: Database, args) -> str:
    missions = mission_repo.list_missions(db, limit=args.limit * 2)
    if args.status:
        missions = [m for m in missions if m["status"] == args.status]
    missions = missions[:args.limit]
    if not missions:
        return "No missions found."
    lines = [f"- {m['id']} | {m['title']} | status={m['status']} | model={m['model_id'] or '?'}"
             for m in missions]
    return f"{len(missions)} mission(s):\n" + "\n".join(lines)


def _handle_get_mission(db: Database, args) -> str:
    mission = mission_repo.get(db, args.mission_id)
    if not mission:
        return f"Mission '{args.mission_id}' not found."
    lines = [f"Mission {mission['id']} | {mission['title']} | status={mission['status']}"]
    for t in mission_repo.tasks_for(db, mission["id"]):
        lines.append(f"- [{t['task_key']}] {t['employee_role']}: {t['status']}")
    if mission["final_summary"]:
        lines.append("Summary: " + _excerpt(mission["final_summary"]))
    return "\n".join(lines)


def _handle_list_profiles(db: Database, args) -> str:
    profiles = profile_repo.list_all(db)
    lines = [f"- {p['id']} | {p['name']} | role={p['role_type']} | model={p['default_model_id']} "
             f"| variant={p['default_variant_id']}" for p in profiles]
    return f"{len(lines)} profile(s):\n" + "\n".join(lines)


def _find_profile(db: Database, identifier: str):
    key = identifier.strip().lower()
    prof = profile_repo.get_by_id(db, identifier.strip()) or profile_repo.get_by_id(db, key)
    if not prof:
        for p in profile_repo.list_all(db):
            if (p["role_type"] or "").lower() == key or (p["name"] or "").lower() == key:
                return p
    return prof


def _handle_get_profile(db: Database, args) -> str:
    prof = _find_profile(db, args.identifier)
    if not prof:
        return f"Profile '{args.identifier}' not found."
    return (f"{prof['id']} | {prof['name']} | role={prof['role_type']} | model={prof['default_model_id']} "
            f"| variant={prof['default_variant_id']} | depth={prof['task_depth']}\n"
            f"Instructions: {_excerpt(prof['instructions'] or '')}")


def _record_action(db: Database, kind: str, payload: dict, summary: str) -> str:
    action_id = new_id()
    db.insert("pending_actions", {
        "id": action_id, "kind": kind, "payload_json": json.dumps(payload, ensure_ascii=False),
        "summary": summary, "status": "pending", "created_by": "ceo-loop", "created_at": ts()})
    return action_id


def _handle_propose_update_contract(db: Database, args) -> str:
    key = args.profile.strip()
    prof = profile_repo.get_by_id(db, key) or profile_repo.get_by_id(db, key.lower())
    if not prof:
        for p in profile_repo.list_all(db):
            if (p["role_type"] or "").lower() == key.lower():
                prof = p
                break
    if not prof:
        return f"ERROR: profile '{args.profile}' not found — proposal NOT recorded."
    unknown = [k for k in args.changes if k not in ALLOWED_CONTRACT_FIELDS]
    if unknown:
        return f"ERROR: unsupported contract fields {unknown} — proposal NOT recorded."
    payload = {"profile_id": prof["id"], "changes": args.changes,
               "expected_updated_at": iso_utc(prof["updated_at"])}
    action_id = _record_action(
        db, "update_contract", payload,
        f"Update {prof['name']}: " + ", ".join(f"{k}={v!r}" for k, v in args.changes.items()))
    return (f"Proposal recorded with id={action_id} and status=pending. "
            f"Do NOT call this tool again for the same change. "
            f"Tell the user the proposal awaits approval.")


def _handle_propose_new_profile(db: Database, args) -> str:
    role = args.role.strip().lower()
    if role not in KNOWN_ROLES:
        return f"ERROR: unknown role '{args.role}' — proposal NOT recorded."
    payload = {"name": args.name.strip(), "role_type": role, "description": args.description,
               "instructions": args.instructions, "default_model_id": args.default_model_id,
               "default_variant_id": args.default_variant_id}
    action_id = _record_action(db, "new_profile", payload,
                               f"New profile '{args.name.strip()}' with role={role}")
    return (f"Proposal recorded with id={action_id} and status=pending. "
            f"Do NOT call this tool again for the same profile. "
            f"Tell the user the proposal awaits approval.")


def _handle_propose_new_team(db: Database, args) -> str:
    """Record a whole-team proposal. Never creates profiles inside the loop."""
    unknown = sorted({m.role_type.strip().lower() for m in args.members} - KNOWN_ROLES)
    if unknown:
        return f"ERROR: unknown role(s) {unknown} — proposal NOT recorded."
    members = [{
        "name": m.name.strip(), "role_type": m.role_type.strip().lower(), "description": m.description,
        "instructions": m.instructions, "default_model_id": m.default_model_id,
        "default_variant_id": m.default_variant_id} for m in args.members]
    payload = {"name": args.name.strip(), "members": members}
    roles = ", ".join(m["role_type"] for m in members)
    action_id = _record_action(
        db, "new_team", payload,
        f"New team '{args.name.strip()}' with {len(members)} member(s): {roles}")
    return (f"Proposal recorded with id={action_id} and status=pending. "
            f"Do NOT call this tool again for the same team. "
            f"Tell the user the proposal awaits approval.")


@dataclass
class ToolSpec:
    name: str
    kind: str  # "read" | "write"
    parse: Callable[[dict], Any]
    handler: Callable[[Database, Any], str]
    description: str


TOOLS: Dict[str, ToolSpec] = {
    "list_missions": ToolSpec("list_missions", "read", parse_list_missions, _handle_list_missions,
                              "List recent missions, optionally filtered by status."),
    "get_mission": ToolSpec("get_mission", "read", parse_get_mission, _handle_get_mission,
                            "Mission details, task statuses, and summary excerpt."),
    "list_profiles": ToolSpec("list_profiles", "read", parse_none, _handle_list_profiles,
                              "All employee profiles with roles and models."),
    "get_profile": ToolSpec("get_profile", "read", parse_get_profile, _handle_get_profile,
                            "One profile by id, alias, or role."),
    "propose_update_contract": ToolSpec("propose_update_contract", "write", parse_update_contract,
                                        _handle_propose_update_contract,
                                        "PROPOSAL ONLY — never executes. Records a contract change for approval."),
    "propose_new_profile": ToolSpec("propose_new_profile", "write", parse_new_profile,
                                    _handle_propose_new_profile,
                                    "PROPOSAL ONLY — never executes. Records a new profile for approval."),
    "propose_new_team": ToolSpec("propose_new_team", "write", parse_new_team, _handle_propose_new_team,
                                 "PROPOSAL ONLY — never executes. Records a new team of 1-8 member "
                                 "profiles (name, role_type, instructions each) for approval."),
}


def tool_catalog_prompt() -> str:
    return "\n".join(f"- {spec.name} ({spec.kind}): {spec.description}" for spec in TOOLS.values())


def run_tool(db: Database, name: str, args: Dict[str, Any]) -> str:
    """Validate + dispatch one tool call. Returns the observation string."""
    spec = TOOLS.get(name)
    if spec is None:
        return f"ERROR: unknown tool '{name}'. Available: {sorted(TOOLS)}."
    try:
        parsed = spec.parse(args or {})
    except Exception:
        return f"ERROR: invalid args for '{name}'"
    try:
        return spec.handler(db, parsed)
    except Exception:
        return f"ERROR: tool '{name}' failed"


def pending_action_ids(db: Database) -> List[str]:
    return [a["id"] for a in db.all("SELECT id FROM pending_actions WHERE status='pending'")]
