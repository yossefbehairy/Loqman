"""Schema creation/upgrade, and compatibility with databases written by the
legacy SQLAlchemy backend (the user's existing agentos.db must keep working)."""

import sqlite3

import pytest

from backend.pyworker.db.core import Database
from backend.pyworker.db.migrate import SchemaTooNew, init_db
from backend.pyworker.db.schema import SCHEMA_VERSION, TABLES


def fresh(path):
    db = Database(str(path))
    init_db(db)
    return db


def test_fresh_database_has_every_table_and_version(tmp_path):
    db = fresh(tmp_path / "n.db")
    names = {r["name"] for r in db.all("SELECT name FROM sqlite_master WHERE type='table'")}
    assert set(TABLES) <= names
    assert db.scalar("PRAGMA user_version") == SCHEMA_VERSION == 16
    assert db.scalar("PRAGMA foreign_keys") == 1
    assert db.scalar("PRAGMA journal_mode") == "wal"
    # the partial unique index that guarantees one active run per conversation
    idx = {r["name"] for r in db.all("PRAGMA index_list(conversation_runs)")}
    assert {"uq_conversation_runs_one_active", "uq_runs_request_key"} <= idx
    init_db(db)  # idempotent
    db.close()


def test_new_ddl_matches_legacy_sqlalchemy_schema(tmp_path):
    sa = pytest.importorskip("sqlalchemy")
    legacy = pytest.importorskip("backend.app.db.session")
    from backend.app.db import models, skill_models  # noqa: F401

    path = tmp_path / "legacy.db"
    eng = sa.create_engine(f"sqlite:///{path}")
    legacy.init_db(eng)
    eng.dispose()
    new = fresh(tmp_path / "new.db")
    old = sqlite3.connect(path)
    old.row_factory = sqlite3.Row

    def cols(con, t):
        return [(r["name"], r["type"], r["notnull"], r["pk"]) for r in con.execute(f"PRAGMA table_info({t})")]

    def idx(con, t):
        out = set()
        for r in con.execute(f"PRAGMA index_list({t})"):
            if r["name"].startswith("sqlite_autoindex"):
                continue
            columns = tuple(c["name"] for c in con.execute(f"PRAGMA index_info({r['name']})"))
            out.add((r["name"], r["unique"], r["partial"], columns))
        return out

    old_tables = {r["name"] for r in old.execute("SELECT name FROM sqlite_master WHERE type='table'")
                  if not r["name"].startswith("sqlite_")}
    new_tables = {r["name"] for r in new.all("SELECT name FROM sqlite_master WHERE type='table'")
                  if not r["name"].startswith("sqlite_")}
    assert old_tables == new_tables
    for t in sorted(old_tables):
        assert cols(old, t) == cols(new.conn, t), t
        assert idx(old, t) == idx(new.conn, t), t
        assert [tuple(r) for r in old.execute(f"PRAGMA foreign_key_list({t})")] == \
               [tuple(r) for r in new.conn.execute(f"PRAGMA foreign_key_list({t})")], t
    assert old.execute("PRAGMA user_version").fetchone()[0] == new.scalar("PRAGMA user_version")
    old.close()
    new.close()


def test_database_written_by_legacy_backend_opens_and_is_served(tmp_path):
    sa = pytest.importorskip("sqlalchemy")
    legacy = pytest.importorskip("backend.app.db.session")
    from sqlalchemy.orm import sessionmaker
    from backend.app.db.models import Conversation, ConversationMessage, Mission, Workspace

    path = tmp_path / "user.db"
    eng = sa.create_engine(f"sqlite:///{path}")
    legacy.init_db(eng)
    S = sessionmaker(bind=eng)
    with S() as s:
        s.add(Conversation(id="c1", title="old chat"))
        s.add(ConversationMessage(conversation_id="c1", role="user", content="hello"))
        s.add(Mission(id="m1", title="m", objective="obj", status="succeeded"))
        s.commit()
    eng.dispose()

    from backend.pyworker.app import Application
    import asyncio

    async def go():
        app = Application.create(db_path=str(path), runtime=object())
        try:
            convs = (await app.handle("conversations.list", {}))["conversations"]
            msgs = (await app.handle("conversations.messages", {"conversation_id": "c1"}))["messages"]
            mission = await app.handle("missions.get", {"mission_id": "m1"})
            assert [c["id"] for c in convs] == ["c1"] and msgs[0]["content"] == "hello"
            assert mission["status"] == "succeeded" and mission["objective"] == "obj"
            # the legacy writer's datetimes parse and render
            assert msgs[0]["created_at"] and convs[0]["created_at"]
            # no upgrade was needed -> no backup file
            assert not list(tmp_path.glob("user.db.pre-v*.bak"))
        finally:
            app.ctx.db.close()

    asyncio.run(go())


def test_existing_sqlite_is_backed_up_before_upgrade(tmp_path):
    path = tmp_path / "old.db"
    with sqlite3.connect(path) as con:
        con.execute("CREATE TABLE missions (id VARCHAR PRIMARY KEY)")
        con.execute("INSERT INTO missions (id) VALUES ('existing')")
    db = Database(str(path))
    init_db(db)
    assert "source_conversation_id" in {r["name"] for r in db.all("PRAGMA table_info(missions)")}
    assert db.scalar("PRAGMA user_version") == SCHEMA_VERSION
    assert db.scalar("SELECT id FROM missions") == "existing"
    backups = list(tmp_path.glob("old.db.pre-v*.bak"))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as con:
        assert con.execute("SELECT id FROM missions").fetchone() == ("existing",)
        assert "source_conversation_id" not in {r[1] for r in con.execute("PRAGMA table_info(missions)")}
    init_db(db)
    assert len(list(tmp_path.glob("old.db.pre-v*.bak"))) == 1
    db.close()


def test_newer_schema_fails_before_changes(tmp_path):
    path = tmp_path / "future.db"
    with sqlite3.connect(path) as con:
        con.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
        con.execute("CREATE TABLE marker (value TEXT)")
    db = Database(str(path))
    with pytest.raises(SchemaTooNew, match="newer"):
        init_db(db)
    assert [r["name"] for r in db.all("SELECT name FROM sqlite_master WHERE type='table'")] == ["marker"]
    db.close()


def test_run_table_gets_request_fields_on_upgrade(tmp_path):
    path = tmp_path / "version4.db"
    with sqlite3.connect(path) as con:
        con.execute("PRAGMA user_version = 4")
        con.execute("CREATE TABLE conversations (id VARCHAR(36) PRIMARY KEY, title VARCHAR(255), "
                    "created_at DATETIME, updated_at DATETIME)")
        con.execute("INSERT INTO conversations VALUES ('conversation', 'old', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)")
        con.execute("CREATE TABLE conversation_runs (id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, "
                    "status TEXT NOT NULL, engine VARCHAR(32) NOT NULL, model_id VARCHAR(128) NOT NULL, "
                    "timeout INTEGER NOT NULL, created_at DATETIME NOT NULL)")
        con.execute("INSERT INTO conversation_runs VALUES ('old-run','conversation','succeeded','codex','model',90,CURRENT_TIMESTAMP)")
    db = Database(str(path))
    init_db(db)
    row = db.one("SELECT id, request_key, variant FROM conversation_runs")
    assert (row["id"], row["request_key"], row["variant"]) == ("old-run", None, None)
    assert db.scalar("PRAGMA user_version") == SCHEMA_VERSION
    assert len(list(tmp_path.glob("version4.db.pre-v*.bak"))) == 1
    db.close()


def legacy_database(path):
    with sqlite3.connect(path) as con:
        con.executescript("""
        PRAGMA user_version=15;
        CREATE TABLE conversations (id VARCHAR(36) PRIMARY KEY, title VARCHAR(255) NOT NULL,
            created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL);
        INSERT INTO conversations VALUES ('chat', 'Original', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);
        CREATE TABLE conversation_runs (
            id VARCHAR(36) PRIMARY KEY, conversation_id VARCHAR(36) NOT NULL REFERENCES conversations(id),
            engine VARCHAR(32) NOT NULL, model_id VARCHAR(128) NOT NULL, variant VARCHAR(64),
            request_key VARCHAR(128), skills_json TEXT, input_files_json TEXT,
            timeout INTEGER NOT NULL, status VARCHAR(16) NOT NULL,
            user_message_seq INTEGER, assistant_message_seq INTEGER, error TEXT,
            created_at DATETIME NOT NULL, started_at DATETIME, finished_at DATETIME);
        INSERT INTO conversation_runs (id,conversation_id,engine,model_id,request_key,timeout,status,created_at)
            VALUES ('run','chat','codex','model','request',90,'succeeded',CURRENT_TIMESTAMP);
        CREATE TABLE conversation_run_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id VARCHAR(36) NOT NULL REFERENCES conversation_runs(id) ON DELETE CASCADE,
            type VARCHAR(16) NOT NULL, data_json TEXT NOT NULL, created_at DATETIME NOT NULL);
        INSERT INTO conversation_run_events (run_id,type,data_json,created_at)
            VALUES ('run','succeeded','{}',CURRENT_TIMESTAMP);
        """)


def test_rebuild_keeps_parent_child_data_and_indexes(tmp_path):
    path = tmp_path / "legacy.db"
    legacy_database(path)
    db = Database(str(path))
    init_db(db)
    cols = {r["name"]: r for r in db.all("PRAGMA table_info(conversation_runs)")}
    assert cols["conversation_id"]["notnull"] == 0
    assert {"kind", "mission_id", "mission_task_id", "attempt", "result_text"} <= set(cols)
    row = db.one("SELECT id,conversation_id,kind,request_key FROM conversation_runs")
    assert (row["id"], row["conversation_id"], row["kind"], row["request_key"]) == ("run", "chat", "conversation", "request")
    assert db.scalar("SELECT run_id FROM conversation_run_events") == "run"
    assert db.all("PRAGMA foreign_key_check") == []
    assert db.scalar("PRAGMA foreign_keys") == 1
    assert db.scalar("PRAGMA user_version") == SCHEMA_VERSION
    idx = {r["name"] for r in db.all("PRAGMA index_list(conversation_runs)")}
    assert {"uq_conversation_runs_one_active", "uq_runs_request_key"} <= idx
    init_db(db)
    backups = list(tmp_path.glob("legacy.db.pre-v*.bak"))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as con:
        assert con.execute("PRAGMA user_version").fetchone()[0] == 15
        assert con.execute("SELECT count(*) FROM conversation_run_events").fetchone()[0] == 1
    db.close()


def test_rebuild_refuses_unknown_columns_without_dropping_data(tmp_path):
    path = tmp_path / "unknown.db"
    legacy_database(path)
    with sqlite3.connect(path) as con:
        con.execute("ALTER TABLE conversation_runs ADD COLUMN custom_field TEXT")
        con.execute("UPDATE conversation_runs SET custom_field='keep'")
    db = Database(str(path))
    with pytest.raises(RuntimeError, match="unrecognized"):
        init_db(db)
    assert db.scalar("SELECT custom_field FROM conversation_runs") == "keep"
    assert db.scalar("PRAGMA user_version") == 15
    db.close()


def test_failed_rebuild_rolls_back_and_restores_foreign_key_checks(tmp_path):
    path = tmp_path / "broken.db"
    legacy_database(path)
    with sqlite3.connect(path) as con:
        con.execute("PRAGMA foreign_keys=OFF")
        con.execute("UPDATE conversation_run_events SET run_id='missing'")
    db = Database(str(path))
    with pytest.raises(RuntimeError, match="foreign-key"):
        init_db(db)
    cols = {r["name"]: r for r in db.all("PRAGMA table_info(conversation_runs)")}
    assert cols["conversation_id"]["notnull"] == 1  # table untouched
    assert db.scalar("SELECT count(*) FROM conversation_runs") == 1
    assert db.scalar("PRAGMA foreign_keys") == 1  # restored
    db.close()
