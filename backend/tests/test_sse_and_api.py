"""
API Integration Tests for Validate, Run, Cancel, and SSE Stream Endpoints
"""

import json


def test_validate_workflow_api(client):
    # 1. Create a valid workflow
    definition = {
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
    create_res = client.post("/api/workflows", json={
        "name": "API Validation Test",
        "definition_json": json.dumps(definition)
    })
    wf_id = create_res.json()["id"]

    # 2. Call validate endpoint
    val_res = client.post(f"/api/workflows/{wf_id}/validate")
    assert val_res.status_code == 200
    data = val_res.json()
    assert data["valid"] is True
    assert data["topological_order"] == ["start-1", "planner-1", "output-1"]


def test_run_workflow_sync_api(client):
    # 1. Create workflow
    definition = {
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
    create_res = client.post("/api/workflows", json={
        "name": "API Sync Run Test",
        "definition_json": json.dumps(definition)
    })
    wf_id = create_res.json()["id"]

    # 2. Call run endpoint with sync=True
    run_res = client.post(f"/api/workflows/{wf_id}/run", json={"input": "Test goal", "sync": True})
    assert run_res.status_code == 200
    run_data = run_res.json()
    assert run_data["status"] == "succeeded"
    assert run_data["execution_id"] is not None
    assert run_data["result"] is not None


def test_cancel_execution_api(client, db_session):
    from backend.app.db.repository import WorkflowRepository, ExecutionRepository
    wf_repo = WorkflowRepository(db_session)
    exec_repo = ExecutionRepository(db_session)

    # 1. Create a dummy workflow
    wf = wf_repo.create(name="Cancel API Test", definition_json='{"nodes":[],"edges":[]}')

    # 2. Create pending execution in DB
    wf_exec = exec_repo.create_execution(workflow_id=wf.id)
    exec_repo.record_node_execution({
        "execution_id": wf_exec.id,
        "node_id": "planner-1",
        "status": "running"
    })

    # 3. Call cancel
    cancel_res = client.post(f"/api/executions/{wf_exec.id}/cancel")
    assert cancel_res.status_code == 200
    data = cancel_res.json()
    assert data["status"] == "cancelled"

    # 4. Verify DB updated
    db_session.refresh(wf_exec)
    assert wf_exec.status == "cancelled"


def test_sse_events_stream_api(client):
    # 1. Create workflow and run synchronously to populate SQLite events
    definition = {
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
    create_res = client.post("/api/workflows", json={
        "name": "SSE Stream Test",
        "definition_json": json.dumps(definition)
    })
    wf_id = create_res.json()["id"]

    run_res = client.post(f"/api/workflows/{wf_id}/run", json={"sync": True})
    exec_id = run_res.json()["execution_id"]

    # 2. Test GET /api/executions/{id}
    details_res = client.get(f"/api/executions/{exec_id}")
    assert details_res.status_code == 200
    details = details_res.json()
    assert details["status"] == "succeeded"
    assert len(details["node_executions"]) == 3
