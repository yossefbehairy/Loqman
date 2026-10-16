"""
FastAPI REST API Integration Tests
Tests all Phase 1 endpoints using FastAPI TestClient with MockRuntime.
"""

def test_api_root(client):
    res = client.get("/")
    assert res.status_code == 200
    data = res.json()
    assert data["app"] == "AgentOS"
    assert data["status"] == "online"


def test_api_health(client):
    res = client.get("/api/health")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ok"


def test_api_runtime_health(client):
    res = client.get("/api/runtime/health")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ok"
    assert data["default_model_status"] == "available"


def test_api_free_models(client):
    res = client.get("/api/models/free")
    assert res.status_code == 200
    models = res.json()
    assert len(models) > 0
    for m in models:
        assert m["is_free"] is True
        assert m["input_cost"] == 0.0
        assert m["output_cost"] == 0.0


def test_api_agent_profiles(client):
    # Retrieve profiles (seeded automatically or via repo)
    res = client.get("/api/agent-profiles")
    assert res.status_code == 200
    profiles = res.json()
    # At least 10 default roles
    assert len(profiles) >= 10
    role_names = [p["role_type"] for p in profiles]
    assert "planner" in role_names
    assert "analyst" in role_names
    assert "reviewer" in role_names

    # Test creating custom profile
    custom_payload = {
        "id": "custom-advisor",
        "name": "Academic Advisor",
        "role_type": "custom",
        "description": "Advises students on major track requirements",
        "instructions": "Help students pick courses",
        "task_depth": "Standard"
    }
    create_res = client.post("/api/agent-profiles", json=custom_payload)
    assert create_res.status_code == 201
    assert create_res.json()["name"] == "Academic Advisor"


def test_api_workflows_crud(client):
    # 1. Create workflow
    payload = {
        "name": "Demo Multi-Agent Workflow",
        "description": "Planner -> Analyst -> Reviewer -> Output",
        "definition_json": '{"nodes":[{"id":"start","type":"start"}],"edges":[]}'
    }
    create_res = client.post("/api/workflows", json=payload)
    assert create_res.status_code == 201
    wf = create_res.json()
    wf_id = wf["id"]
    assert wf["name"] == "Demo Multi-Agent Workflow"
    assert wf["version"] == 1

    # 2. Get workflow
    get_res = client.get(f"/api/workflows/{wf_id}")
    assert get_res.status_code == 200
    assert get_res.json()["id"] == wf_id

    # 3. List workflows
    list_res = client.get("/api/workflows")
    assert list_res.status_code == 200
    assert len(list_res.json()) >= 1

    # 4. Update workflow
    update_res = client.put(f"/api/workflows/{wf_id}", json={"name": "Renamed Workflow"})
    assert update_res.status_code == 200
    assert update_res.json()["name"] == "Renamed Workflow"
    assert update_res.json()["version"] == 2

    # 5. Delete workflow
    del_res = client.delete(f"/api/workflows/{wf_id}")
    assert del_res.status_code == 204

    # 6. Verify deleted
    not_found_res = client.get(f"/api/workflows/{wf_id}")
    assert not_found_res.status_code == 404
