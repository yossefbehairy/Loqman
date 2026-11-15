"""Version 17 snapshot upgrade, sessions, driver dedupe and both stream projections."""

import json
import sqlite3

import pytest

from backend.pyworker.core import backup
from backend.pyworker.db.core import Database, ts
from backend.pyworker.db.migrate import init_db
from backend.pyworker.runtime.driver import DriverEvent
from backend.pyworker.runtime.sessions import SessionRepository

from .fixtures import schema_v17
from .helpers import Api
from .test_runs import FakeRuntime, new_conv, turn, wait_status


def binding(**kwargs):
    return {"scope_kind": "conversation", "scope_id": "c", "engine": "codex", "protocol": "codex-app-server",
        "namespace": "local", "native_session_id": "native", "model_id": "model", "cwd": ".", **kwargs}


def test_v17_snapshot_upgrades_data_backup_and_second_upgrade_is_noop(tmp_path):
    assert schema_v17.SCHEMA_VERSION == 17
    path = tmp_path / "old.db"
    with sqlite3.connect(path) as conn:
        for ddl in schema_v17.TABLES.values():
            conn.execute(ddl)
        for indexes in schema_v17.INDEXES.values():
            for ddl in indexes:
                conn.execute(ddl)
        conn.execute("PRAGMA user_version=17")
        conn.execute("INSERT INTO conversations(id,title,created_at,updated_at) VALUES('c','keep',?,?)", (ts(), ts()))
        conn.execute("INSERT INTO conversation_runs(id,conversation_id,engine,model_id,timeout,status,created_at) "
                     "VALUES('r','c','codex','model',90,'succeeded',?)", (ts(),))
        conn.execute("INSERT INTO conversation_run_events(run_id,type,data_json,created_at) VALUES('r','succeeded','{}',?)", (ts(),))
        conn.execute("INSERT INTO approvals(id,run_id,action_summary,scope,status,expires_at,created_at) "
                     "VALUES('a','r','keep','workspace','approved',?,?)", (ts(), ts()))
        conn.execute("INSERT INTO opencode_conversations VALUES('legacy','old-native','m','p','agent',?,?)", (ts(), ts()))
    db = Database(str(path))
    try:
        init_db(db)
        assert db.scalar("PRAGMA user_version") == 18
        assert db.scalar("SELECT title FROM conversations") == "keep"
        assert db.scalar("SELECT runtime_key FROM conversation_runs") == "default"
        assert db.scalar("SELECT origin FROM approvals") == "workspace"
        assert db.scalar("SELECT driver_event_id FROM conversation_run_events") is None
        assert db.scalar("SELECT COUNT(*) FROM runtime_sessions") == 0  # no legacy backfill
        assert db.scalar("SELECT session_id FROM opencode_conversations") == "old-native"
        assert db.all("PRAGMA foreign_key_check") == []
        backups = list(tmp_path.glob("old.db.pre-v18-*.bak"))
        assert len(backups) == 1
        with sqlite3.connect(backups[0]) as conn:
            assert conn.execute("PRAGMA user_version").fetchone()[0] == 17
            assert conn.execute("SELECT title FROM conversations").fetchone()[0] == "keep"
            assert "runtime_key" not in {r[1] for r in conn.execute("PRAGMA table_info(conversation_runs)")}
        before = db.conn.total_changes
        init_db(db)
        assert db.conn.total_changes == before
        assert list(tmp_path.glob("old.db.pre-v18-*.bak")) == backups
    finally:
        db.close()


def test_sessions_upsert_unique_native_identity_constraints_and_delete(tmp_path):
    db = Database(str(tmp_path / "sessions.db"))
    init_db(db)
    repo = SessionRepository(db)
    try:
        assert repo.get("conversation", "c", "codex") is None
        first = repo.put(**binding())
        assert first["generation"] == 1 and first["state"] == "unverified"
        changed = repo.put(**binding(native_session_id="new", generation=2, state="ready", model_id="effective"))
        assert changed["created_at"] == first["created_at"] and changed["generation"] == 2
        assert repo.get("conversation", "c", "codex")["model_id"] == "effective"
        with pytest.raises(sqlite3.IntegrityError):
            repo.put(**binding(scope_id="other", native_session_id="new"))
        with pytest.raises(sqlite3.IntegrityError):
            repo.put(**binding(scope_kind="invalid", scope_id="bad"))
        with pytest.raises(sqlite3.IntegrityError):
            repo.put(**binding(state="invalid-state"))
        with pytest.raises(ValueError):
            repo.put(**binding(untrusted_column="value"))
        with pytest.raises(ValueError):
            with db.tx():
                repo.put(**binding(native_session_id="rollback"))
                raise ValueError("rollback")
        assert repo.get("conversation", "c", "codex")["native_session_id"] == "new"
        assert repo.delete("conversation", "c", "codex")
        assert not repo.delete("conversation", "c", "codex")
    finally:
        db.close()


def test_v18_backup_restore_preserves_session_and_new_columns(tmp_path, monkeypatch):
    monkeypatch.setattr(backup.tempfile, "tempdir", str(tmp_path))
    live = tmp_path / "live.db"
    db = Database(str(live))
    init_db(db)
    SessionRepository(db).put(**binding())
    exported, _ = backup.create_backup(db)
    try:
        assert backup.validate_backup_file(exported) == 18
        db.update("runtime_sessions", {"native_session_id": "changed"}, "scope_id=?", ("c",))
        db.close()
        pre = backup.restore_database(exported, live)
        with sqlite3.connect(live) as conn:
            assert conn.execute("SELECT native_session_id FROM runtime_sessions").fetchone()[0] == "native"
            assert "delivery_status" in {r[1] for r in conn.execute("PRAGMA table_info(approvals)")}
        with sqlite3.connect(pre) as conn:
            assert conn.execute("SELECT native_session_id FROM runtime_sessions").fetchone()[0] == "changed"
    finally:
        db.close()
        backup.cleanup_backup(exported)


def test_session_binding_survives_restart(tmp_path):
    path = tmp_path / "restart.db"
    db = Database(str(path))
    init_db(db)
    SessionRepository(db).put(**binding(generation=3, state="ready", last_turn_id="previous"))
    db.close()
    reopened = Database(str(path))
    try:
        init_db(reopened)
        row = SessionRepository(reopened).get("conversation", "c", "codex")
        assert (row["native_session_id"], row["generation"], row["last_turn_id"]) == ("native", 3, "previous")
    finally:
        reopened.close()


@pytest.mark.asyncio
async def test_runtime_events_dedupe_legacy_projection_and_terminal_transaction(make_app):
    rt = FakeRuntime(gated=False)
    api = Api(make_app(runtime_factory=lambda _: rt, mode="real"))
    cid = await new_conv(api)
    run = await turn(api, cid)
    await wait_status(api, run["id"], "succeeded")
    legacy = (await api("runs.events", run_id=run["id"]))["events"]
    assert [e["type"] for e in legacy] == ["queued", "running", "succeeded"]
    assert legacy[0]["data"] == {"run_id": run["id"], "status": "queued"}
    assert legacy[1]["data"] == {"run_id": run["id"], "status": "running"}
    finished = await api("runs.get", run_id=run["id"])
    assert legacy[2]["data"] == {"run_id": run["id"], "status": "succeeded",
                                 "assistant_message_seq": finished["assistant_message_seq"]}
    normal = (await api("runs.events", run_id=run["id"], event_format="normalised-v1"))["events"]
    assert [e["type"] for e in normal] == ["queued", "running", "turn.started", "item.completed", "turn.completed", "succeeded"]
    rows = api.db.all("SELECT * FROM conversation_run_events WHERE run_id=? ORDER BY id", (run["id"],))
    assert rows[-2]["type"] == "runtime" and rows[-1]["type"] == "succeeded"
    assert json.loads(rows[-2]["data_json"])["event"]["type"] == "turn.completed"
    manager = api.app.ctx.runs
    event = DriverEvent(**json.loads(next(r["data_json"] for r in rows if r["type"] == "runtime"))["event"])
    count = len(rows)
    with api.db.tx():
        assert manager._insert_driver_event(event) is None
    assert api.db.scalar("SELECT COUNT(*) FROM conversation_run_events") == count
    assert api.db.scalar("SELECT COUNT(*) FROM usage_events WHERE run_id=?", (run["id"],)) == 1
    streamed = [e async for e in manager.iter_events(run["id"], event_format="normalised-v1") if e]
    assert streamed == normal
    assert [e async for e in manager.iter_events(run["id"], normal[-1]["id"], "normalised-v1")] == []


@pytest.mark.asyncio
async def test_hidden_runtime_pages_advance_replay_cursor(make_app):
    rt = FakeRuntime()
    api = Api(make_app(runtime_factory=lambda _: rt, mode="real"))
    cid = await new_conv(api)
    run = await turn(api, cid)
    await rt.started.wait()
    manager = api.app.ctx.runs
    for seq in range(1, 1002):
        await manager._driver_event(DriverEvent(1, f"synthetic-{seq}", run["id"], run["id"], seq,
            "fake", "default", ts(), "runtime.notice", {"code": "test", "message": "test"}))
    rt.gate.set()
    await wait_status(api, run["id"], "succeeded")
    legacy = manager.list_events(run["id"])
    assert [e["type"] for e in legacy] == ["queued", "running", "succeeded"]
    assert [e async for e in manager.iter_events(run["id"]) if e] == legacy


@pytest.mark.asyncio
async def test_opt_in_stream_envelopes_and_default_legacy_bytes(make_app):
    rt = FakeRuntime(gated=False)
    api = Api(make_app(runtime_factory=lambda _: rt, mode="real"))
    run = await turn(api, await new_conv(api))
    await wait_status(api, run["id"], "succeeded")
    from backend.pyworker.streams import StreamRegistry
    messages = []

    async def emit(message):
        messages.append(message)

    registry = StreamRegistry(api.app.ctx, emit)
    api.app.ctx.streams = registry
    for format_ in ("legacy", "normalised-v1"):
        params = {} if format_ == "legacy" else {"event_format": format_}
        response = await api("stream.open", topic="run", id=run["id"], **params)
        sid = response["stream_id"]
        task = registry._streams[sid]
        await task
        seen = [m for m in messages if m["stream_id"] == sid and m["type"] == "stream"]
        expected = api.app.ctx.runs.list_events(run["id"], event_format=format_)
        assert [json.dumps(m, ensure_ascii=False) for m in seen] == [json.dumps({
            "type": "stream", "stream_id": sid, "event": e["type"], "id": e["id"], "data": e["data"]
        }, ensure_ascii=False) for e in expected]
        assert messages[-1] == {"type": "stream_end", "stream_id": sid, "reason": "complete"}
    assert (await api.fail("runs.events", run_id=run["id"], event_format="wrong")).status == 422
    assert (await api.fail("stream.open", topic="run", id=run["id"], event_format="wrong")).status == 422


@pytest.mark.asyncio
async def test_cancel_persists_first_and_late_driver_terminal_is_not_added(make_app):
    rt = FakeRuntime(ignore_abort=True)
    api = Api(make_app(runtime_factory=lambda _: rt, mode="real"))
    run = await turn(api, await new_conv(api))
    await rt.started.wait()
    await api("runs.cancel", run_id=run["id"])
    before = api.db.scalar("SELECT COUNT(*) FROM conversation_run_events")
    await api.app.ctx.runs._driver_event(DriverEvent(1, "late", run["id"], run["id"], 99,
        "fake", "default", ts(), "turn.completed", {"status": "succeeded", "final_item_ids": []}))
    assert api.db.scalar("SELECT COUNT(*) FROM conversation_run_events") == before
    assert (await api("runs.get", run_id=run["id"]))["status"] == "cancelled"
    assert rt.aborted == [f"conv-run-{run['id']}"]


def test_v18_additive_columns_and_partial_unique_indexes(tmp_path):
    from backend.pyworker.db.schema import DRIVER_COLUMNS

    db = Database(str(tmp_path / "indexes.db"))
    init_db(db)
    try:
        for table, columns in DRIVER_COLUMNS.items():
            assert set(columns) <= {row["name"] for row in db.all(f"PRAGMA table_info({table})")}
        db.execute("INSERT INTO conversation_runs(id,engine,model_id,timeout,status,created_at) "
                   "VALUES('r','mock','m',90,'succeeded',?)", (ts(),))
        values = {"run_id": "r", "action_summary": "test", "scope": "test", "status": "pending",
                  "created_at": ts(), "expires_at": ts()}
        db.insert("approvals", {**values, "id": "a"})
        db.insert("approvals", {**values, "id": "b"})  # legacy null request IDs coexist
        db.insert("approvals", {**values, "id": "c", "runtime_request_id": "request"})
        with pytest.raises(sqlite3.IntegrityError):
            db.insert("approvals", {**values, "id": "d", "runtime_request_id": "request"})
        for driver_id in (None, None, "event"):
            db.execute("INSERT INTO conversation_run_events(run_id,type,data_json,created_at,driver_event_id) "
                       "VALUES('r','runtime','{}',?,?)", (ts(), driver_id))
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("INSERT INTO conversation_run_events(run_id,type,data_json,created_at,driver_event_id) "
                       "VALUES('r','runtime','{}',?,'event')", (ts(),))
        SessionRepository(db).put(**binding(scope_kind="ceo", scope_id="no-conversation-row"))
        assert db.all("PRAGMA foreign_key_check") == []
    finally:
        db.close()
