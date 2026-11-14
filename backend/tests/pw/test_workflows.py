"""Workflow CRUD/validation and the deterministic execution engine."""

import asyncio
import json

import pytest

from backend.pyworker.runtime.base import AgentResult
from backend.pyworker.runtime.mock_runtime import MockRuntime

from .helpers import Api

MODEL = "muse-spark-1.3-contributor-free"


def chain():
    agent = lambda nid, role, depth="Standard", **extra: {  # noqa: E731
        "id": nid, "type": "agent", "data": {"role_type": role, "model_id": MODEL, "task_depth": depth, **extra}}
    return {
        "nodes": [{"id": "start-1", "type": "start"}, agent("planner-1", "planner"),
                  agent("analyst-1", "analyst", "Deep"), agent("reviewer-1", "reviewer", "Deep"),
                  {"id": "output-1", "type": "output"}],
        "edges": [{"id": "e1", "source": "start-1", "target": "planner-1"},
                  {"id": "e2", "source": "planner-1", "target": "analyst-1"},
                  {"id": "e3", "source": "analyst-1", "target": "reviewer-1"},
                  {"id": "e4", "source": "planner-1", "target": "reviewer-1"},
                  {"id": "e5", "source": "reviewer-1", "target": "output-1"}],
    }


async def make_wf(api, definition=None, name="Pipeline"):
    return await api("workflows.create", name=name, definition_json=json.dumps(definition or chain()))


async def wait_exec(api, execution_id, timeout=10):
    for _ in range(int(timeout / 0.02)):
        e = await api("executions.get", execution_id=execution_id)
        if e["status"] in ("succeeded", "failed", "cancelled"):
            return e
        await asyncio.sleep(0.02)
    raise AssertionError("execution did not finish")


@pytest.mark.asyncio
async def test_workflow_crud_and_versioning(api):
    wf = await api("workflows.create", name="W")
    assert wf["version"] == 1 and wf["definition_json"] == "{}"
    up = await api("workflows.update", workflow_id=wf["id"], description="d")
    assert up["version"] == 2 and up["description"] == "d" and up["name"] == "W"
    assert (await api("workflows.get", workflow_id=wf["id"]))["version"] == 2
    assert [w["id"] for w in (await api("workflows.list"))["workflows"]] == [wf["id"]]
    await api("workflows.delete", workflow_id=wf["id"])
    assert (await api.fail("workflows.get", workflow_id=wf["id"])).status == 404
    assert (await api.fail("workflows.update", workflow_id="x", name="n")).status == 404
    assert (await api.fail("workflows.delete", workflow_id="x")).status == 404
    assert (await api.fail("workflows.create", name="")).status == 422


@pytest.mark.asyncio
async def test_validate_reports_structural_errors(api):
    good = await make_wf(api)
    v = await api("workflows.validate", workflow_id=good["id"])
    assert v["valid"] is True and v["topological_order"][0] == "start-1" and v["topological_order"][-1] == "output-1"
    cyc = chain()
    cyc["edges"].append({"id": "e6", "source": "reviewer-1", "target": "planner-1"})
    cyc["edges"] = [e for e in cyc["edges"] if e["id"] != "e5"] + [{"id": "e7", "source": "analyst-1", "target": "output-1"}]
    bad = await make_wf(api, cyc, "cyclic")
    v = await api("workflows.validate", workflow_id=bad["id"])
    assert v["valid"] is False and any("cycl" in e.lower() for e in v["errors"])
    broken = await api("workflows.create", name="x", definition_json="{not json")
    v = await api("workflows.validate", workflow_id=broken["id"])
    assert v["valid"] is False and "Malformed" in v["errors"][0]


@pytest.mark.asyncio
async def test_run_rejects_invalid_graph_with_detail(api):
    wf = await api("workflows.create", name="empty", definition_json=json.dumps({"nodes": [], "edges": []}))
    err = await api.fail("workflows.run", workflow_id=wf["id"])
    assert err.status == 400 and err.detail["errors"] and err.detail["message"].startswith("Workflow graph")
    broken = await api("workflows.create", name="x", definition_json="{oops")
    assert (await api.fail("workflows.run", workflow_id=broken["id"])).status == 400
    assert (await api.fail("workflows.run", workflow_id="nope")).status == 404
    assert (await api("executions.list"))["executions"] == []


@pytest.mark.asyncio
async def test_sync_run_full_pipeline_with_team_chat(api):
    wf = await make_wf(api)
    run = await api("workflows.run", workflow_id=wf["id"], input={"goal": "Deploy"}, sync=True)
    assert run["status"] == "succeeded" and run["result"]["final_output"]
    ex = await api("executions.get", execution_id=run["execution_id"])
    assert ex["status"] == "succeeded" and ex["started_at"] and ex["finished_at"] and ex["final_output_json"]
    assert json.loads(ex["workflow_snapshot_json"])["nodes"][0]["id"] == "start-1"  # immutable snapshot
    assert len(ex["node_executions"]) == 5
    assert all(n["status"] == "succeeded" and n["output_json"] and n["duration_ms"] >= 0 for n in ex["node_executions"])
    assert [n["attempt_count"] for n in ex["node_executions"] if n["role_type"] == "planner"] == [1]
    team = await api("executions.messages", execution_id=run["execution_id"])
    assert team["count"] == 5
    assert [m["turn"] for m in team["messages"]] == [1, 2, 3, 4, 5]
    assert [m["from_node_id"] for m in team["messages"]] == ["start-1", "planner-1", "analyst-1", "reviewer-1", "output-1"]
    assert team["messages"][1]["to_node_ids"] == ["analyst-1", "reviewer-1"]
    kinds = {e["kind"] for e in (await api("usage.events"))["events"]}
    assert kinds == {"workflow_node"}
    listed = (await api("executions.list"))["executions"]
    assert [e["id"] for e in listed] == [run["execution_id"]]


@pytest.mark.asyncio
async def test_background_run_returns_pending_then_finishes(api):
    wf = await make_wf(api)
    run = await api("workflows.run", workflow_id=wf["id"])
    assert run["status"] == "pending" and run["result"] is None
    ex = await wait_exec(api, run["execution_id"])
    assert ex["status"] == "succeeded"


class FlakyRuntime(MockRuntime):
    def __init__(self, fail_times=0, text=None):
        super().__init__()
        self.fail_times, self.calls, self.text = fail_times, 0, text

    async def execute_task(self, task):
        self.calls += 1
        if self.calls <= self.fail_times:
            return AgentResult(text="this is not json", model_id=task.model_id)
        if self.text is not None:
            return AgentResult(text=self.text, model_id=task.model_id)
        return await super().execute_task(task)


@pytest.mark.asyncio
async def test_invalid_json_is_retried_with_feedback_then_succeeds(make_app):
    rt = FlakyRuntime(fail_times=2)
    api = Api(make_app(runtime=rt))
    wf = await make_wf(api)
    run = await api("workflows.run", workflow_id=wf["id"], sync=True)
    assert run["status"] == "succeeded"
    ex = await api("executions.get", execution_id=run["execution_id"])
    planner = next(n for n in ex["node_executions"] if n["role_type"] == "planner")
    assert planner["attempt_count"] == 3
    failures = [e for e in ex["events"] if e["event_type"] == "node.attempt.failed"]
    assert len(failures) == 2


@pytest.mark.asyncio
async def test_exhausted_retries_fail_node_skip_downstream_and_fail_workflow(make_app):
    api = Api(make_app(runtime=FlakyRuntime(fail_times=99)))
    wf = await make_wf(api)
    run = await api("workflows.run", workflow_id=wf["id"], sync=True)
    assert run["status"] == "failed" and run["result"]["failed_node"] == "planner-1"
    ex = await api("executions.get", execution_id=run["execution_id"])
    by_node = {n["node_id"]: n for n in ex["node_executions"]}
    assert by_node["planner-1"]["status"] == "failed" and by_node["planner-1"]["attempt_count"] == 3
    assert ex["status"] == "failed" and "planner-1" in ex["error_summary"]
    assert by_node["start-1"]["status"] == "succeeded"
    assert {by_node[n]["status"] for n in ("analyst-1", "reviewer-1", "output-1")} == {"pending"}


@pytest.mark.asyncio
async def test_zero_cost_revalidation_blocks_unknown_models(api):
    d = chain()
    d["nodes"][1]["data"]["model_id"] = "paid-gpt-9"
    wf = await make_wf(api, d)
    run = await api("workflows.run", workflow_id=wf["id"], sync=True)
    assert run["status"] == "failed" and "Zero-cost revalidation failed" in run["result"]["error"]
    ex = await api("executions.get", execution_id=run["execution_id"])
    assert ex["status"] == "failed"


@pytest.mark.asyncio
async def test_ai_call_safety_cap(make_app):
    api = Api(make_app(runtime=FlakyRuntime(fail_times=999)))
    d = chain()
    for n in d["nodes"]:
        if n["type"] == "agent":
            n["data"]["max_retries"] = 5
    wf = await make_wf(api, d)
    run = await api("workflows.run", workflow_id=wf["id"], sync=True)
    assert run["status"] == "failed"
    ex = await api("executions.get", execution_id=run["execution_id"])
    planner = next(n for n in ex["node_executions"] if n["role_type"] == "planner")
    assert planner["attempt_count"] == 6  # cap (10) not reached on one node; all 6 allowed attempts were used


@pytest.mark.asyncio
async def test_cancel_running_execution_wins_over_late_results(make_app):
    gate, started = asyncio.Event(), asyncio.Event()

    class Slow(MockRuntime):
        def __init__(self):
            super().__init__()
            self.aborted = []

        async def execute_task(self, task):
            started.set()
            await gate.wait()
            return await super().execute_task(task)

        async def abort_task(self, session_id):
            self.aborted.append(session_id)

    rt = Slow()
    api = Api(make_app(runtime=rt))
    wf = await make_wf(api)
    run = await api("workflows.run", workflow_id=wf["id"])
    await asyncio.wait_for(started.wait(), 5)
    out = await api("executions.cancel", execution_id=run["execution_id"])
    assert out["status"] == "cancelled" and rt.aborted == [f"workflow-{run['execution_id']}-planner-1"]
    gate.set()
    await asyncio.sleep(0.3)
    ex = await api("executions.get", execution_id=run["execution_id"])
    assert ex["status"] == "cancelled" and ex["final_output_json"] is None
    assert all(n["status"] in ("cancelled", "succeeded") for n in ex["node_executions"])
    assert not any(n["status"] == "succeeded" for n in ex["node_executions"] if n["node_id"] != "start-1")
    assert any(e["event_type"] == "execution.cancelled" for e in ex["events"])
    again = await api("executions.cancel", execution_id=run["execution_id"])
    assert again["status"] == "cancelled" and "terminal" in again["message"]
    assert (await api.fail("executions.cancel", execution_id="nope")).status == 404
    assert (await api.fail("executions.get", execution_id="nope")).status == 404
    assert (await api.fail("executions.messages", execution_id="nope")).status == 404


@pytest.mark.asyncio
async def test_crash_recovery_fails_dangling_executions(make_app):
    from backend.pyworker.app import Application
    from backend.pyworker.runtime import ledger

    app = make_app()
    api = Api(app)
    wf = await make_wf(api)
    run = await api("workflows.run", workflow_id=wf["id"], sync=True)
    path = api.db.path
    api.db.execute("UPDATE workflow_executions SET status='running', finished_at=NULL WHERE id=?", (run["execution_id"],))
    api.db.execute("UPDATE node_executions SET status='running' WHERE node_id='output-1'")
    ledger.bind(None)
    api.db.close()
    app2 = Application.create(db_path=path, runtime=MockRuntime())
    try:
        assert app2.recovery["recovered_workflows"] == 1 and app2.recovery["recovered_nodes"] == 1
        ex = await app2.handle("executions.get", {"execution_id": run["execution_id"]})
        assert ex["status"] == "failed" and "restart" in ex["error_summary"]
        assert any(e["event_type"] == "execution.interrupted" for e in ex["events"])
    finally:
        ledger.bind(None)
        app2.ctx.db.close()


@pytest.mark.asyncio
async def test_validated_alias_fields_also_execute(api):
    """data.model / data.role pass validation, so they must also reach the executor."""
    definition = {"nodes": [{"id": "start-1", "type": "start"},
                            {"id": "a-1", "type": "agent", "data": {"role": "planner", "model": MODEL}},
                            {"id": "output-1", "type": "output"}],
                  "edges": [{"id": "e1", "source": "start-1", "target": "a-1"},
                            {"id": "e2", "source": "a-1", "target": "output-1"}]}
    wf = await make_wf(api, definition, "aliases")
    assert (await api("workflows.validate", workflow_id=wf["id"]))["valid"] is True
    run = await api("workflows.run", workflow_id=wf["id"], sync=True)
    assert run["status"] == "succeeded"
    ex = await api("executions.get", execution_id=run["execution_id"])
    agent = next(n for n in ex["node_executions"] if n["node_id"] == "a-1")
    assert agent["model_id"] == MODEL and agent["role_type"] == "planner"
