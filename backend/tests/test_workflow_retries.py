import json

import pytest

from backend.app.db.models import NodeExecution, Workflow
from backend.app.db.repository import ExecutionRepository
from backend.app.engine.executor import execute_workflow
from backend.app.runtime.mock_runtime import MockRuntime


@pytest.mark.asyncio
@pytest.mark.parametrize("retries,expected_calls", [(0, 1), (2, 3)])
async def test_node_retry_setting_controls_number_of_attempts(db_session, retries, expected_calls):
    class FailingRuntime(MockRuntime):
        calls = 0

        async def execute_task(self, task):
            self.calls += 1
            raise RuntimeError("provider failed")

    definition = {
        "nodes": [
            {"id": "start", "type": "start"},
            {"id": "agent", "type": "agent", "data": {"role_type": "planner", "model_id": "muse-spark-1.3-contributor-free", "max_retries": retries}},
            {"id": "output", "type": "output"},
        ],
        "edges": [{"id": "e1", "source": "start", "target": "agent"}, {"id": "e2", "source": "agent", "target": "output"}],
    }
    workflow = Workflow(name="retry test", definition_json=json.dumps(definition))
    db_session.add(workflow)
    db_session.commit()
    execution = ExecutionRepository(db_session).create_execution(workflow_id=workflow.id, workflow_snapshot_json=workflow.definition_json)
    runtime = FailingRuntime()
    result = await execute_workflow(execution_id=execution.id, db=db_session, runtime=runtime, workflow_definition=definition)
    node = db_session.query(NodeExecution).filter_by(execution_id=execution.id, node_id="agent").one()
    assert result["status"] == "failed"
    assert runtime.calls == node.attempt_count == expected_calls
