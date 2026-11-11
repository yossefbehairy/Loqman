"""Mission + task persistence and API rendering (port of MissionRepository)."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from backend.pyworker.db.core import Database, iso, new_id, ts

MISSION_STATUSES = {"queued", "planning", "running", "succeeded", "partially_succeeded", "failed", "cancelled"}
TASK_STATUSES = {"queued", "running", "succeeded", "failed", "skipped", "cancelled"}


def get(db: Database, mission_id: str) -> Optional[Dict[str, Any]]:
    return db.one("SELECT * FROM missions WHERE id=?", (mission_id,))


def list_missions(db: Database, skip: int = 0, limit: int = 50) -> List[Dict[str, Any]]:
    return db.all("SELECT * FROM missions ORDER BY created_at DESC, rowid DESC LIMIT ? OFFSET ?", (limit, skip))


def tasks_for(db: Database, mission_id: str) -> List[Dict[str, Any]]:
    return db.all("SELECT * FROM mission_tasks WHERE mission_id=? ORDER BY rowid", (mission_id,))


def create_mission(db: Database, objective: str, title: Optional[str] = None, model_id: Optional[str] = None,
                   variant: Optional[str] = None, idempotency_key: Optional[str] = None,
                   request_fingerprint: Optional[str] = None,
                   source_conversation_id: Optional[str] = None,
                   status: str = "queued") -> Dict[str, Any]:
    if idempotency_key:
        existing = db.one("SELECT * FROM missions WHERE idempotency_key=?", (idempotency_key,))
        if existing:
            return existing
    now = ts()
    mid = new_id()
    db.insert("missions", {
        "id": mid, "title": title or objective[:80], "objective": objective, "status": status,
        "model_id": model_id, "variant": variant, "source_conversation_id": source_conversation_id,
        "idempotency_key": idempotency_key, "request_fingerprint": request_fingerprint,
        "created_at": now, "updated_at": now})
    return get(db, mid)


def create_task(db: Database, mission_id: str, task_key: str, employee_role: str, title: str,
                instructions: str, input_context: Optional[str] = None, depends_on: Optional[List[str]] = None,
                agent_profile_id: Optional[str] = None, model_id: Optional[str] = None,
                variant: Optional[str] = None, expected_output: Optional[str] = None) -> Dict[str, Any]:
    tid = new_id()
    db.insert("mission_tasks", {
        "id": tid, "mission_id": mission_id, "task_key": task_key, "employee_role": employee_role,
        "agent_profile_id": agent_profile_id, "title": title, "instructions": instructions,
        "input_context": input_context, "expected_output": expected_output,
        "depends_on_json": json.dumps(depends_on or []), "status": "queued", "model_id": model_id,
        "variant": variant, "attempt_count": 0})
    return db.one("SELECT * FROM mission_tasks WHERE id=?", (tid,))


def touch_mission(db: Database, mission_id: str) -> None:
    db.update("missions", {"updated_at": ts()}, "id=?", (mission_id,))


def _parse_json_safe(text: Optional[str]) -> Optional[Any]:
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        return text


def _iso(value: Optional[str]) -> Optional[str]:
    # Stored instants are naive UTC; the legacy API rendered them without an offset.
    return iso(value)


def task_out(t: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": t["id"], "task_key": t["task_key"], "employee_role": t["employee_role"], "title": t["title"],
        "status": t["status"], "model_id": t["model_id"], "variant": t["variant"],
        "attempt_count": t["attempt_count"] or 0, "output": _parse_json_safe(t["output_json"]),
        "error_message": t["error_message"], "duration_ms": t["duration_ms"], "session_id": t["session_id"],
        "started_at": _iso(t["started_at"]), "finished_at": _iso(t["finished_at"]),
    }


def mission_out(m: Dict[str, Any], tasks: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "id": m["id"], "title": m["title"], "objective": m["objective"], "status": m["status"],
        "model_id": m["model_id"], "variant": m["variant"], "plan": _parse_json_safe(m["plan_json"]),
        "final_summary": _parse_json_safe(m["final_summary"]), "error_summary": m["error_summary"],
        "tasks": [task_out(t) for t in tasks],
        "created_at": _iso(m["created_at"]), "started_at": _iso(m["started_at"]),
        "finished_at": _iso(m["finished_at"]),
    }
