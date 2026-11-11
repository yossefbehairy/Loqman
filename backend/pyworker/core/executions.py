"""Workflow + execution persistence (port of Workflow/ExecutionRepository)."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from backend.pyworker.db.core import Database, iso, new_id, ts


# -- workflows --------------------------------------------------------------

def workflow_out(row: Dict[str, Any]) -> Dict[str, Any]:
    return {"id": row["id"], "name": row["name"], "description": row["description"],
            "definition_json": row["definition_json"], "version": row["version"],
            "created_at": iso(row["created_at"]), "updated_at": iso(row["updated_at"])}


def workflow_get(db: Database, workflow_id: str) -> Optional[Dict[str, Any]]:
    return db.one("SELECT * FROM workflows WHERE id=?", (workflow_id,))


def workflow_list(db: Database, skip: int = 0, limit: int = 100) -> List[Dict[str, Any]]:
    return db.all("SELECT * FROM workflows ORDER BY updated_at DESC LIMIT ? OFFSET ?", (limit, skip))


def workflow_create(db: Database, name: str, description: Optional[str], definition_json: str) -> Dict[str, Any]:
    now = ts()
    wid = new_id()
    db.insert("workflows", {"id": wid, "name": name, "description": description,
                            "definition_json": definition_json, "version": 1,
                            "created_at": now, "updated_at": now})
    return workflow_get(db, wid)


def workflow_update(db: Database, workflow_id: str, name: Optional[str], description: Optional[str],
                    definition_json: Optional[str]) -> Optional[Dict[str, Any]]:
    wf = workflow_get(db, workflow_id)
    if wf is None:
        return None
    values: Dict[str, Any] = {"version": wf["version"] + 1, "updated_at": ts()}
    if name is not None:
        values["name"] = name
    if description is not None:
        values["description"] = description
    if definition_json is not None:
        values["definition_json"] = definition_json
    db.update("workflows", values, "id=?", (workflow_id,))
    return workflow_get(db, workflow_id)


def workflow_delete(db: Database, workflow_id: str) -> bool:
    if workflow_get(db, workflow_id) is None:
        return False
    with db.tx():
        # ON DELETE CASCADE is declared for executions; explicit for clarity/safety.
        db.execute("DELETE FROM workflows WHERE id=?", (workflow_id,))
    return True


# -- executions -------------------------------------------------------------

def node_out(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": row["id"], "execution_id": row["execution_id"], "node_id": row["node_id"],
        "role_type": row["role_type"], "model_id": row["model_id"],
        "native_variant_id": row["native_variant_id"], "task_depth": row["task_depth"],
        "status": row["status"], "attempt_count": row["attempt_count"],
        "input_json": row["input_json"], "output_json": row["output_json"],
        "error_message": row["error_message"], "started_at": iso(row["started_at"]),
        "finished_at": iso(row["finished_at"]), "duration_ms": row["duration_ms"],
    }


def event_out(row: Dict[str, Any]) -> Dict[str, Any]:
    return {"id": row["id"], "execution_id": row["execution_id"],
            "node_execution_id": row["node_execution_id"], "event_type": row["event_type"],
            "event_json": row["event_json"], "created_at": iso(row["created_at"])}


def execution_get(db: Database, execution_id: str) -> Optional[Dict[str, Any]]:
    return db.one("SELECT * FROM workflow_executions WHERE id=?", (execution_id,))


def execution_nodes(db: Database, execution_id: str) -> List[Dict[str, Any]]:
    return db.all("SELECT * FROM node_executions WHERE execution_id=? ORDER BY rowid", (execution_id,))


def execution_events(db: Database, execution_id: str) -> List[Dict[str, Any]]:
    return db.all("SELECT * FROM execution_events WHERE execution_id=? ORDER BY created_at, rowid",
                  (execution_id,))


def execution_out(db: Database, row: Dict[str, Any], detail: bool = True) -> Dict[str, Any]:
    out = {
        "id": row["id"], "workflow_id": row["workflow_id"], "status": row["status"],
        "input_json": row["input_json"], "final_output_json": row["final_output_json"],
        "workflow_snapshot_json": row["workflow_snapshot_json"], "runtime_status": row["runtime_status"],
        "started_at": iso(row["started_at"]), "finished_at": iso(row["finished_at"]),
        "error_summary": row["error_summary"],
        "node_executions": [], "events": [],
    }
    if detail:
        out["node_executions"] = [node_out(n) for n in execution_nodes(db, row["id"])]
        out["events"] = [event_out(e) for e in execution_events(db, row["id"])]
    return out


def create_execution(db: Database, workflow_id: str, input_json: Optional[str],
                     workflow_snapshot_json: Optional[str]) -> Dict[str, Any]:
    eid = new_id()
    db.insert("workflow_executions", {
        "id": eid, "workflow_id": workflow_id, "status": "pending", "input_json": input_json,
        "workflow_snapshot_json": workflow_snapshot_json, "started_at": ts()})
    return execution_get(db, eid)


def list_executions(db: Database, skip: int = 0, limit: int = 50) -> List[Dict[str, Any]]:
    return db.all("SELECT * FROM workflow_executions ORDER BY started_at DESC LIMIT ? OFFSET ?", (limit, skip))


def record_node_execution(db: Database, data: Dict[str, Any]) -> Dict[str, Any]:
    row = {"id": new_id(), "execution_id": data["execution_id"], "node_id": data["node_id"],
           "role_type": data.get("role_type"), "model_id": data.get("model_id"),
           "native_variant_id": data.get("native_variant_id"),
           "task_depth": data.get("task_depth") or "Standard",
           "status": data.get("status") or "pending", "attempt_count": 0}
    db.insert("node_executions", row)
    return db.one("SELECT * FROM node_executions WHERE id=?", (row["id"],))


def team_messages(db: Database, execution_id: str) -> List[Dict[str, Any]]:
    """Persistent team-chat history ordered by turn."""
    parsed = []
    for e in execution_events(db, execution_id):
        if e["event_type"] != "team.message":
            continue
        try:
            payload = json.loads(e["event_json"])
            if not isinstance(payload, dict):
                continue
        except Exception:
            continue
        try:
            turn = int(payload.get("turn", 0))
        except Exception:
            turn = 0
        parsed.append((turn, str(e["created_at"]), e, payload))
    parsed.sort(key=lambda t: (t[0], t[1]))
    return [{
        "execution_id": execution_id, "turn": int(p.get("turn", 0)) if str(p.get("turn", 0)).lstrip("-").isdigit() else 0,
        "from_node_id": str(p.get("fromNodeId", "")), "from_display_name": str(p.get("fromDisplayName", "")),
        "from_role": str(p.get("fromRole", "")), "to_node_ids": list(p.get("toNodeIds", []) or []),
        "excerpt": str(p.get("excerpt", "")), "full_output_ref": str(p.get("fullOutputRef", "")),
        "timestamp": str(p.get("timestamp", "")), "created_at": iso(e["created_at"]),
    } for _t, _c, e, p in parsed]
