"""
Startup Crash Recovery Policy Tests
Verifies that any dangling non-terminal states ('running', 'ready', 'pending')
are deterministically converted to 'failed' upon server start.
"""

from backend.app.db.models import Workflow, WorkflowExecution, NodeExecution, ExecutionEvent
from backend.app.core.lifecycle import run_startup_crash_recovery, CRASH_RECOVERY_ERROR


def test_startup_crash_recovery(db_session):
    # 1. Create a base workflow
    wf = Workflow(name="Test Workflow", definition_json="{}")
    db_session.add(wf)
    db_session.commit()

    # 2. Create executions in various states
    # Non-terminal states (should be marked failed)
    e_running = WorkflowExecution(workflow_id=wf.id, status="running")
    e_ready = WorkflowExecution(workflow_id=wf.id, status="ready")
    e_pending = WorkflowExecution(workflow_id=wf.id, status="pending")
    # Terminal states (should remain untouched)
    e_succeeded = WorkflowExecution(workflow_id=wf.id, status="succeeded")
    e_failed = WorkflowExecution(workflow_id=wf.id, status="failed", error_summary="Prior failure")

    db_session.add_all([e_running, e_ready, e_pending, e_succeeded, e_failed])
    db_session.commit()

    # 3. Add node executions in various states
    n_running = NodeExecution(execution_id=e_running.id, node_id="node-1", status="running")
    n_pending = NodeExecution(execution_id=e_ready.id, node_id="node-2", status="pending")
    n_succeeded = NodeExecution(execution_id=e_succeeded.id, node_id="node-3", status="succeeded")

    db_session.add_all([n_running, n_pending, n_succeeded])
    db_session.commit()

    # 4. Run Startup Crash Recovery
    stats = run_startup_crash_recovery(db_session)

    assert stats["recovered_workflows"] == 3
    assert stats["recovered_nodes"] == 2

    # 5. Verify database states
    db_session.refresh(e_running)
    db_session.refresh(e_ready)
    db_session.refresh(e_pending)
    db_session.refresh(e_succeeded)
    db_session.refresh(e_failed)

    assert e_running.status == "failed"
    assert e_running.error_summary == CRASH_RECOVERY_ERROR
    assert e_running.finished_at is not None

    assert e_ready.status == "failed"
    assert e_ready.error_summary == CRASH_RECOVERY_ERROR

    assert e_pending.status == "failed"
    assert e_pending.error_summary == CRASH_RECOVERY_ERROR

    # Terminal states remain intact
    assert e_succeeded.status == "succeeded"
    assert e_failed.status == "failed"
    assert e_failed.error_summary == "Prior failure"

    # Check node executions
    db_session.refresh(n_running)
    db_session.refresh(n_pending)
    db_session.refresh(n_succeeded)

    assert n_running.status == "failed"
    assert n_running.error_message == CRASH_RECOVERY_ERROR
    assert n_pending.status == "failed"
    assert n_succeeded.status == "succeeded"

    # Check interrupted events were logged
    events = db_session.query(ExecutionEvent).filter(ExecutionEvent.event_type == "execution.interrupted").all()
    assert len(events) == 3
