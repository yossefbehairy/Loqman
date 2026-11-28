"""Startup crash-recovery policy: dangling missions become failed.

Never fakes success and never re-executes anything.
"""

from __future__ import annotations

import logging

from backend.pyworker.db.core import Database, ts

logger = logging.getLogger("agentos.lifecycle")

CRASH_RECOVERY_ERROR = "Execution interrupted due to server restart or shutdown"
MISSION_NON_TERMINAL = ("queued", "planning", "running")


def _marks(values) -> str:
    return ",".join("?" for _ in values)


def run_startup_crash_recovery(db: Database) -> dict:
    now = ts()
    stats = {"recovered_missions": 0, "recovered_mission_tasks": 0}
    with db.tx():
        stats["recovered_missions"] = db.update(
            "missions", {"status": "failed", "finished_at": now, "error_summary": CRASH_RECOVERY_ERROR},
            f"status IN ({_marks(MISSION_NON_TERMINAL)})", MISSION_NON_TERMINAL)
        stats["recovered_mission_tasks"] = db.update(
            "mission_tasks", {"status": "failed", "finished_at": now, "error_message": CRASH_RECOVERY_ERROR},
            "status IN ('queued','running')")
    if stats["recovered_missions"] or stats["recovered_mission_tasks"]:
        logger.warning("[CRASH_RECOVERY] recovered %s mission(s) and %s task(s)",
                       stats["recovered_missions"], stats["recovered_mission_tasks"])
    else:
        logger.info("[CRASH_RECOVERY] clean: zero dangling missions found.")
    return stats
