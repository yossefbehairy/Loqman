"""Startup crash-recovery policy: dangling executions/missions become failed.

Never fakes success and never re-executes anything.
"""

from __future__ import annotations

import json
import logging

from backend.pyworker.db.core import Database, new_id, ts

logger = logging.getLogger("agentos.lifecycle")

CRASH_RECOVERY_ERROR = "Execution interrupted due to server restart or shutdown"
NON_TERMINAL_STATES = ("running", "ready", "pending")
MISSION_NON_TERMINAL = ("queued", "planning", "running")


def _marks(values) -> str:
    return ",".join("?" for _ in values)


def run_startup_crash_recovery(db: Database) -> dict:
    now = ts()
    stats = {"recovered_workflows": 0, "recovered_nodes": 0,
             "recovered_missions": 0, "recovered_mission_tasks": 0}
    with db.tx():
        dangling = db.all(
            f"SELECT id, status FROM workflow_executions WHERE status IN ({_marks(NON_TERMINAL_STATES)})",
            NON_TERMINAL_STATES)
        for wf in dangling:
            db.update("workflow_executions",
                      {"status": "failed", "finished_at": now, "error_summary": CRASH_RECOVERY_ERROR},
                      "id=?", (wf["id"],))
            db.insert("execution_events", {
                "id": new_id(), "execution_id": wf["id"], "node_execution_id": None,
                "event_type": "execution.interrupted",
                "event_json": json.dumps({"prior_status": wf["status"], "reason": CRASH_RECOVERY_ERROR}),
                "created_at": now})
            stats["recovered_workflows"] += 1
        stats["recovered_nodes"] = db.update(
            "node_executions",
            {"status": "failed", "finished_at": now, "error_message": CRASH_RECOVERY_ERROR},
            f"status IN ({_marks(NON_TERMINAL_STATES)})", NON_TERMINAL_STATES)
        stats["recovered_missions"] = db.update(
            "missions", {"status": "failed", "finished_at": now, "error_summary": CRASH_RECOVERY_ERROR},
            f"status IN ({_marks(MISSION_NON_TERMINAL)})", MISSION_NON_TERMINAL)
        stats["recovered_mission_tasks"] = db.update(
            "mission_tasks", {"status": "failed", "finished_at": now, "error_message": CRASH_RECOVERY_ERROR},
            "status IN ('queued','running')")
    if stats["recovered_workflows"] or stats["recovered_nodes"]:
        logger.warning("[CRASH_RECOVERY] recovered %s workflow(s) and %s node(s)",
                       stats["recovered_workflows"], stats["recovered_nodes"])
    else:
        logger.info("[CRASH_RECOVERY] clean: zero dangling executions found.")
    return stats
