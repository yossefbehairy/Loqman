"""
Team-Chat Enhancement Tests (Sequential DAG with Persistent Message-Passing)
Verifies transcript construction, 6000-char cap, DB persistence + /messages API,
SSE replay inclusion, and zero regressions.
"""

import json
import pytest

from backend.app.db.models import Workflow
from backend.app.db.repository import ExecutionRepository
from backend.app.engine.context import (
    build_multi_source_context,
    build_team_transcript,
    extract_team_excerpt,
    format_agent_prompt,
)
from backend.app.engine.executor import execute_workflow
from backend.app.runtime.mock_runtime import MockRuntime


def _team_chain_definition():
    return {
        "nodes": [
            {"id": "start-1", "type": "start"},
            {"id": "planner-1", "type": "agent", "data": {"role_type": "planner", "model_id": "muse-spark-1.3-contributor-free", "task_depth": "Standard"}},
            {"id": "analyst-1", "type": "agent", "data": {"role_type": "analyst", "model_id": "muse-spark-1.3-contributor-free", "task_depth": "Deep"}},
            {"id": "reviewer-1", "type": "agent", "data": {"role_type": "reviewer", "model_id": "muse-spark-1.3-contributor-free", "task_depth": "Deep"}},
            {"id": "output-1", "type": "output"},
        ],
        "edges": [
            {"id": "e1", "source": "start-1", "target": "planner-1"},
            {"id": "e2", "source": "planner-1", "target": "analyst-1"},
            {"id": "e3", "source": "analyst-1", "target": "reviewer-1"},
            {"id": "e4", "source": "planner-1", "target": "reviewer-1"},
            {"id": "e5", "source": "reviewer-1", "target": "output-1"},
        ],
    }


def test_transcript_teammate_addressing_and_order():
    """Analyst sees Planner; Reviewer sees Start + Planner + Analyst in turn order."""
    node_definitions = {
        "start-1": {"id": "start-1", "type": "start", "data": {"role_type": "start"}},
        "planner-1": {"id": "planner-1", "type": "agent", "data": {"role_type": "planner"}},
        "analyst-1": {"id": "analyst-1", "type": "agent", "data": {"role_type": "analyst"}},
    }
    node_outputs = {
        "start-1": {"goal": "Deploy AI student registration"},
        "planner-1": {"key_objective": "Plan X", "team_message": "Planner here: staged 3 steps."},
        "analyst-1": {"analysis": "ok", "team_message": "Analyst here: reviewed Planner steps."},
    }

    # Analyst perspective: Start (turn 1) + Planner (turn 2)
    analyst_ctx = build_multi_source_context(
        node_id="analyst-1",
        upstream_node_ids=["start-1", "planner-1"],
        node_definitions=node_definitions,
        node_outputs={"start-1": node_outputs["start-1"], "planner-1": node_outputs["planner-1"]},
        workflow_input="goal",
    )
    analyst_transcript = build_team_transcript(
        analyst_ctx,
        node_turns={"start-1": 1, "planner-1": 2},
        node_display_names={"start-1": "Start (start-1)", "planner-1": "Planner (planner-1)"},
    )
    assert "Planner" in analyst_transcript
    assert analyst_transcript.index("Turn 1") < analyst_transcript.index("Turn 2")

    # Reviewer perspective: Start + Planner + Analyst chronological
    reviewer_ctx = build_multi_source_context(
        node_id="reviewer-1",
        upstream_node_ids=["start-1", "planner-1", "analyst-1"],
        node_definitions={**node_definitions, "reviewer-1": {"id": "reviewer-1", "type": "agent", "data": {"role_type": "reviewer"}}},
        node_outputs=node_outputs,
        workflow_input="goal",
    )
    reviewer_transcript = build_team_transcript(
        reviewer_ctx,
        node_turns={"start-1": 1, "planner-1": 2, "analyst-1": 3},
    )
    assert "planner" in reviewer_transcript.lower()
    assert "analyst" in reviewer_transcript.lower()
    assert reviewer_transcript.index("Turn 1") < reviewer_transcript.index("Turn 2") < reviewer_transcript.index("Turn 3")

    # Prompt injects transcript + collaboration instruction but stays schema-safe
    prompt = format_agent_prompt(
        base_instructions="Audit the plan.",
        node_role="reviewer",
        context_data=reviewer_ctx,
        task_depth="Deep",
        team_transcript=reviewer_transcript,
    )
    assert "Team Conversation Transcript" in prompt
    assert "sequential AI team" in prompt
    assert "ONLY valid JSON" in prompt


def test_transcript_length_cap():
    """Large upstream output truncates safely at <= 6000 chars with marker."""
    node_definitions = {}
    node_outputs = {}
    upstream_ids = []
    node_turns = {}
    for i in range(30):
        nid = f"agent-{i}"
        upstream_ids.append(nid)
        node_definitions[nid] = {"id": nid, "type": "agent", "data": {"role_type": "analyst"}}
        node_outputs[nid] = {"summary": "x" * 20000, "team_message": f"handover {i} " + "y" * 20000}
        node_turns[nid] = i + 1
    ctx = build_multi_source_context(
        node_id="reviewer-1",
        upstream_node_ids=upstream_ids,
        node_definitions=node_definitions,
        node_outputs=node_outputs,
        workflow_input="goal",
    )
    transcript = build_team_transcript(ctx, node_turns=node_turns)
    assert len(transcript) <= 6000
    assert "truncated for payload cap" in transcript
    assert "analyst" in transcript.lower()
    assert "said to team" in transcript


def test_extract_team_excerpt_prefers_handover_keys():
    parsed = {"key_objective": "obj", "plan_steps": ["a", "b"], "team_message": "Hello team, handover note here."}
    assert extract_team_excerpt(parsed) == "Hello team, handover note here."
    # Fallback still returns something bounded
    assert len(extract_team_excerpt({"a": "b" * 1000})) <= 400


@pytest.mark.asyncio
async def test_full_chain_persists_team_messages(db_session):
    """End-to-end: 5 nodes succeed and persist 5 ordered team.message events."""
    runtime = MockRuntime()
    exec_repo = ExecutionRepository(db_session)
    wf_def = _team_chain_definition()

    wf = Workflow(name="Team Chat Chain", definition_json=json.dumps(wf_def))
    db_session.add(wf)
    db_session.commit()
    wf_exec = exec_repo.create_execution(workflow_id=wf.id, workflow_snapshot_json=wf.definition_json)

    result = await execute_workflow(
        execution_id=wf_exec.id,
        db=db_session,
        runtime=runtime,
        workflow_definition=wf_def,
        initial_input={"goal": "Deploy AI student registration"},
    )
    assert result["status"] == "succeeded"

    from backend.app.db.models import ExecutionEvent

    team_events = (
        db_session.query(ExecutionEvent)
        .filter(ExecutionEvent.execution_id == wf_exec.id, ExecutionEvent.event_type == "team.message")
        .all()
    )
    assert len(team_events) == 5

    payloads = [json.loads(e.event_json) for e in team_events]
    turns = sorted(p["turn"] for p in payloads)
    assert turns == [1, 2, 3, 4, 5]
    for p in payloads:
        assert p["executionId"] == wf_exec.id
        assert p["fromNodeId"] and p["fromRole"] and p["excerpt"]
        assert isinstance(p["toNodeIds"], list)
        assert p["fullOutputRef"]
        assert p["timestamp"]
        assert len(p["excerpt"]) <= 400


def test_messages_api_returns_ordered_chat(client):
    """GET /api/executions/{id}/messages returns ordered team chat from SQLite."""
    wf_def = _team_chain_definition()
    create_res = client.post("/api/workflows", json={"name": "Team Messages API", "definition_json": json.dumps(wf_def)})
    assert create_res.status_code == 201
    wf_id = create_res.json()["id"]

    run_res = client.post(f"/api/workflows/{wf_id}/run", json={"input": {"goal": "Team API test"}, "sync": True})
    assert run_res.status_code == 200
    exec_id = run_res.json()["execution_id"]
    assert run_res.json()["status"] == "succeeded"

    msg_res = client.get(f"/api/executions/{exec_id}/messages")
    assert msg_res.status_code == 200
    data = msg_res.json()
    assert data["execution_id"] == exec_id
    assert data["count"] == 5
    turns = [m["turn"] for m in data["messages"]]
    assert turns == sorted(turns) == [1, 2, 3, 4, 5]
    roles = [m["from_role"] for m in data["messages"]]
    assert "planner" in roles and "analyst" in roles and "reviewer" in roles
    for m in data["messages"]:
        assert m["excerpt"] and m["full_output_ref"] and m["timestamp"]


def test_sse_stream_replays_team_messages(client):
    """GET /api/executions/{id} includes node records; events endpoint exists for replay."""
    wf_def = _team_chain_definition()
    create_res = client.post("/api/workflows", json={"name": "Team SSE Replay", "definition_json": json.dumps(wf_def)})
    wf_id = create_res.json()["id"]
    run_res = client.post(f"/api/workflows/{wf_id}/run", json={"input": "SSE test", "sync": True})
    exec_id = run_res.json()["execution_id"]

    details = client.get(f"/api/executions/{exec_id}")
    assert details.status_code == 200
    assert details.json()["status"] == "succeeded"
    assert len(details.json()["node_executions"]) == 5
    assert any(e["event_type"] == "team.message" for e in details.json()["events"])
