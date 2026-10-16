"""
Deterministic Executor End-to-End Tests
Tests multi-agent chain (Start -> Planner -> Analyst -> Reviewer -> Output),
multi-source context, zero-cost revalidation failure, and cancellation.
"""

import pytest
import json
from backend.app.db.models import Workflow, WorkflowExecution, NodeExecution
from backend.app.db.repository import ExecutionRepository
from backend.app.engine.executor import execute_workflow, cancel_running_execution
from backend.app.runtime.mock_runtime import MockRuntime


@pytest.mark.asyncio
async def test_full_pipeline_execution(db_session):
    """
    Executes: Start -> Planner -> Analyst -> Reviewer -> Output
    """
    runtime = MockRuntime()
    exec_repo = ExecutionRepository(db_session)

    # 1. Create workflow in DB
    wf_definition = {
        "nodes": [
            {"id": "start-1", "type": "start"},
            {"id": "planner-1", "type": "agent", "data": {"role_type": "planner", "model_id": "muse-spark-1.3-contributor-free", "task_depth": "Standard"}},
            {"id": "analyst-1", "type": "agent", "data": {"role_type": "analyst", "model_id": "muse-spark-1.3-contributor-free", "task_depth": "Deep"}},
            {"id": "reviewer-1", "type": "agent", "data": {"role_type": "reviewer", "model_id": "muse-spark-1.3-contributor-free", "task_depth": "Deep"}},
            {"id": "output-1", "type": "output"}
        ],
        "edges": [
            {"id": "e1", "source": "start-1", "target": "planner-1"},
            {"id": "e2", "source": "planner-1", "target": "analyst-1"},
            {"id": "e3", "source": "analyst-1", "target": "reviewer-1"},
            {"id": "e4", "source": "planner-1", "target": "reviewer-1"},  # Multi-source edge to reviewer
            {"id": "e5", "source": "reviewer-1", "target": "output-1"}
        ]
    }

    wf = Workflow(
        name="Academic Portal Multi-Agent Pipeline",
        definition_json=json.dumps(wf_definition)
    )
    db_session.add(wf)
    db_session.commit()

    # 2. Create execution record
    wf_exec = exec_repo.create_execution(
        workflow_id=wf.id,
        input_json=json.dumps({"goal": "Deploy AI student registration"}),
        workflow_snapshot_json=wf.definition_json
    )

    # 3. Execute workflow
    result = await execute_workflow(
        execution_id=wf_exec.id,
        db=db_session,
        runtime=runtime,
        workflow_definition=wf_definition,
        initial_input={"goal": "Deploy AI student registration"}
    )

    assert result["status"] == "succeeded"
    assert "final_output" in result

    # 4. Verify DB records
    db_session.refresh(wf_exec)
    assert wf_exec.status == "succeeded"
    assert wf_exec.final_output_json is not None
    assert wf_exec.started_at is not None
    assert wf_exec.finished_at is not None

    # Verify all 5 nodes succeeded
    node_records = db_session.query(NodeExecution).filter(NodeExecution.execution_id == wf_exec.id).all()
    assert len(node_records) == 5
    for n in node_records:
        assert n.status == "succeeded"
        assert n.output_json is not None
        assert n.duration_ms >= 0


@pytest.mark.asyncio
async def test_zero_cost_revalidation_failure(db_session):
    """
    Verifies that if an AI Agent node specifies a non-free or unavailable model,
    execution immediately aborts with status='failed'.
    """
    runtime = MockRuntime()
    exec_repo = ExecutionRepository(db_session)

    # Workflow with a paid / unknown model
    wf_definition = {
        "nodes": [
            {"id": "start-1", "type": "start"},
            {"id": "agent-1", "type": "agent", "data": {"role_type": "planner", "model_id": "paid-gpt-4o-super-expensive"}},
            {"id": "output-1", "type": "output"}
        ],
        "edges": [
            {"id": "e1", "source": "start-1", "target": "agent-1"},
            {"id": "e2", "source": "agent-1", "target": "output-1"}
        ]
    }

    wf = Workflow(name="Paid Model Test", definition_json=json.dumps(wf_definition))
    db_session.add(wf)
    db_session.commit()

    wf_exec = exec_repo.create_execution(workflow_id=wf.id, workflow_snapshot_json=wf.definition_json)

    result = await execute_workflow(
        execution_id=wf_exec.id,
        db=db_session,
        runtime=runtime,
        workflow_definition=wf_definition
    )

    assert result["status"] == "failed"
    assert "Zero-cost revalidation failed" in result["error"]

    db_session.refresh(wf_exec)
    assert wf_exec.status == "failed"
    assert "Zero-cost revalidation failed" in wf_exec.error_summary


@pytest.mark.asyncio
async def test_execution_cancellation(db_session):
    """
    Verifies that signaling cancellation aborts execution cleanly.
    """
    runtime = MockRuntime()
    exec_repo = ExecutionRepository(db_session)

    wf_definition = {
        "nodes": [
            {"id": "start-1", "type": "start"},
            {"id": "planner-1", "type": "agent", "data": {"role_type": "planner", "model_id": "muse-spark-1.3-contributor-free"}},
            {"id": "output-1", "type": "output"}
        ],
        "edges": [
            {"id": "e1", "source": "start-1", "target": "planner-1"},
            {"id": "e2", "source": "planner-1", "target": "output-1"}
        ]
    }

    wf = Workflow(name="Cancel Test", definition_json=json.dumps(wf_definition))
    db_session.add(wf)
    db_session.commit()

    wf_exec = exec_repo.create_execution(workflow_id=wf.id, workflow_snapshot_json=wf.definition_json)

    # Cancel before execution
    cancel_running_execution(wf_exec.id)

    result = await execute_workflow(
        execution_id=wf_exec.id,
        db=db_session,
        runtime=runtime,
        workflow_definition=wf_definition
    )

    assert result["status"] == "cancelled"
    db_session.refresh(wf_exec)
    assert wf_exec.status == "cancelled"
