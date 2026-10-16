"""
AgentOS Lifecycle & Startup Crash Recovery Policy
Enforces deterministic recovery of non-terminal executions upon server boot.
"""

import logging
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from backend.app.db.models import WorkflowExecution, NodeExecution, ExecutionEvent, Mission, MissionTask

logger = logging.getLogger("agentos.lifecycle")

CRASH_RECOVERY_ERROR = "Execution interrupted due to server restart or shutdown"
NON_TERMINAL_STATES = ["running", "ready", "pending"]
MISSION_NON_TERMINAL = ["queued", "planning", "running"]


def run_startup_crash_recovery(db: Session) -> dict:
    """
    Scans workflow_executions and node_executions for any dangling executions left
    in non-terminal states ('running', 'ready', 'pending') from a prior interrupted
    process, and immediately transitions them to 'failed' with explicit explanation.
    """
    now = datetime.now(timezone.utc)
    recovered_stats = {
        "recovered_workflows": 0,
        "recovered_nodes": 0
    }

    try:
        # 1. Recover dangling workflow executions
        dangling_workflows = db.query(WorkflowExecution).filter(
            WorkflowExecution.status.in_(NON_TERMINAL_STATES)
        ).all()

        for wf_exec in dangling_workflows:
            prior_status = wf_exec.status
            wf_exec.status = "failed"
            wf_exec.finished_at = now
            wf_exec.error_summary = CRASH_RECOVERY_ERROR
            
            # Log event
            event = ExecutionEvent(
                execution_id=wf_exec.id,
                event_type="execution.interrupted",
                event_json=f'{{"prior_status": "{prior_status}", "reason": "{CRASH_RECOVERY_ERROR}"}}',
                created_at=now
            )
            db.add(event)
            recovered_stats["recovered_workflows"] += 1

        # 2. Recover dangling node executions
        dangling_nodes = db.query(NodeExecution).filter(
            NodeExecution.status.in_(NON_TERMINAL_STATES)
        ).all()

        for node_exec in dangling_nodes:
            node_exec.status = "failed"
            node_exec.finished_at = now
            node_exec.error_message = CRASH_RECOVERY_ERROR
            recovered_stats["recovered_nodes"] += 1

        # 3. Recover dangling CEO missions + tasks (explicit failure, never fake success)
        recovered_stats["recovered_missions"] = 0
        recovered_stats["recovered_mission_tasks"] = 0
        try:
            dangling_missions = db.query(Mission).filter(
                Mission.status.in_(MISSION_NON_TERMINAL)
            ).all()
            for m in dangling_missions:
                m.status = "failed"
                m.finished_at = now
                m.error_summary = CRASH_RECOVERY_ERROR
                recovered_stats["recovered_missions"] += 1
            dangling_tasks = db.query(MissionTask).filter(
                MissionTask.status.in_(("queued", "running"))
            ).all()
            for t in dangling_tasks:
                t.status = "failed"
                t.finished_at = now
                t.error_message = CRASH_RECOVERY_ERROR
                recovered_stats["recovered_mission_tasks"] += 1
        except Exception as me:
            # Missions tables may not exist on very old DBs before migration-less create_all.
            logger.warning(f"[CRASH_RECOVERY] missions recovery skipped: {me}")

        db.commit()

        if recovered_stats["recovered_workflows"] > 0 or recovered_stats["recovered_nodes"] > 0:
            logger.warning(
                f"[CRASH_RECOVERY] Recovered {recovered_stats['recovered_workflows']} dangling workflow(s) "
                f"and {recovered_stats['recovered_nodes']} dangling node(s) marked as 'failed'."
            )
        else:
            logger.info("[CRASH_RECOVERY] Database check clean: zero dangling executions found.")

        return recovered_stats

    except Exception as e:
        db.rollback()
        logger.error(f"[CRASH_RECOVERY] Failed to run crash recovery: {e}")
        raise
