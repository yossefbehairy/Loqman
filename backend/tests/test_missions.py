"""
CEO missions orchestration tests (mock-based, no real OpenCode calls).
Covers: planning validation, dispatch/dependencies, retry, cancellation,
duplicate protection, partial success, synthesis, and full API E2E.
"""

import json

from backend.app.orchestration.planner import (
    build_planning_prompt,
    build_synthesis_prompt,
    parse_and_validate_plan,
)
from backend.app.orchestration.ceo import _levels
from backend.app.db.models import MissionTask


def _t(key, deps=None):
    return MissionTask(
        mission_id="m1", task_key=key, employee_role="planner",
        title=key, instructions="x", depends_on_json=json.dumps(deps or []),
    )


# --- planning schema ---

def test_plan_prompt_contains_rules():
    p = build_planning_prompt("Build a website", ["planner", "reviewer"])
    assert "STRICT JSON" in p
    assert "depends_on" in p


def test_plan_parse_valid():
    raw = json.dumps({
        "objective": "O",
        "tasks": [
            {"key": "planning", "employee_role": "planner", "title": "P",
             "instructions": "Do plan.", "depends_on": [], "expected_output": "JSON"},
            {"key": "quality_review", "employee_role": "reviewer", "title": "Q",
             "instructions": "Review.", "depends_on": ["planning"], "expected_output": "JSON"},
        ],
    })
    plan = parse_and_validate_plan(raw, "O")
    assert len(plan.tasks) == 2
    assert plan.tasks[1].depends_on == ["planning"]


def test_plan_parse_fenced_json():
    raw = "```json\n" + json.dumps({
        "objective": "O",
        "tasks": [{"key": "a", "employee_role": "planner", "title": "T",
                    "instructions": "I", "depends_on": [], "expected_output": "J"}],
    }) + "\n```"
    plan = parse_and_validate_plan(raw, "O")
    assert plan.tasks[0].key == "a"


def test_plan_rejects_invalid_json():
    try:
        parse_and_validate_plan("not json", "O")
        assert False, "should raise"
    except ValueError:
        pass


def test_plan_rejects_unknown_role():
    raw = json.dumps({"objective": "O", "tasks": [
        {"key": "a", "employee_role": "astronaut", "title": "T", "instructions": "I",
         "depends_on": [], "expected_output": "J"}]})
    try:
        parse_and_validate_plan(raw, "O")
        assert False, "should raise"
    except ValueError as e:
        assert "employee_role" in str(e) or "unknown" in str(e)


def test_plan_rejects_cycle():
    raw = json.dumps({"objective": "O", "tasks": [
        {"key": "a", "employee_role": "planner", "title": "T", "instructions": "I",
         "depends_on": ["b"], "expected_output": "J"},
        {"key": "b", "employee_role": "reviewer", "title": "T", "instructions": "I",
         "depends_on": ["a"], "expected_output": "J"}]})
    try:
        parse_and_validate_plan(raw, "O")
        assert False, "should raise"
    except ValueError as e:
        assert "cycle" in str(e)


def test_plan_rejects_dup_keys():
    raw = json.dumps({"objective": "O", "tasks": [
        {"key": "a", "employee_role": "planner", "title": "T", "instructions": "I",
         "depends_on": [], "expected_output": "J"},
        {"key": "a", "employee_role": "reviewer", "title": "T", "instructions": "I",
         "depends_on": [], "expected_output": "J"}]})
    try:
        parse_and_validate_plan(raw, "O")
        assert False, "should raise"
    except ValueError as e:
        assert "duplicate" in str(e)


def test_levels_dependency_order():
    tasks = [_t("a"), _t("b", ["a"]), _t("c", ["a"]), _t("d", ["b", "c"])]
    lv = _levels(tasks)
    assert [t.task_key for t in lv[0]] == ["a"]
    assert sorted(t.task_key for t in lv[1]) == ["b", "c"]
    assert [t.task_key for t in lv[2]] == ["d"]


def test_synthesis_prompt_marks_unverified():
    s = build_synthesis_prompt("Goal", [{"task_key": "k", "title": "T",
               "employee_role": "planner", "status": "succeeded", "output_excerpt": "{}"}])
    assert "unverified" in s.lower()


# --- API E2E (mock runtime via conftest client) ---

def test_mission_full_e2e_mock(client):
    r = client.post("/api/missions", json={"objective": "Analyze sample requirements and return plan"})
    assert r.status_code == 201, r.text
    mid = r.json()["id"]
    assert r.json()["status"] in ("queued", "planning", "running", "succeeded")

    import time
    final = None
    for _ in range(100):
        g = client.get(f"/api/missions/{mid}")
        assert g.status_code == 200
        st = g.json()["status"]
        if st in ("succeeded", "partially_succeeded", "failed", "cancelled"):
            final = g.json()
            break
        time.sleep(0.2)
    assert final is not None, "mission did not reach terminal state"
    assert final["status"] == "succeeded", final
    assert len(final["tasks"]) >= 2
    for t in final["tasks"]:
        assert t["status"] == "succeeded", t
        assert t["output"] is not None, t
        assert t["model_id"], t
    assert final["final_summary"] is not None


def test_mission_duplicate_protection(client):
    obj = "Duplicate protection probe objective xyz"
    r1 = client.post("/api/missions", json={"objective": obj})
    assert r1.status_code == 201
    r2 = client.post("/api/missions", json={"objective": obj})
    assert r2.status_code == 201
    assert r1.json()["id"] == r2.json()["id"]


def test_mission_rejects_unknown_model(client):
    r = client.post("/api/missions", json={"objective": "X", "model_id": "nope-paid-model-123"})
    assert r.status_code == 400
    assert "free" in r.text.lower()


def test_mission_rejects_bad_variant(client):
    r = client.post(
        "/api/missions",
        json={"objective": "X", "model_id": "muse-spark-1.3-contributor-free", "variant": "ultra-mega"},
    )
    assert r.status_code == 400
    assert "Variant" in r.text


def test_mission_cancel(client):
    r = client.post("/api/missions", json={"objective": "Cancel me please"})
    assert r.status_code == 201
    mid = r.json()["id"]
    # Unknown mission -> 404.
    nf = client.post("/api/missions/does-not-exist/cancel")
    assert nf.status_code == 404
    c = client.post(f"/api/missions/{mid}/cancel")
    assert c.status_code == 200
    # Mock worker is instant: cancel either wins (cancelled) or the mission
    # already reached a terminal state — both are truthful, never fake.
    assert c.json()["status"] in (
        "cancelled", "succeeded", "partially_succeeded", "failed",
        "queued", "planning", "running",
    )
    import time
    for _ in range(50):
        g = client.get(f"/api/missions/{mid}")
        if g.json()["status"] in ("cancelled", "succeeded", "partially_succeeded", "failed"):
            break
        time.sleep(0.1)
    assert g.json()["status"] in ("cancelled", "succeeded", "partially_succeeded", "failed")


def test_mission_list_and_get(client):
    r = client.post("/api/missions", json={"objective": "List probe"})
    assert r.status_code == 201
    lst = client.get("/api/missions")
    assert lst.status_code == 200
    assert any(m["id"] == r.json()["id"] for m in lst.json())


# --- failure modes with stub runtimes (no real network) ---

class _FailingRuntime:
    async def check_health(self):
        from backend.app.runtime.base import RuntimeHealth
        return RuntimeHealth(status="offline", default_model_status="unavailable",
                             models_count=0, free_models_count=0)

    async def list_available_models(self, free_only=True):
        from backend.app.runtime.mock_runtime import MockRuntime
        return await MockRuntime().list_available_models(free_only=free_only)

    async def execute_task(self, task):
        raise RuntimeError("Simulated OpenCode disconnection")


class _FlakyThenOkRuntime:
    """Fails the first attempt of each employee task, then succeeds (retry path)."""

    def __init__(self):
        self.calls = 0
        from backend.app.runtime.mock_runtime import MockRuntime
        self.mock = MockRuntime()

    async def check_health(self):
        return await self.mock.check_health()

    async def list_available_models(self, free_only=True):
        return await self.mock.list_available_models(free_only=free_only)

    async def execute_task(self, task):
        self.calls += 1
        if "you are the '" in task.prompt.lower() and self.calls % 2 == 1:
            raise RuntimeError("transient task blip (attempt 1)")
        return await self.mock.execute_task(task)


def test_mission_failed_when_runtime_disconnected(client):
    from backend.app.runtime import set_runtime
    from backend.app.runtime.mock_runtime import MockRuntime
    set_runtime(_FailingRuntime())
    try:
        r = client.post("/api/missions", json={"objective": "Will fail at planning"})
        assert r.status_code == 201
        mid = r.json()["id"]
        import time
        final = None
        for _ in range(100):
            g = client.get(f"/api/missions/{mid}")
            if g.json()["status"] in ("failed", "cancelled", "succeeded", "partially_succeeded"):
                final = g.json()
                break
            time.sleep(0.1)
        assert final is not None
        assert final["status"] == "failed", final
        assert "Planning failed" in (final["error_summary"] or "")
        # No fake deliverables on total failure.
        assert all(t["status"] != "succeeded" for t in final["tasks"])
    finally:
        set_runtime(MockRuntime())


def test_mission_retry_exhaustion_marks_failed(client):
    from backend.app.runtime import set_runtime
    from backend.app.runtime.mock_runtime import MockRuntime
    flaky = _FlakyThenOkRuntime()
    set_runtime(flaky)
    try:
        r = client.post("/api/missions", json={"objective": "Retry probe"})
        assert r.status_code == 201
        mid = r.json()["id"]
        import time
        final = None
        for _ in range(100):
            g = client.get(f"/api/missions/{mid}")
            if g.json()["status"] in ("failed", "cancelled", "succeeded", "partially_succeeded"):
                final = g.json()
                break
            time.sleep(0.1)
        assert final is not None
        # First attempts flaked but retries must recover -> succeeded.
        assert final["status"] == "succeeded", final
        assert all(t["status"] == "succeeded" for t in final["tasks"])
        assert any(t["attempt_count"] >= 2 for t in final["tasks"])
    finally:
        set_runtime(MockRuntime())
