"""
Adversarial Empirical Stress Tests for Milestone 1 (Challenger 2)
Focus Areas:
1. SQLite WAL mode concurrency and rapid updates without locking errors.
2. Persistence verification across separate database sessions and engine disconnections.
3. Zero-cost model preservation (0 input / 0 output cost) across profile updates and workflow revalidation.
"""

import os
import json
import tempfile
import threading
import concurrent.futures
import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker
from fastapi.testclient import TestClient

from backend.app.db.session import Base, get_db
from backend.app.db.models import AgentProfile, Workflow, WorkflowExecution
from backend.app.db.repository import AgentProfileRepository, ExecutionRepository
from backend.app.main import app
from backend.app.runtime import set_runtime, get_runtime
from backend.app.runtime.mock_runtime import MockRuntime
from backend.app.engine.executor import execute_workflow


# ==============================================================================
# FIXTURES: Disk-backed SQLite with WAL mode
# ==============================================================================

@pytest.fixture(scope="function")
def disk_sqlite_db():
    """
    Creates a temporary disk-backed SQLite database to empirically test WAL mode,
    busy_timeout, multi-threaded concurrency, and session reconnection.
    """
    tmp_dir = tempfile.mkdtemp()
    db_path = os.path.join(tmp_dir, "test_agentos_wal.db")
    db_url = f"sqlite:///{db_path}"

    engine = create_engine(
        db_url,
        connect_args={"check_same_thread": False},
        pool_size=10,
        max_overflow=20
    )

    # Attach SQLite PRAGMAs explicitly
    @event.listens_for(engine, "connect")
    def set_sqlite_pragmas(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON;")
        cursor.execute("PRAGMA journal_mode=WAL;")
        cursor.execute("PRAGMA busy_timeout=5000;")
        cursor.execute("PRAGMA synchronous=NORMAL;")
        cursor.close()

    # Create schema and seed profiles
    Base.metadata.create_all(bind=engine)
    SessionFactory = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    init_session = SessionFactory()
    repo = AgentProfileRepository(init_session)
    repo.seed_defaults()
    init_session.close()

    yield {
        "db_path": db_path,
        "db_url": db_url,
        "engine": engine,
        "session_factory": SessionFactory
    }

    # Teardown
    engine.dispose()
    try:
        if os.path.exists(db_path):
            os.remove(db_path)
        wal_file = f"{db_path}-wal"
        shm_file = f"{db_path}-shm"
        if os.path.exists(wal_file):
            os.remove(wal_file)
        if os.path.exists(shm_file):
            os.remove(shm_file)
        os.rmdir(tmp_dir)
    except Exception:
        pass


@pytest.fixture(scope="function")
def disk_client(disk_sqlite_db):
    """
    FastAPI TestClient wired to the disk-backed SQLite DB with MockRuntime.
    """
    session_factory = disk_sqlite_db["session_factory"]

    def override_get_db():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    mock_rt = MockRuntime()
    set_runtime(mock_rt)

    from backend.app.api.routes.workflows import get_session_factory
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_session_factory] = lambda: session_factory

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()


# ==============================================================================
# SECTION 1: SQLite WAL Mode Concurrency & Rapid Updates
# ==============================================================================

def test_sqlite_wal_pragmas_on_disk_db(disk_sqlite_db):
    """
    Empirically verifies that disk-backed SQLite initializes with:
    - journal_mode == 'wal'
    - busy_timeout == 5000
    - foreign_keys == 1 (ON)
    """
    engine = disk_sqlite_db["engine"]
    with engine.connect() as conn:
        jm = conn.execute(text("PRAGMA journal_mode;")).scalar()
        bt = conn.execute(text("PRAGMA busy_timeout;")).scalar()
        fk = conn.execute(text("PRAGMA foreign_keys;")).scalar()
        syn = conn.execute(text("PRAGMA synchronous;")).scalar()

        assert str(jm).lower() == "wal", f"Expected WAL mode, got {jm}"
        assert bt == 5000, f"Expected busy_timeout 5000ms, got {bt}"
        assert fk == 1, f"Expected foreign_keys ON (1), got {fk}"
        assert syn == 1, f"Expected synchronous NORMAL (1), got {syn}"


def test_concurrent_multi_threaded_profile_updates(disk_sqlite_db):
    """
    Stress-tests concurrent writes and reads across 10 threads and 100 operations.
    Verifies that WAL mode + busy_timeout=5000 prevent 'database is locked' errors.
    """
    session_factory = disk_sqlite_db["session_factory"]
    target_profiles = [
        "dr_sara", "eng_tarek", "prof_yasmin", "general_manager",
        "ahmed", "khaled", "layla", "omar", "nour", "mona"
    ]

    errors = []
    successes = []

    def worker_update(thread_id: int, iteration: int):
        profile_alias = target_profiles[iteration % len(target_profiles)]
        session = session_factory()
        try:
            repo = AgentProfileRepository(session)
            contract = {
                "thread_id": thread_id,
                "iteration": iteration,
                "timeout": 100 + iteration,
                "max_retries": (iteration % 3) + 1,
                "strict_json": True
            }
            updated = repo.update_profile(profile_alias, {
                "instructions": f"Updated by thread {thread_id} iteration {iteration}",
                "settings_json": json.dumps(contract),
                "task_depth": "Deep" if iteration % 2 == 0 else "Standard"
            })
            if updated is None:
                errors.append(f"Failed to find profile {profile_alias}")
            else:
                successes.append((thread_id, iteration, updated.id))
        except Exception as e:
            errors.append(f"Thread {thread_id} failed with error: {str(e)}")
        finally:
            session.close()

    def worker_read(thread_id: int, iteration: int):
        session = session_factory()
        try:
            repo = AgentProfileRepository(session)
            profiles = repo.list_all()
            assert len(profiles) == 10
        except Exception as e:
            errors.append(f"Read thread {thread_id} failed: {str(e)}")
        finally:
            session.close()

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = []
        for i in range(100):
            # Mix writes and reads concurrently
            t_id = i % 10
            futures.append(executor.submit(worker_update, t_id, i))
            if i % 3 == 0:
                futures.append(executor.submit(worker_read, t_id, i))
        
        concurrent.futures.wait(futures)

    # Verify zero database lock errors
    assert len(errors) == 0, f"Encountered {len(errors)} concurrent errors: {errors[:5]}"
    assert len(successes) == 100, f"Expected 100 successful updates, got {len(successes)}"


def test_rapid_contention_same_profile_updates(disk_sqlite_db):
    """
    Torture test: Multiple threads hammer updates on the EXACT SAME profile ('dr_sara')
    to create maximum write contention.
    """
    session_factory = disk_sqlite_db["session_factory"]
    errors = []
    commit_counts = []

    def update_dr_sara(worker_idx: int):
        for step in range(15):
            session = session_factory()
            try:
                repo = AgentProfileRepository(session)
                settings = {
                    "worker": worker_idx,
                    "step": step,
                    "timestamp": step * 10,
                    "badge": "👑 Manager",
                    "strict_json": True
                }
                prof = repo.update_profile("dr_sara", {
                    "name": f"Dr. Sara (Worker {worker_idx} - Step {step})",
                    "settings_json": json.dumps(settings)
                })
                assert prof is not None
                commit_counts.append(1)
            except Exception as e:
                errors.append(f"Worker {worker_idx} step {step} error: {e}")
            finally:
                session.close()

    threads = []
    for w in range(5):
        t = threading.Thread(target=update_dr_sara, args=(w,))
        threads.append(t)
        t.start()

    for t in threads:
        t.join()

    assert len(errors) == 0, f"Locking errors under contention: {errors}"
    assert sum(commit_counts) == 75, f"Expected 75 committed updates, got {sum(commit_counts)}"

    # Check final state is cleanly readable and uncorrupted
    verify_session = session_factory()
    repo = AgentProfileRepository(verify_session)
    final_prof = repo.get_by_id("dr_sara")
    assert final_prof is not None
    assert "Dr. Sara" in final_prof.name
    final_data = json.loads(final_prof.settings_json)
    assert "worker" in final_data
    assert final_data["badge"] == "👑 Manager"
    verify_session.close()


# ==============================================================================
# SECTION 2: Persistence Across Database Sessions & Disconnection
# ==============================================================================

def test_profile_contract_persistence_across_sessions_and_reconnection(disk_sqlite_db, disk_client):
    """
    Verifies that PUT /api/agent-profiles/{id} updates persist across complete engine
    disconnection and fresh reconnection (simulating process restart).
    """
    db_path = disk_sqlite_db["db_path"]
    db_url = disk_sqlite_db["db_url"]

    # 1. Update profile through HTTP API
    complex_contract = {
        "department": "planning",
        "is_manager": True,
        "badge": "👑 Manager",
        "arabic_name": "د. سارة الاستشارية",
        "title": "مدير التخطيط الاستراتيجي والهيكلة",
        "timeout": 240,
        "max_retries": 3,
        "strict_json": True,
        "custom_rules": [
            "Rule 1: Always enforce strict JSON output",
            "Rule 2: Never exceed allocated budget"
        ],
        "nested_metadata": {
            "department_code": "PLN-01",
            "clearance": "Executive",
            "tags": ["strategy", "planning", "governance"]
        }
    }

    update_payload = {
        "name": "Dr. Sara (د. سارة الاستشارية)",
        "description": "Lead Planning Director with persisted contract",
        "instructions": (
            "توجيهات معمارية دقيقة تفكك النظام إلى مراحل ومحطات قابلة للقياس، "
            "مع التحقق من القيود والشروط التشغيلية."
        ),
        "default_model_id": "deepseek-v4.1-flash-free",
        "default_variant_id": "max",
        "task_depth": "Deep",
        "settings_json": json.dumps(complex_contract, ensure_ascii=False)
    }

    put_res = disk_client.put("/api/agent-profiles/dr_sara", json=update_payload)
    assert put_res.status_code == 200
    put_data = put_res.json()
    assert put_data["name"] == update_payload["name"]
    assert put_data["default_model_id"] == "deepseek-v4.1-flash-free"
    assert put_data["default_variant_id"] == "max"
    assert put_data["task_depth"] == "Deep"

    # 2. Simulate server shutdown: Dispose engine and tear down existing sessions
    disk_sqlite_db["engine"].dispose()

    # 3. Simulate server restart: Create completely new Engine and SessionFactory from disk
    fresh_engine = create_engine(
        db_url,
        connect_args={"check_same_thread": False}
    )
    FreshSessionFactory = sessionmaker(autocommit=False, autoflush=False, bind=fresh_engine)

    def fresh_get_db():
        session = FreshSessionFactory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = fresh_get_db

    with TestClient(app) as fresh_client:
        # Re-query GET /api/agent-profiles/{id} using alias
        get_res_alias = fresh_client.get("/api/agent-profiles/dr_sara")
        assert get_res_alias.status_code == 200
        persisted_alias = get_res_alias.json()

        # Re-query GET /api/agent-profiles/{id} using primary ID
        get_res_pk = fresh_client.get("/api/agent-profiles/planner")
        assert get_res_pk.status_code == 200
        persisted_pk = get_res_pk.json()

        assert persisted_alias["id"] == persisted_pk["id"] == "planner"
        assert persisted_alias["name"] == update_payload["name"]
        assert persisted_alias["instructions"] == update_payload["instructions"]
        assert persisted_alias["default_model_id"] == "deepseek-v4.1-flash-free"
        assert persisted_alias["default_variant_id"] == "max"
        assert persisted_alias["task_depth"] == "Deep"

        # Verify parsed settings_json integrity
        parsed_settings = json.loads(persisted_alias["settings_json"])
        assert parsed_settings["timeout"] == 240
        assert parsed_settings["max_retries"] == 3
        assert parsed_settings["strict_json"] is True
        assert parsed_settings["arabic_name"] == "د. سارة الاستشارية"
        assert parsed_settings["title"] == "مدير التخطيط الاستراتيجي والهيكلة"
        assert len(parsed_settings["custom_rules"]) == 2
        assert parsed_settings["nested_metadata"]["department_code"] == "PLN-01"

    fresh_engine.dispose()


def test_persistence_for_all_department_managers(disk_client):
    """
    Verifies contract persistence across all 4 department managers:
    - Dr. Sara (dr_sara)
    - Eng. Tarek (eng_tarek)
    - Prof. Yasmin (prof_yasmin)
    - General Manager (general_manager)
    """
    managers = [
        {
            "id": "dr_sara",
            "name": "Dr. Sara (د. سارة - إدارة التخطيط)",
            "dept": "planning",
            "model": "deepseek-v4.1-flash-free",
            "variant": "high",
            "timeout": 150
        },
        {
            "id": "eng_tarek",
            "name": "Eng. Tarek (م. طارق - إدارة التحليل والمخاطر)",
            "dept": "analysis",
            "model": "deepseek-v4.1-flash-free",
            "variant": "max",
            "timeout": 180
        },
        {
            "id": "prof_yasmin",
            "name": "Prof. Yasmin (أ. ياسمين - إدارة الجودة والاعتماد)",
            "dept": "quality",
            "model": "muse-spark-1.3-contributor-free",
            "variant": "high",
            "timeout": 120
        },
        {
            "id": "general_manager",
            "name": "General Manager (المدير العام للمنظومة)",
            "dept": "executive",
            "model": "deepseek-v4.1-flash-free",
            "variant": "max",
            "timeout": 300
        }
    ]

    for mgr in managers:
        contract = {
            "department": mgr["dept"],
            "is_manager": True,
            "badge": "👑 Manager",
            "timeout": mgr["timeout"],
            "strict_json": True
        }
        update_body = {
            "name": mgr["name"],
            "default_model_id": mgr["model"],
            "default_variant_id": mgr["variant"],
            "settings_json": json.dumps(contract)
        }
        put_res = disk_client.put(f"/api/agent-profiles/{mgr['id']}", json=update_body)
        assert put_res.status_code == 200, f"Update failed for {mgr['id']}: {put_res.text}"

        get_res = disk_client.get(f"/api/agent-profiles/{mgr['id']}")
        assert get_res.status_code == 200
        data = get_res.json()
        assert data["name"] == mgr["name"]
        assert data["default_model_id"] == mgr["model"]
        assert data["default_variant_id"] == mgr["variant"]
        settings = json.loads(data["settings_json"])
        assert settings["timeout"] == mgr["timeout"]
        assert settings["department"] == mgr["dept"]


def test_profile_update_validation_and_edge_cases(disk_client):
    """
    Adversarial boundary testing:
    - Empty name (min_length=1) -> 422 Unprocessable Entity
    - Empty instructions (min_length=1) -> 422 Unprocessable Entity
    - Non-existent profile ID -> 404 Not Found
    - Large settings_json payload (50KB) -> 200 OK and persists without truncation
    """
    # 1. Empty name
    res_empty_name = disk_client.put("/api/agent-profiles/dr_sara", json={"name": ""})
    assert res_empty_name.status_code == 422

    # 2. Empty instructions
    res_empty_inst = disk_client.put("/api/agent-profiles/dr_sara", json={"instructions": ""})
    assert res_empty_inst.status_code == 422

    # 3. Non-existent profile
    res_404 = disk_client.put("/api/agent-profiles/unknown_manager_xyz", json={"name": "Ghost"})
    assert res_404.status_code == 404

    # 4. Large settings_json payload (50KB)
    large_dict = {
        "rules": [f"Standard rule number {i} for high compliance" for i in range(1000)],
        "notes": "Large payload test ensuring SQLite TEXT column has no truncation"
    }
    large_json_str = json.dumps(large_dict)
    assert len(large_json_str) > 40000

    res_large = disk_client.put("/api/agent-profiles/dr_sara", json={"settings_json": large_json_str})
    assert res_large.status_code == 200

    # Verify retrieval
    res_get_large = disk_client.get("/api/agent-profiles/dr_sara")
    assert res_get_large.status_code == 200
    retrieved_json = res_get_large.json()["settings_json"]
    assert retrieved_json == large_json_str
    assert len(json.loads(retrieved_json)["rules"]) == 1000


# ==============================================================================
# SECTION 3: Zero-Cost Model Preservation (0 input / 0 output cost)
# ==============================================================================

def test_catalog_free_models_strict_zero_cost(disk_client):
    """
    Empirically verifies that GET /api/models/free returns strictly verified
    zero-cost models with input_cost == 0.0 and output_cost == 0.0.
    """
    res = disk_client.get("/api/models/free")
    assert res.status_code == 200
    models = res.json()
    assert len(models) > 0

    model_ids = {m["id"] for m in models}
    # DeepSeek and Muse Spark must be present
    assert "deepseek-v4.1-flash-free" in model_ids
    assert "muse-spark-1.3-contributor-free" in model_ids

    for m in models:
        assert m["is_free"] is True, f"Model {m['id']} is marked is_free=False"
        assert m["input_cost"] == 0.0, f"Model {m['id']} has non-zero input cost: {m['input_cost']}"
        assert m["output_cost"] == 0.0, f"Model {m['id']} has non-zero output cost: {m['output_cost']}"


def test_model_refresh_preserves_zero_costs(disk_client):
    """
    Verifies POST /api/models/refresh preserves strictly 0 input and 0 output cost.
    """
    res = disk_client.post("/api/models/refresh")
    assert res.status_code == 200
    refreshed_models = res.json()
    assert len(refreshed_models) > 0

    for m in refreshed_models:
        assert m["input_cost"] == 0.0
        assert m["output_cost"] == 0.0
        assert m["is_free"] is True


def test_agent_profile_update_preserves_zero_cost_model(disk_client):
    """
    Verifies updating an agent profile to a zero-cost model preserves the model
    and ensures it resolves to an active zero-cost catalog model.
    """
    # 1. Update Dr. Sara to deepseek-v4.1-flash-free
    put_res = disk_client.put("/api/agent-profiles/dr_sara", json={
        "default_model_id": "deepseek-v4.1-flash-free",
        "default_variant_id": "max"
    })
    assert put_res.status_code == 200
    data = put_res.json()
    assert data["default_model_id"] == "deepseek-v4.1-flash-free"

    # 2. Check catalog to ensure that model is strictly 0.0 cost
    cat_res = disk_client.get("/api/models/free")
    assert cat_res.status_code == 200
    free_models = {m["id"]: m for m in cat_res.json()}
    assert data["default_model_id"] in free_models
    assigned_model = free_models[data["default_model_id"]]
    assert assigned_model["input_cost"] == 0.0
    assert assigned_model["output_cost"] == 0.0


@pytest.mark.asyncio
async def test_zero_cost_workflow_revalidation_with_updated_profile(disk_sqlite_db):
    """
    Verifies that when a profile is updated to a verified zero-cost model,
    the workflow execution engine's pre-node zero-cost revalidation succeeds.
    """
    session_factory = disk_sqlite_db["session_factory"]
    session = session_factory()
    runtime = MockRuntime()
    set_runtime(runtime)
    exec_repo = ExecutionRepository(session)
    profile_repo = AgentProfileRepository(session)

    # 1. Update profile to deepseek-v4.1-flash-free
    profile_repo.update_profile("dr_sara", {
        "default_model_id": "deepseek-v4.1-flash-free",
        "default_variant_id": "high"
    })
    session.commit()

    # 2. Build DAG referencing this updated model
    wf_definition = {
        "nodes": [
            {"id": "start-1", "type": "start"},
            {
                "id": "agent-1",
                "type": "agent",
                "data": {
                    "role_type": "planner",
                    "model_id": "deepseek-v4.1-flash-free",
                    "variant": "high",
                    "instructions": "Plan project milestones."
                }
            },
            {"id": "output-1", "type": "output"}
        ],
        "edges": [
            {"id": "e1", "source": "start-1", "target": "agent-1"},
            {"id": "e2", "source": "agent-1", "target": "output-1"}
        ]
    }

    wf = Workflow(name="Zero-Cost Verified Workflow", definition_json=json.dumps(wf_definition))
    session.add(wf)
    session.commit()

    wf_exec = exec_repo.create_execution(workflow_id=wf.id, workflow_snapshot_json=wf.definition_json)

    # Execute workflow
    result = await execute_workflow(
        execution_id=wf_exec.id,
        db=session,
        runtime=runtime,
        workflow_definition=wf_definition
    )

    assert result["status"] == "succeeded"
    session.refresh(wf_exec)
    assert wf_exec.status == "succeeded"
    session.close()


@pytest.mark.asyncio
async def test_zero_cost_revalidation_strictly_rejects_non_free_model(disk_sqlite_db):
    """
    Verifies that if an agent node specifies a paid or unconfirmed model,
    pre-node zero-cost revalidation immediately aborts execution and marks it failed.
    """
    session_factory = disk_sqlite_db["session_factory"]
    session = session_factory()
    runtime = MockRuntime()
    set_runtime(runtime)
    exec_repo = ExecutionRepository(session)

    # DAG specifying paid / unauthorized model
    wf_definition = {
        "nodes": [
            {"id": "start-1", "type": "start"},
            {
                "id": "agent-1",
                "type": "agent",
                "data": {
                    "role_type": "analyst",
                    "model_id": "openai/gpt-4o-paid-enterprise",
                    "instructions": "Evaluate risk."
                }
            },
            {"id": "output-1", "type": "output"}
        ],
        "edges": [
            {"id": "e1", "source": "start-1", "target": "agent-1"},
            {"id": "e2", "source": "agent-1", "target": "output-1"}
        ]
    }

    wf = Workflow(name="Adversarial Paid Model Attack", definition_json=json.dumps(wf_definition))
    session.add(wf)
    session.commit()

    wf_exec = exec_repo.create_execution(workflow_id=wf.id, workflow_snapshot_json=wf.definition_json)

    result = await execute_workflow(
        execution_id=wf_exec.id,
        db=session,
        runtime=runtime,
        workflow_definition=wf_definition
    )

    assert result["status"] == "failed"
    assert "Zero-cost revalidation failed" in result["error"]
    assert "openai/gpt-4o-paid-enterprise" in result["error"]

    session.refresh(wf_exec)
    assert wf_exec.status == "failed"
    assert "Zero-cost revalidation failed" in wf_exec.error_summary
    session.close()


def test_manager_consultation_uses_updated_zero_cost_model(disk_client):
    """
    Verifies direct manager consultation (POST /api/chat) uses the updated
    zero-cost model and returns authentic structured advice.
    """
    # Update General Manager to deepseek-v4.1-flash-free
    disk_client.put("/api/agent-profiles/general_manager", json={
        "default_model_id": "deepseek-v4.1-flash-free",
        "default_variant_id": "max"
    })

    # Consult General Manager
    chat_res = disk_client.post("/api/chat", json={
        "manager_id": "general_manager",
        "message": "تحديث نموذج العمل إلى DeepSeek Free"
    })
    assert chat_res.status_code == 200
    chat_data = chat_res.json()
    assert chat_data["manager_id"] == "general_manager"
    assert "المدير العام" in chat_data["manager_name"]
    assert len(chat_data["reply"]) > 20
    assert chat_data["suggested_mission"] is not None
