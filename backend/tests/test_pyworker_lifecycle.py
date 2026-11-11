"""Lifecycle tests: responsiveness during runs, cancel races, restart, EOF, shutdown.
Every test runs the worker as a real child process with the explicit mock engine."""

import sqlite3
import time

from .pyworker_harness import worker_factory  # noqa: F401  (pytest fixture)


def _db_one(db_path, sql, args=()):
    con = sqlite3.connect(db_path)
    try:
        return con.execute(sql, args).fetchone()
    finally:
        con.close()


def test_worker_stays_responsive_while_run_is_in_progress(worker_factory):
    w = worker_factory()
    t0 = time.monotonic()
    run_id = w.start_run("slow", delay_ms=1500)
    assert time.monotonic() - t0 < 1.0  # the RPC ends when the run is created, not when it finishes
    assert w.ok("runs.get", {"run_id": run_id})["status"] in ("queued", "running")
    latencies = []
    for _ in range(5):
        t = time.monotonic()
        assert w.ok("health")["status"] == "ok"
        latencies.append(time.monotonic() - t)
        time.sleep(0.05)
    assert max(latencies) < 0.5
    assert w.ok("runs.get", {"run_id": run_id})["status"] in ("queued", "running")
    w.wait_status(run_id, "succeeded", timeout=10)


def test_cancel_stops_run_and_blocks_a_late_result(worker_factory):
    w = worker_factory()
    cid = w.new_conversation()
    # an engine that ignores cancellation and still answers 500 ms after being cancelled
    run_id = w.start_run("stubborn", delay_ms=5000, late_ms=500, conversation=cid)
    w.wait_status(run_id, "running")
    res = w.ok("runs.cancel", {"run_id": run_id})
    assert res["status"] == "cancelled" and res["error"] == "Cancelled by user."
    time.sleep(1.0)  # let the late result arrive
    run = w.ok("runs.get", {"run_id": run_id})
    assert run["status"] == "cancelled" and run["assistant_content"] is None
    types = [e["type"] for e in w.run_events(run_id)]
    assert types == ["queued", "running", "cancelled"]
    msgs = w.ok("conversations.messages", {"conversation_id": cid})["messages"]
    assert [m["role"] for m in msgs] == ["user"]  # no assistant message was written
    # cancelling again is idempotent; cancelling a run that finished otherwise is refused
    assert w.ok("runs.cancel", {"run_id": run_id})["status"] == "cancelled"
    done = w.start_run("quick")
    w.wait_status(done, "succeeded")
    assert w.err("runs.cancel", {"run_id": done})["status"] == 409
    assert w.err("runs.cancel", {"run_id": "0" * 32})["status"] == 404


def test_restart_during_run_marks_interrupted_and_never_reruns(worker_factory, tmp_path):
    db = tmp_path / "restart.db"
    w1 = worker_factory(db_path=db)
    cid = w1.new_conversation()
    run_id = w1.start_run("crashy", delay_ms=30000, conversation=cid)
    w1.wait_status(run_id, "running")
    w1.kill()  # hard kill: no cleanup runs, the DB still says "running"
    assert _db_one(db, "SELECT status FROM conversation_runs WHERE id=?", (run_id,))[0] == "running"

    w2 = worker_factory(db_path=db)
    assert w2.ready["interrupted_runs"] == 1
    run = w2.ok("runs.get", {"run_id": run_id})
    assert run["status"] == "interrupted" and "not re-executed" in run["error"]
    events = w2.run_events(run_id)
    ids = [e["id"] for e in events]
    assert ids == sorted(set(ids)) and events[-1]["type"] == "interrupted"
    time.sleep(0.5)  # a rerun would have added events / a reply by now
    assert len(w2.run_events(run_id)) == len(events)
    assert [m["role"] for m in w2.ok("conversations.messages", {"conversation_id": cid})["messages"]] == ["user"]
    w2.ok("shutdown")
    w2.wait_exit()
    w3 = worker_factory(db_path=db)
    assert w3.ready["interrupted_runs"] == 0 and len(w3.run_events(run_id)) == len(events)


def test_eof_during_run_exits_cleanly_and_interrupts_run(worker_factory, tmp_path):
    db = tmp_path / "eof.db"
    w = worker_factory(db_path=db)
    run_id = w.start_run("eof", delay_ms=30000)
    w.wait_status(run_id, "running")
    w.close_stdin()
    assert w.wait_exit(10) == 0
    assert _db_one(db, "SELECT status FROM conversation_runs WHERE id=?", (run_id,))[0] == "interrupted"
    last = _db_one(db, "SELECT type FROM conversation_run_events WHERE run_id=? ORDER BY id DESC", (run_id,))
    assert last[0] == "interrupted"
    assert w.bad_lines == []


def test_eof_with_no_runs_exits_promptly(worker_factory):
    w = worker_factory()
    w.close_stdin()
    assert w.wait_exit(5) == 0


def test_shutdown_request_during_run_and_missions(worker_factory, tmp_path):
    db = tmp_path / "shutdown.db"
    w = worker_factory(db_path=db)
    run_id = w.start_run("sd", delay_ms=30000)
    w.wait_status(run_id, "running")
    m = w.ok("missions.create", {"objective": "Long running mission [mock:delay=30000]"})
    assert w.ok("shutdown") == {"status": "shutting_down"}
    assert w.wait_exit(10) == 0
    assert _db_one(db, "SELECT status FROM conversation_runs WHERE id=?", (run_id,))[0] == "interrupted"
    # the in-flight mission is persisted as failed (never left "running", never faked)
    status = _db_one(db, "SELECT status FROM missions WHERE id=?", (m["id"],))[0]
    assert status in ("failed", "cancelled")
    assert _db_one(db, "SELECT COUNT(*) FROM conversation_runs WHERE status IN ('queued','running')")[0] == 0


def test_shutdown_does_not_hang_on_run_that_ignores_cancel(worker_factory, tmp_path):
    db = tmp_path / "stubborn.db"
    w = worker_factory(db_path=db, args=["--shutdown-timeout", "1"])
    run_id = w.start_run("stuck", delay_ms=60000, late_ms=60000)
    w.wait_status(run_id, "running")
    t0 = time.monotonic()
    w.close_stdin()
    assert w.wait_exit(10) == 0
    assert time.monotonic() - t0 < 6
    assert _db_one(db, "SELECT status FROM conversation_runs WHERE id=?", (run_id,))[0] == "interrupted"


def test_parent_closing_stdout_ends_worker(tmp_path):
    """No reader thread here: the parent reads the ready line, then drops the read end."""
    import json
    import subprocess
    import sys

    from .pyworker_harness import ROOT

    err = open(tmp_path / "err.txt", "wb")
    proc = subprocess.Popen(
        [sys.executable, "-m", "backend.pyworker", "--db", str(tmp_path / "o.db"), "--runtime-mode", "mock"],
        cwd=str(ROOT), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=err)
    try:
        assert json.loads(proc.stdout.readline())["type"] == "ready"
        proc.stdout.close()  # the parent drops the read end but keeps stdin open
        try:
            proc.stdin.write(b'{"id":"ping","method":"health"}\n')  # the reply write fails -> worker stops itself
            proc.stdin.flush()
        except OSError:
            pass
        assert proc.wait(10) == 0
    finally:
        if proc.poll() is None:
            proc.kill()
        err.close()
    assert "stdout closed by parent" in (tmp_path / "err.txt").read_text(errors="replace")
