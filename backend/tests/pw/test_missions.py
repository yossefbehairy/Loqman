"""CEO missions: planning -> workers -> synthesis, cancel races, delegation, idempotency."""

import asyncio
import json

import pytest

from backend.pyworker.runtime.base import AgentResult
from backend.pyworker.runtime.errors import RuntimeErrorBase
from backend.pyworker.runtime.mock_runtime import MockRuntime

from .helpers import Api

TERMINAL = {"succeeded", "partially_succeeded", "failed", "cancelled"}


async def wait_mission(api, mission_id, timeout=10, until=TERMINAL):
    for _ in range(int(timeout / 0.02)):
        m = await api("missions.get", mission_id=mission_id)
        if m["status"] in until:
            return m
        await asyncio.sleep(0.02)
    raise AssertionError(f"mission stuck in {m['status']}")


@pytest.mark.asyncio
async def test_reviewer_rejection_prevents_successful_mission(make_app):
    class RejectingReviewer(MockRuntime):
        async def execute_task(self, task):
            if "You are the 'reviewer' employee." in task.prompt:
                return AgentResult(text='{"approved":false,"reason":"The reported total is wrong"}',
                                   model_id=task.model_id)
            return await super().execute_task(task)

    api = Api(make_app(runtime=RejectingReviewer()))
    mission = await api("missions.create", objective="Plan and review the report")
    done = await wait_mission(api, mission["id"])
    assert done["status"] in ("failed", "partially_succeeded")
    reviewer = next(t for t in done["tasks"] if t["employee_role"] == "reviewer")
    assert reviewer["status"] == "failed"


class ScriptedMock(MockRuntime):
    """MockRuntime with hooks for worker prompts (matched on 'employee')."""

    def __init__(self, worker=None, planner=None, gate=None, started=None):
        super().__init__()
        self.worker, self.planner, self.gate, self.started = worker, planner, gate, started
        self.worker_calls = 0
        self.aborted = []

    async def execute_task(self, task):
        low = task.prompt.lower()
        if "employee." in low and "return strict json only matching" in low:
            self.worker_calls += 1
            if self.started is not None:
                self.started.set()
            if self.gate is not None:
                await self.gate.wait()
            if self.worker is not None:
                return await self.worker(task)
        if self.planner is not None and ("ceo planner" in low or "structured execution plan" in low):
            return AgentResult(text=self.planner, model_id=task.model_id)
        return await super().execute_task(task)

    async def abort_task(self, session_id):
        self.aborted.append(session_id)
        if self.gate is not None:
            self.gate.set()


@pytest.mark.asyncio
async def test_full_mission_runs_planner_workers_and_synthesis(api):
    m = await api("missions.create", objective="Plan and review the launch", title="Launch")
    assert m["status"] == "queued" and m["title"] == "Launch" and m["tasks"] == []
    done = await wait_mission(api, m["id"])
    assert done["status"] == "succeeded"
    assert [t["task_key"] for t in done["tasks"]] == ["planning", "quality_review"]
    assert all(t["status"] == "succeeded" and t["output"]["result"] is not None for t in done["tasks"])
    assert done["plan"]["tasks"][1]["depends_on"] == ["planning"]
    assert done["final_summary"] is not None and done["started_at"] and done["finished_at"]
    runs = (await api("missions.runs", mission_id=m["id"]))["runs"]
    assert sorted(r["kind"] for r in runs) == ["mission_planner", "mission_synthesis", "mission_task", "mission_task"]
    assert all(r["status"] == "succeeded" and r["conversation_id"] is None and r["mission_id"] == m["id"] for r in runs)
    kinds = {e["kind"] for e in (await api("usage.events", limit=50))["events"]}
    assert {"mission_planner", "mission_delegate", "mission_synthesis"} <= kinds
    assert (await api("missions.get", mission_id=m["id"]))["status"] == "succeeded"
    assert [x["id"] for x in (await api("missions.list"))["missions"]] == [m["id"]]


@pytest.mark.asyncio
async def test_validation_before_any_write(api):
    err = await api.fail("missions.create", objective="x", model_id="not-a-real-model")
    assert err.status == 400 and "not verified as free" in err.message
    err = await api.fail("missions.create", objective="x", variant="warp-9")
    assert err.status == 400 and "Variant" in err.message
    assert (await api.fail("missions.create", objective="   ")).status == 400
    assert (await api.fail("missions.create", objective="")).status == 422
    assert (await api.fail("missions.create", objective="x" * 4001)).status == 422
    assert (await api("missions.list"))["missions"] == []
    assert (await api.fail("missions.get", mission_id="nope")).status == 404
    assert (await api.fail("missions.cancel", mission_id="nope")).status == 404


@pytest.mark.asyncio
async def test_request_key_makes_creation_idempotent(api):
    a = await api("missions.create", objective="Build the thing", request_key="k1")
    b = await api("missions.create", objective="Build the thing", request_key="k1")
    assert a["id"] == b["id"]
    err = await api.fail("missions.create", objective="A different objective", request_key="k1")
    assert err.status == 409
    await wait_mission(api, a["id"])
    assert len((await api("missions.list"))["missions"]) == 1


@pytest.mark.asyncio
async def test_greeting_gets_one_reply_not_a_pipeline(api):
    m = await api("missions.create", objective="  Hello! ")
    assert m["status"] == "succeeded" and [t["task_key"] for t in m["tasks"]] == ["greeting_reply"]
    assert isinstance(m["final_summary"], str) and m["final_summary"]
    runs = (await api("missions.runs", mission_id=m["id"]))["runs"]
    assert runs == []  # no employee pipeline ran
    assert (await api.fail("missions.create", objective="hi", request_key="k")).status == 422
    real = await api("missions.create", objective="hi there, please build me an app")
    assert real["status"] == "queued"  # only exact greetings are shortcut
    await wait_mission(api, real["id"])


@pytest.mark.asyncio
async def test_planner_garbage_fails_mission_honestly(make_app):
    api = Api(make_app(runtime=ScriptedMock(planner="this is not json")))
    m = await api("missions.create", objective="Anything real")
    done = await wait_mission(api, m["id"])
    assert done["status"] == "failed" and done["error_summary"].startswith("Planning failed:")
    assert done["tasks"] == []
    runs = (await api("missions.runs", mission_id=m["id"]))["runs"]
    assert [(r["kind"], r["status"]) for r in runs] == [("mission_planner", "failed")]


@pytest.mark.asyncio
async def test_worker_invalid_json_retries_then_fails_and_skips_dependents(make_app):
    async def bad(task):
        return AgentResult(text="definitely not json", model_id=task.model_id)

    rt = ScriptedMock(worker=bad)
    api = Api(make_app(runtime=rt))
    m = await api("missions.create", objective="Do real work")
    done = await wait_mission(api, m["id"])
    assert done["status"] == "failed"
    planning, review = done["tasks"]
    assert planning["status"] == "failed" and planning["attempt_count"] == 3  # MISSION_MAX_RETRIES + 1
    assert review["status"] == "skipped"
    assert "invalid JSON" in (planning["error_message"] or "")
    assert rt.worker_calls == 3
    runs = (await api("missions.runs", mission_id=m["id"]))["runs"]
    worker_runs = [r for r in runs if r["kind"] == "mission_task"]
    assert len(worker_runs) == 3 and all(r["status"] == "failed" for r in worker_runs)
    assert [r["attempt"] for r in worker_runs] == [1, 2, 3]


@pytest.mark.asyncio
async def test_partial_success(make_app):
    calls = {"n": 0}

    async def flaky(task):
        calls["n"] += 1
        if "quality_review" in task.prompt.lower() or "review" in task.prompt.lower().split("task:")[-1][:40]:
            raise RuntimeErrorBase("nope")
        return AgentResult(text='{"ok": true}', model_id=task.model_id)

    api = Api(make_app(runtime=ScriptedMock(worker=flaky)))
    m = await api("missions.create", objective="Partial work")
    done = await wait_mission(api, m["id"])
    assert done["status"] == "partially_succeeded"
    assert [t["status"] for t in done["tasks"]] == ["succeeded", "failed"]


@pytest.mark.asyncio
async def test_cancel_wins_and_late_worker_output_is_discarded(make_app):
    gate, started = asyncio.Event(), asyncio.Event()

    async def late(task):
        return AgentResult(text='{"late": true}', model_id=task.model_id)

    rt = ScriptedMock(worker=late, gate=gate, started=started)
    api = Api(make_app(runtime=rt))
    m = await api("missions.create", objective="Long work")
    await asyncio.wait_for(started.wait(), 5)
    cancelled = await api("missions.cancel", mission_id=m["id"])
    assert cancelled["status"] == "cancelled"
    assert all(t["status"] == "cancelled" for t in cancelled["tasks"])
    gate.set()  # the worker finishes late
    await asyncio.sleep(0.3)
    final = await api("missions.get", mission_id=m["id"])
    assert final["status"] == "cancelled" and final["final_summary"] is None
    assert all(t["status"] == "cancelled" and t["output"] is None for t in final["tasks"])
    runs = (await api("missions.runs", mission_id=m["id"]))["runs"]
    assert not any(r["status"] == "succeeded" for r in runs if r["kind"] == "mission_task")
    assert not [r for r in runs if r["status"] in ("queued", "running")]  # nothing left active
    # cancelling again is a harmless no-op that returns the terminal mission
    assert (await api("missions.cancel", mission_id=m["id"]))["status"] == "cancelled"


@pytest.mark.asyncio
async def test_cancel_after_completion_keeps_result(api):
    m = await api("missions.create", objective="Quick real task")
    done = await wait_mission(api, m["id"])
    assert (await api("missions.cancel", mission_id=m["id"]))["status"] == done["status"]


@pytest.mark.asyncio
async def test_crash_recovery_fails_dangling_missions(make_app):
    from backend.pyworker.app import Application
    from backend.pyworker.runtime import ledger

    app = make_app()
    db = app.ctx.db
    mission = await app.handle("missions.create", {"objective": "Will be interrupted"})
    await asyncio.sleep(0.01)
    path = db.path
    db.execute("UPDATE missions SET status='running' WHERE id=?", (mission["id"],))
    db.execute("UPDATE mission_tasks SET status='running'")
    for t in list(app.ctx.background):
        t.cancel()
    await asyncio.sleep(0.05)
    ledger.bind(None)
    db.close()
    app2 = Application.create(db_path=path, runtime=MockRuntime())
    try:
        assert app2.recovery["recovered_missions"] >= 1
        got = await app2.handle("missions.get", {"mission_id": mission["id"]})
        assert got["status"] == "failed" and "interrupted" in got["error_summary"]
    finally:
        ledger.bind(None)
        app2.ctx.db.close()


# -- explicit delegation ----------------------------------------------------------------

@pytest.mark.asyncio
async def test_delegation_runs_one_employee_without_planning(make_app):
    seen = []

    async def worker_text(task):
        seen.append(task.prompt)
        return AgentResult(text="The deliverable text", model_id=task.model_id, session_id="s1")

    class Plain(ScriptedMock):
        async def execute_task(self, task):
            if "Assignment:" in task.prompt:
                seen.append(task.prompt)
                return AgentResult(text="The deliverable text", model_id=task.model_id, session_id="s1")
            return await super().execute_task(task)

    api = Api(make_app(runtime=Plain()))
    m = await api("missions.delegate", conversation_id="conv-1", employee_role="researcher",
                  title="Research X", instructions="Find facts about X", model_id="deepseek-v4.1-flash-free",
                  input_context="ctx", expected_output="bullet list")
    assert m["status"] == "queued" and [t["task_key"] for t in m["tasks"]] == ["explicit_assignment"]
    done = await wait_mission(api, m["id"])
    assert done["status"] == "succeeded"
    assert done["tasks"][0]["output"]["result"] == "The deliverable text"
    assert done["final_summary"] == {"result": "The deliverable text"}
    assert "Expected deliverable: bullet list" in seen[0] and "Input/context: ctx" in seen[0]
    runs = (await api("missions.runs", mission_id=m["id"]))["runs"]
    assert [(r["kind"], r["status"]) for r in runs] == [("mission_task", "succeeded")]  # no planner/synthesis
    row = api.db.one("SELECT source_conversation_id FROM missions WHERE id=?", (m["id"],))
    assert row["source_conversation_id"] == "conv-1"


@pytest.mark.asyncio
async def test_delegation_validation(api):
    base = dict(conversation_id="c", title="t", instructions="do", model_id="deepseek-v4.1-flash-free")
    assert (await api.fail("missions.delegate", employee_role="ghost", **base)).status == 404
    assert (await api.fail("missions.delegate", employee_role="x", agent_profile_id="ghost", **base)).status == 404
    err = await api.fail("missions.delegate", employee_role="coder", **{**base, "model_id": "nope"})
    assert err.status == 400
    assert (await api("missions.list"))["missions"] == []  # nothing persisted on a failed preflight


@pytest.mark.asyncio
async def test_delegation_pins_exact_profile_over_role(api):
    api.db.insert("agent_profiles", {
        "id": "coder-2", "name": "Second Coder", "role_type": "coder", "instructions": "I am second",
        "default_model_id": "deepseek-v4.1-flash-free", "task_depth": "Standard",
        "created_at": "2026-01-01 00:00:00.000000", "updated_at": "2026-01-01 00:00:00.000000"})
    m = await api("missions.delegate", conversation_id="c", employee_role="coder", agent_profile_id="coder-2",
                  title="t", instructions="do", model_id="deepseek-v4.1-flash-free")
    row = api.db.one("SELECT agent_profile_id FROM mission_tasks WHERE mission_id=?", (m["id"],))
    assert row["agent_profile_id"] == "coder-2"
    await wait_mission(api, m["id"])


@pytest.mark.asyncio
async def test_delegation_engine_must_be_honoured(make_app):
    """A profile pinned to an unavailable engine fails the delegation; it never silently falls back."""
    def factory(engine):
        raise RuntimeError("cli missing")

    app = make_app(runtime=MockRuntime(), mode="real", runtime_factory=None)
    api = Api(app)
    app.ctx.engine_factory = factory
    await api("profiles.update", profile_id="coder", engine="codex")
    err = await api.fail("missions.delegate", conversation_id="c", employee_role="coder", title="t",
                         instructions="do", model_id="gpt-x")
    assert err.status == 503 and "unavailable" in err.message
    assert (await api("missions.list"))["missions"] == []
