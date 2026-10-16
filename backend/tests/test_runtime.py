"""
Runtime Adapter Tests (MockRuntime and RealOpenCodeRuntime Contract)
"""

import pytest
from backend.app.runtime.mock_runtime import MockRuntime
from backend.app.runtime.base import ExecuteAgentTask


@pytest.mark.asyncio
async def test_mock_runtime_health():
    runtime = MockRuntime()
    health = await runtime.check_health()
    assert health.status == "ok"
    assert health.default_model_status == "available"
    assert health.models_count > 0
    assert health.free_models_count > 0


@pytest.mark.asyncio
async def test_mock_runtime_list_models():
    runtime = MockRuntime()
    free_models = await runtime.list_available_models(free_only=True)
    assert len(free_models) > 0
    for m in free_models:
        assert m.is_free is True
        assert m.cost.input == 0.0
        assert m.cost.output == 0.0


@pytest.mark.asyncio
async def test_mock_runtime_execute_planner_task():
    runtime = MockRuntime()
    task = ExecuteAgentTask(
        prompt="Role: Planner. Create steps for registration system.",
        model_id="muse-spark-1.3-contributor-free",
        task_depth="Standard"
    )
    result = await runtime.execute_task(task)
    assert result.text is not None
    assert "plan_steps" in result.text
    assert result.cost == 0.0
    assert result.duration_ms >= 0


@pytest.mark.asyncio
async def test_mock_runtime_execute_analyst_task():
    runtime = MockRuntime()
    task = ExecuteAgentTask(
        prompt="Role: Analyst. Evaluate risks for registration system.",
        model_id="muse-spark-1.3-contributor-free",
        task_depth="Deep"
    )
    result = await runtime.execute_task(task)
    assert result.text is not None
    assert "identified_risks" in result.text
    assert result.cost == 0.0
