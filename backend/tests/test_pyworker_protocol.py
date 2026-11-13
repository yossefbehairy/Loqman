"""Wire-protocol tests for the stdlib-only Python worker (real child process)."""

import json
import sqlite3

from .pyworker_harness import worker_factory  # noqa: F401  (pytest fixture)


def test_ready_health_and_stdout_purity(worker_factory):
    w = worker_factory()
    assert w.ready["type"] == "ready"
    assert w.ready["protocol"] == "agentos-worker/1"
    assert w.ready["runtime_mode"] == "mock" and w.ready["methods"] >= 70
    assert w.ready["interrupted_runs"] == 0 and w.ready["recovery"]["recovered_missions"] == 0
    health = w.ok("health")
    assert health["status"] == "ok" and health["app_name"]
    w.ok("shutdown")
    assert w.wait_exit() == 0
    assert w.bad_lines == []  # only valid protocol lines ever reached stdout


def test_successful_run_and_unknown_method(worker_factory):
    w = worker_factory()
    run_id = w.start_run("hello world")
    run = w.wait_status(run_id, "succeeded")
    assert run["assistant_content"] and run["engine"] == "mock"
    err = w.err("no.such.method")
    assert err["code"] == "method_not_found"
    assert "Traceback" not in json.dumps(err)


def test_malformed_json_does_not_break_worker(worker_factory):
    w = worker_factory()
    w.send_raw(b"{this is not json\n")
    assert w.wait_null_error(1)["code"] == "parse_error"
    w.send_raw(b"\xff\xfe\n")  # invalid UTF-8
    assert w.wait_null_error(2)["code"] == "parse_error"
    w.send_raw(b'{"id": 1, "method": "health", "x": NaN}\n')
    assert w.wait_null_error(3)["code"] == "parse_error"
    assert w.ok("health")["status"] == "ok"
    assert w.bad_lines == []


def test_invalid_requests_and_params(worker_factory):
    w = worker_factory()
    for i, raw in enumerate(
        [
            b"[1,2,3]\n",
            b'"just a string"\n',
            b'{"method": "health"}\n',  # missing id
            b'{"id": true, "method": "health"}\n',  # bool id
            b'{"id": "", "method": "health"}\n',  # empty id
            b'{"id": 1.5, "method": "health"}\n',  # float id
            b'{"id": {"a": 1}, "method": "health"}\n',  # object id
        ],
        start=1,
    ):
        w.send_raw(raw)
        assert w.wait_null_error(i)["code"] == "invalid_request", raw

    # id is recoverable -> echoed back
    w.send_raw(b'{"id": "a", "method": "Health!"}\n')
    assert w.wait_response("a")["error"]["code"] == "invalid_request"
    w.send_raw(b'{"id": "b", "method": "health", "params": [1]}\n')
    assert w.wait_response("b")["error"]["code"] == "invalid_request"
    w.send_raw(b'{"id": "c", "method": "health", "surprise": 1}\n')
    assert w.wait_response("c")["error"]["code"] == "invalid_request"

    cid = w.new_conversation()
    bad_params = [
        ("runs.start_turn", {}),
        ("runs.start_turn", {"conversation_id": cid, "engine": "mock", "model_id": "m", "prompt": 5}),
        ("runs.start_turn", {"conversation_id": cid, "engine": "mock", "model_id": "m", "prompt": ""}),
        ("runs.start_turn", {"conversation_id": cid, "engine": "mock", "model_id": "m", "prompt": "x", "extra": 1}),
        ("runs.start_turn", {"conversation_id": cid, "engine": "gemini", "model_id": "m", "prompt": "x"}),
        ("runs.start_turn", {"conversation_id": cid, "engine": "mock", "model_id": "m", "prompt": "x", "timeout": True}),
        ("runs.get", {}),
        ("runs.get", {"run_id": 7}),
        ("runs.events", {"run_id": "0" * 32, "after_id": -1}),
        ("conversations.create", {"title": ""}),
        ("health", {"unexpected": 1}),
    ]
    for method, params in bad_params:
        err = w.err(method, params)
        assert err["code"] == "invalid_params" and err["status"] == 422, (method, params, err)
    assert w.err("runs.get", {"run_id": "0" * 32})["code"] == "not_found"
    assert w.err("runs.cancel", {"run_id": "0" * 32})["status"] == 404
    assert w.err("ceo.approve", {"action_id": "x"})["status"] == 403  # needs explicit confirm
    # nothing leaked from rejected starts
    assert w.ok("conversations.messages", {"conversation_id": cid})["messages"] == []


def test_oversize_message_rejected_before_json_decode(worker_factory):
    w = worker_factory(args=["--max-message-bytes", "1024"])
    assert w.ready["max_message_bytes"] == 1024

    def padded(total: int) -> bytes:
        base = b'{"id":"p","method":"health"}'
        return base + b" " * (total - len(base))

    w.send_raw(padded(1024) + b"\n")  # exactly at the limit is accepted
    assert w.wait_response("p")["result"]["status"] == "ok"
    w.send_raw(b"x" * 1025 + b"\n")  # one byte over is rejected (never JSON-decoded)
    assert w.wait_null_error(1)["code"] == "message_too_large"
    w.send_raw(b"y" * (3 * 1024 * 1024) + b"\n")  # drained, not buffered; stream stays in sync
    assert w.wait_null_error(2)["code"] == "message_too_large"
    assert w.ok("health")["status"] == "ok"
    w.send_raw(b'{"id":"crlf","method":"health"}\r\n')  # CRLF framing is tolerated
    assert w.wait_response("crlf")["result"]["status"] == "ok"


def test_many_concurrent_requests_get_matching_responses(worker_factory):
    w = worker_factory()
    run_ids = [w.start_run(f"p{i}") for i in range(5)]
    sent = {}
    for i in range(60):
        if i % 2:
            rid = f"s{i}"
            sent[rid] = ("runs.get", run_ids[i % 5])
            w.send({"id": rid, "method": "runs.get", "params": {"run_id": run_ids[i % 5]}})
        else:
            rid = i  # integer ids are valid too
            sent[rid] = ("health", None)
            w.send({"id": rid, "method": "health"})
    for rid, (method, run_id) in sent.items():
        resp = w.wait_response(rid)
        assert resp["id"] == rid
        if method == "runs.get":
            assert resp["result"]["id"] == run_id  # replies never cross
        else:
            assert resp["result"]["status"] == "ok"
    assert len(w.responses) >= 60


def test_mock_engine_is_refused_outside_mock_mode_without_fallback(worker_factory):
    w = worker_factory(args=["--runtime-mode", "real"])
    assert w.ready["runtime_mode"] == "real"
    cid = w.new_conversation()
    err = w.err("runs.start_turn", {"conversation_id": cid, "engine": "mock", "model_id": "m", "prompt": "hi"})
    assert err["status"] == 422 and "Unsupported engine" in err["message"]
    assert w.ok("conversations.messages", {"conversation_id": cid})["messages"] == []
    assert w.err("profiles.update", {"profile_id": "custom", "engine": "mock"})["status"] == 422


def test_run_events_replay_in_order_without_duplicates_and_stream_resume(worker_factory):
    w = worker_factory()
    run_id = w.start_run("replay")
    w.wait_status(run_id, "succeeded")
    events = w.run_events(run_id)
    assert [e["type"] for e in events] == ["queued", "running", "succeeded"]
    ids = [e["id"] for e in events]
    assert ids == sorted(set(ids))
    assert [e["type"] for e in w.run_events(run_id, ids[0])] == ["running", "succeeded"]
    assert w.run_events(run_id, ids[-1]) == []
    # the push stream replays the same durable history, ends after the terminal event,
    # and resumes from last_event_id without repeating anything
    sid = w.open_stream("run", run_id)
    assert w.wait_stream_end(sid)["reason"] == "complete"
    got = w.stream_events(sid)
    assert [(m["event"], m["id"]) for m in got] == [(e["type"], e["id"]) for e in events]
    assert all(m["data"]["run_id"] == run_id for m in got)
    sid2 = w.open_stream("run", run_id, last_event_id=ids[0])
    w.wait_stream_end(sid2)
    assert [m["event"] for m in w.stream_events(sid2)] == ["running", "succeeded"]
    assert w.err("stream.open", {"topic": "run", "id": "nope"})["status"] == 404


def test_refuses_foreign_database_and_bad_paths(worker_factory, tmp_path):
    foreign = tmp_path / "foreign.db"
    con = sqlite3.connect(foreign)
    con.execute("CREATE TABLE users(id INTEGER)")
    con.commit()
    con.close()
    before = foreign.read_bytes()
    w = worker_factory(db_path=foreign, start=False)
    w.start(wait_ready=False)
    w.wait_ready()
    assert w.fatal["error"]["code"] == "startup_failed" and "not an AgentOS database" in w.fatal["error"]["message"]
    assert w.wait_exit() == 2
    assert foreign.read_bytes() == before  # never touched

    w3 = worker_factory(db_path=tmp_path / "nodir" / "w.db", start=False)
    w3.start(wait_ready=False)
    w3.wait_ready()
    assert w3.fatal["error"]["code"] == "startup_failed"
    assert "nodir" not in w3.fatal["error"]["message"]  # no paths in client-visible errors
    assert w3.wait_exit() == 2

    newer = tmp_path / "newer.db"
    con = sqlite3.connect(newer)
    con.execute("CREATE TABLE missions(id)")
    con.execute("PRAGMA user_version=99")
    con.commit()
    con.close()
    w4 = worker_factory(db_path=newer, start=False)
    w4.start(wait_ready=False)
    w4.wait_ready()
    assert "newer" in w4.fatal["error"]["message"] and w4.wait_exit() == 2


def test_data_dir_mode_creates_agentos_db_there(worker_factory, tmp_path):
    data = tmp_path / "userdata"
    w = worker_factory(db_path=False, args=["--data-dir", str(data)])
    assert w.ok("health")["status"] == "ok"
    assert (data / "agentos.db").exists()
    assert w.ok("profiles.list")["profiles"]
    w.ok("shutdown")
    assert w.wait_exit() == 0


def test_large_responses_are_not_limited_by_the_inbound_cap(worker_factory, tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "big.bin").write_bytes(b"A" * (2 * 1024 * 1024))
    w = worker_factory(args=["--max-message-bytes", "4096"])
    wid = w.ok("workspaces.create", {"path": str(ws)})["id"]
    art = w.ok("artifacts.register", {"workspace_id": wid, "path": "big.bin"})
    content = w.ok("artifacts.content", {"artifact_id": art["id"]}, timeout=30)
    assert content["size"] == 2 * 1024 * 1024 and len(content["content_base64"]) > 2_000_000
    assert w.bad_lines == []
