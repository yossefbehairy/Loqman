"""Event streams over the real pipe (mission/execution/run) and their lifecycle."""

import json
import time

from .pyworker_harness import worker_factory  # noqa: F401  (pytest fixture)


def _wait_mission(w, mid, timeout=15):
    deadline = time.monotonic() + timeout
    while True:
        m = w.ok("missions.get", {"mission_id": mid})
        if m["status"] in ("succeeded", "failed", "partially_succeeded", "cancelled"):
            return m
        assert time.monotonic() < deadline, m["status"]
        time.sleep(0.05)


def test_mission_stream_snapshot_then_live_events(worker_factory):
    w = worker_factory()
    m = w.ok("missions.create", {"objective": "Plan and review something slow [mock:delay=300]"})
    sid = w.open_stream("mission", m["id"])
    first = w.wait_stream_event(sid, "mission.status.changed")
    assert first["data"]["missionId"] == m["id"]
    _wait_mission(w, m["id"])
    done = w.wait_stream_event(sid, "task.status.changed")
    assert done["data"]["missionId"] == m["id"]
    events = [e["event"] for e in w.stream_events(sid)]
    assert "mission.status.changed" in events and "task.status.changed" in events
    assert w.ok("stream.close", {"stream_id": sid}) == {"closed": True}
    assert w.wait_stream_end(sid)["reason"] == "closed"
    assert w.ok("stream.close", {"stream_id": sid}) == {"closed": False}
    assert w.err("stream.open", {"topic": "mission", "id": "nope"})["status"] == 404


def test_execution_stream_replays_history_and_ends_on_terminal(worker_factory):
    w = worker_factory()
    definition = {
        "nodes": [{"id": "s", "type": "start"},
                  {"id": "a", "type": "agent", "data": {"role_type": "planner",
                                                        "model_id": "muse-spark-1.3-contributor-free"}},
                  {"id": "o", "type": "output"}],
        "edges": [{"id": "e1", "source": "s", "target": "a"}, {"id": "e2", "source": "a", "target": "o"}]}
    wf = w.ok("workflows.create", {"name": "wf", "definition_json": json.dumps(definition)})
    run = w.ok("workflows.run", {"workflow_id": wf["id"], "sync": True})
    assert run["status"] == "succeeded"
    sid = w.open_stream("execution", run["execution_id"])
    # history is persisted: a finished execution replays its team messages; live tail waits for a terminal event
    deadline = time.monotonic() + 5
    while len(w.stream_events(sid)) < 3 and time.monotonic() < deadline:
        time.sleep(0.05)
    assert [e["event"] for e in w.stream_events(sid)].count("team.message") == 3
    assert w.ok("stream.close", {"stream_id": sid})["closed"] is True
    assert w.ok("executions.messages", {"execution_id": run["execution_id"]})["count"] == 3


def test_stream_limit_and_cleanup_on_shutdown(worker_factory):
    w = worker_factory()
    run_id = w.start_run("long", delay_ms=30000)
    ids = [w.open_stream("run", run_id) for _ in range(5)]
    assert len(set(ids)) == 5
    for sid in ids:
        w.wait_stream_event(sid, "running")
    w.ok("shutdown")
    assert w.wait_exit(10) == 0
    assert w.bad_lines == []


def test_slow_stream_consumer_never_blocks_requests(worker_factory):
    w = worker_factory()
    run_id = w.start_run("x", delay_ms=500)
    sid = w.open_stream("run", run_id)
    t0 = time.monotonic()
    for _ in range(20):
        assert w.ok("health")["status"] == "ok"
    assert time.monotonic() - t0 < 5
    assert w.wait_stream_end(sid)["reason"] == "complete"
