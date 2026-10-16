"""
Database Schema, Pragmas, Cascade Delete, and Repository Tests
"""

import pytest
from sqlalchemy.exc import IntegrityError
from backend.app.db.models import (
    Workflow,
    WorkflowExecution,
    NodeExecution,
    ExecutionEvent,
    AgentProfile,
)
from backend.app.db.repository import (
    WorkflowRepository,
    AgentProfileRepository,
    ExecutionRepository,
)


def test_foreign_key_enforcement(db_session):
    """
    Verifies PRAGMA foreign_keys=ON raises IntegrityError when referencing non-existent workflow.
    """
    exec_repo = ExecutionRepository(db_session)
    with pytest.raises(IntegrityError):
        exec_repo.create_execution(
            workflow_id="non-existent-wf-id",
            input_json="{}"
        )
    db_session.rollback()


def test_cascade_delete(db_session):
    """
    Verifies deleting a workflow cascades to executions, node executions, and events.
    """
    wf_repo = WorkflowRepository(db_session)
    exec_repo = ExecutionRepository(db_session)

    # Create workflow
    wf = wf_repo.create(name="Cascade Test Workflow", definition_json='{"nodes":[]}')

    # Create execution
    wf_exec = exec_repo.create_execution(workflow_id=wf.id, input_json='{"goal":"test"}')

    # Create node execution
    node_exec = exec_repo.record_node_execution({
        "execution_id": wf_exec.id,
        "node_id": "planner-1",
        "task_depth": "Standard",
        "status": "succeeded"
    })

    # Create execution event
    event = exec_repo.record_event(
        execution_id=wf_exec.id,
        node_execution_id=node_exec.id,
        event_type="node.status.changed",
        event_json='{"status":"succeeded"}'
    )

    # Verify rows exist
    assert db_session.query(Workflow).count() == 1
    assert db_session.query(WorkflowExecution).count() == 1
    assert db_session.query(NodeExecution).count() == 1
    assert db_session.query(ExecutionEvent).count() == 1

    # Delete workflow
    wf_repo.delete(wf.id)

    # Verify cascade delete removed everything
    assert db_session.query(Workflow).count() == 0
    assert db_session.query(WorkflowExecution).count() == 0
    assert db_session.query(NodeExecution).count() == 0
    assert db_session.query(ExecutionEvent).count() == 0


def test_workflow_repository_crud(db_session):
    """
    Tests full CRUD lifecycle of workflows.
    """
    repo = WorkflowRepository(db_session)

    # 1. Create
    wf = repo.create(name="Advising Flow", description="Student portal", definition_json='{"version":1}')
    assert wf.id is not None
    assert wf.version == 1
    assert wf.name == "Advising Flow"

    # 2. Get by ID
    fetched = repo.get_by_id(wf.id)
    assert fetched is not None
    assert fetched.name == "Advising Flow"

    # 3. Update
    updated = repo.update(wf.id, name="Updated Advising Flow", definition_json='{"version":2}')
    assert updated.name == "Updated Advising Flow"
    assert updated.version == 2

    # 4. List
    all_wfs = repo.list_all()
    assert len(all_wfs) == 1

    # 5. Delete
    deleted = repo.delete(wf.id)
    assert deleted is True
    assert repo.get_by_id(wf.id) is None


def test_agent_profiles_seeding(db_session):
    """
    Tests that 10 standard AI employee profiles are seeded into SQLite.
    """
    repo = AgentProfileRepository(db_session)
    seeded_count = repo.seed_defaults()
    assert seeded_count == 10

    profiles = repo.list_all()
    assert len(profiles) == 10

    roles = {p.role_type for p in profiles}
    expected_roles = {
        "planner", "researcher", "analyst", "reviewer", "data_extractor",
        "summarizer", "report_writer", "coder", "translator", "custom"
    }
    assert roles == expected_roles

    # Idempotency check: running seed again seeds 0 new profiles
    assert repo.seed_defaults() == 0
