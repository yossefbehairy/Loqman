"""Agent profile persistence, default-org seeding and additive upgrades.

Port of the legacy ``AgentProfileRepository`` onto plain sqlite3 rows (dicts).
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from backend.pyworker.db.core import Database, iso, ts
from backend.pyworker.db.org_defaults import (
    CEO_ENGLISH_NAME_RULE,
    DEFAULT_EMPLOYEE_PROFILES,
    LEGACY_GM_INSTRUCTIONS,
    LEGACY_GM_NAMES,
    LEGACY_ORG_NAMES,
    LEGACY_ORG_TEAM_NAMES,
    englishify_legacy_text,
)

PROFILE_ALIASES = {
    "dr_sara": "planner",
    "eng_tarek": "analyst",
    "prof_yasmin": "reviewer",
    "general_manager": "custom",
    "loqman": "custom",
    "ceo": "custom",
    "ahmed": "coder",
    "khaled": "researcher",
    "layla": "data_extractor",
    "omar": "summarizer",
    "nour": "report_writer",
    "mona": "translator",
}

PROFILE_ENGINES = ("opencode", "claude-code", "codex", "antigravity")
PROFILE_COLUMNS = (
    "id", "name", "role_type", "description", "instructions", "input_schema_json",
    "output_schema_json", "default_model_id", "default_variant_id", "task_depth",
    "settings_json", "engine",
)
DEFAULT_MODEL = "muse-spark-1.3-contributor-free"


def profile_out(row: Dict[str, Any]) -> Dict[str, Any]:
    out = {k: row.get(k) for k in PROFILE_COLUMNS}
    out["created_at"] = iso(row["created_at"])
    out["updated_at"] = iso(row["updated_at"])
    return out


def get_by_id(db: Database, profile_id: str) -> Optional[Dict[str, Any]]:
    row = db.one("SELECT * FROM agent_profiles WHERE id=?", (profile_id,))
    if row is None and profile_id in PROFILE_ALIASES:
        row = db.one("SELECT * FROM agent_profiles WHERE id=?", (PROFILE_ALIASES[profile_id],))
    return row


def list_all(db: Database) -> List[Dict[str, Any]]:
    return db.all("SELECT * FROM agent_profiles ORDER BY name ASC")


def create(db: Database, data: Dict[str, Any]) -> Dict[str, Any]:
    now = ts()
    row = {
        "id": data["id"], "name": data["name"], "role_type": data["role_type"],
        "description": data.get("description"), "instructions": data["instructions"],
        "input_schema_json": data.get("input_schema_json"),
        "output_schema_json": data.get("output_schema_json"),
        "default_model_id": data.get("default_model_id") or DEFAULT_MODEL,
        "default_variant_id": data.get("default_variant_id"),
        "task_depth": data.get("task_depth") or "Standard",
        "settings_json": data.get("settings_json"),
        "engine": data.get("engine"),
        "created_at": now, "updated_at": now,
    }
    db.insert("agent_profiles", row)
    return db.one("SELECT * FROM agent_profiles WHERE id=?", (row["id"],))


def update(db: Database, profile_id: str, changes: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Set provided keys; None values are ignored except for ``engine``."""
    profile = get_by_id(db, profile_id)
    if profile is None:
        return None
    values = {k: v for k, v in changes.items()
              if k in PROFILE_COLUMNS and k != "id" and (v is not None or k == "engine")}
    values["updated_at"] = ts()
    db.update("agent_profiles", values, "id=?", (profile["id"],))
    return db.one("SELECT * FROM agent_profiles WHERE id=?", (profile["id"],))


def delete(db: Database, profile_id: str) -> None:
    db.execute("DELETE FROM agent_profiles WHERE id=?", (profile_id,))


# -- seeding / upgrades ---------------------------------------------------

def _read_settings(profile: Dict[str, Any]) -> Dict[str, Any]:
    try:
        data = json.loads(profile.get("settings_json") or "{}")
    except Exception:
        data = {}
    return data if isinstance(data, dict) else {}


def _merge_settings(profile: Dict[str, Any], default_settings: Dict[str, Any],
                    force: Optional[Dict[str, Any]] = None) -> bool:
    """Additive merge: missing keys filled, user values win."""
    current = _read_settings(profile)
    before = json.dumps(current, sort_keys=True, ensure_ascii=False)
    for key, value in default_settings.items():
        current.setdefault(key, value)
    if force:
        current.update(force)
    if json.dumps(current, sort_keys=True, ensure_ascii=False) == before:
        return False
    profile["settings_json"] = json.dumps(current, ensure_ascii=False)
    return True


def _englishify_settings(value: Any) -> Any:
    if isinstance(value, str):
        return englishify_legacy_text(value)
    if isinstance(value, list):
        return [_englishify_settings(item) for item in value]
    if isinstance(value, dict):
        return {k: (v if k == "arabic_name" else _englishify_settings(v)) for k, v in value.items()}
    return value


def _englishify_profile(profile: Dict[str, Any]) -> bool:
    changed = False
    instructions = profile.get("instructions") or ""
    if (profile["id"] == "custom" and instructions.startswith("You are Loqman")
            and "Display names in English" not in instructions):
        profile["instructions"] = instructions.rstrip() + "\n\n" + CEO_ENGLISH_NAME_RULE
        changed = True
    current = _read_settings(profile)
    shipped_name = (profile.get("name") or "").strip() in LEGACY_ORG_NAMES
    shipped_team = current.get("team_name") in LEGACY_ORG_TEAM_NAMES
    if not (shipped_name or shipped_team):
        return changed
    name = englishify_legacy_text(profile.get("name") or "")
    if name != (profile.get("name") or ""):
        profile["name"] = name
        changed = True
    upgraded_instructions = englishify_legacy_text(profile.get("instructions") or "")
    if upgraded_instructions != (profile.get("instructions") or ""):
        profile["instructions"] = upgraded_instructions
        changed = True
    upgraded = _englishify_settings(current)
    if upgraded != current:
        profile["settings_json"] = json.dumps(upgraded, ensure_ascii=False)
        changed = True
    return changed


def _upgrade_existing_org(db: Database, profiles: List[Dict[str, Any]]) -> None:
    by_id = {p["id"]: p for p in profiles}
    for profile_data in DEFAULT_EMPLOYEE_PROFILES:
        profile = by_id.get(profile_data["id"])
        if profile is None:
            continue
        before = dict(profile)
        default_settings = json.loads(profile_data["settings_json"])
        force: Dict[str, Any] = {}
        changed = False

        if profile_data["id"] == "custom":
            current = _read_settings(profile)
            instructions = (profile.get("instructions") or "").strip()
            untouched_ceo = (
                (profile.get("name") or "").strip() in LEGACY_GM_NAMES
                or current.get("arabic_name") in (None, "", "المدير العام")
                or instructions == LEGACY_GM_INSTRUCTIONS.strip()
                or instructions.startswith("You are the General Manager")
            )
            if untouched_ceo:
                profile["name"] = profile_data["name"]
                profile["description"] = profile_data["description"]
                profile["instructions"] = profile_data["instructions"]
                force = {k: default_settings[k] for k in ("department", "badge", "arabic_name", "title")}
                changed = True

        if _merge_settings(profile, default_settings, force):
            changed = True
        if _englishify_profile(profile):
            changed = True
        if changed or profile != before:
            profile["updated_at"] = ts()
            db.update(
                "agent_profiles",
                {k: profile[k] for k in ("name", "description", "instructions", "settings_json", "updated_at")},
                "id=?", (profile["id"],),
            )


def seed_defaults(db: Database) -> int:
    """Seed the Loqman org exactly once, only into an empty table; otherwise
    upgrade existing catalogs additively. Deleted profiles are never revived."""
    existing = list_all(db)
    if existing:
        with db.tx():
            _upgrade_existing_org(db, existing)
        return 0
    now = ts()
    with db.tx():
        for data in DEFAULT_EMPLOYEE_PROFILES:
            db.insert("agent_profiles", {
                "id": data["id"], "name": data["name"], "role_type": data["role_type"],
                "description": data.get("description"), "instructions": data["instructions"],
                "input_schema_json": data.get("input_schema_json"),
                "output_schema_json": data.get("output_schema_json"),
                "default_model_id": data.get("default_model_id") or DEFAULT_MODEL,
                "default_variant_id": data.get("default_variant_id"),
                "task_depth": data.get("task_depth") or "Standard",
                "settings_json": data.get("settings_json"),
                "engine": data.get("engine"),
                "created_at": now, "updated_at": now,
            })
    return len(DEFAULT_EMPLOYEE_PROFILES)
