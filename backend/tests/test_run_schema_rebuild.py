import sqlite3

import pytest
from sqlalchemy import create_engine, inspect

from backend.app.db.session import init_db, SCHEMA_VERSION


def legacy_database(path):
    with sqlite3.connect(path) as db:
        db.executescript("""
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
            id INTEGER PRIMARY KEY AUTOINCREMENT, run_id VARCHAR(36) NOT NULL REFERENCES conversation_runs(id) ON DELETE CASCADE,
            type VARCHAR(16) NOT NULL, data_json TEXT NOT NULL, created_at DATETIME NOT NULL);
        INSERT INTO conversation_run_events (run_id,type,data_json,created_at)
            VALUES ('run','succeeded','{}',CURRENT_TIMESTAMP);
        """)


def test_rebuild_keeps_parent_child_data_and_indexes(tmp_path):
    path = tmp_path / "legacy.db"
    legacy_database(path)
    engine = create_engine(f"sqlite:///{path}")
    try:
        init_db(engine)
        columns = {c["name"]: c for c in inspect(engine).get_columns("conversation_runs")}
        assert columns["conversation_id"]["nullable"]
        assert {"kind", "mission_id", "mission_task_id", "attempt", "result_text"}.issubset(columns)
        with engine.connect() as db:
            assert tuple(db.exec_driver_sql("SELECT id,conversation_id,kind,request_key FROM conversation_runs").one()) == ("run", "chat", "conversation", "request")
            assert db.exec_driver_sql("SELECT run_id FROM conversation_run_events").scalar_one() == "run"
            assert db.exec_driver_sql("PRAGMA foreign_key_check").fetchall() == []
            assert db.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
            assert db.exec_driver_sql("PRAGMA user_version").scalar_one() == SCHEMA_VERSION
        indexes = {index["name"] for index in inspect(engine).get_indexes("conversation_runs")}
        assert {"uq_conversation_runs_one_active", "uq_runs_request_key"}.issubset(indexes)
        init_db(engine)
        assert len(list(tmp_path.glob("legacy.db.pre-v*.bak"))) == 1
        backup = next(tmp_path.glob("legacy.db.pre-v*.bak"))
        with sqlite3.connect(backup) as db:
            assert db.execute("PRAGMA user_version").fetchone()[0] == 15
            assert db.execute("SELECT count(*) FROM conversation_run_events").fetchone()[0] == 1
    finally:
        engine.dispose()


def test_rebuild_refuses_unknown_columns_without_dropping_data(tmp_path):
    path = tmp_path / "unknown.db"
    legacy_database(path)
    with sqlite3.connect(path) as db:
        db.execute("ALTER TABLE conversation_runs ADD COLUMN custom_field TEXT")
        db.execute("UPDATE conversation_runs SET custom_field='keep'")
    engine = create_engine(f"sqlite:///{path}")
    try:
        with pytest.raises(RuntimeError, match="unrecognized"):
            init_db(engine)
        with engine.connect() as db:
            assert db.exec_driver_sql("SELECT custom_field FROM conversation_runs").scalar_one() == "keep"
            assert db.exec_driver_sql("PRAGMA user_version").scalar_one() == 15
    finally:
        engine.dispose()


def test_failed_rebuild_rolls_back_and_restores_foreign_key_checks(tmp_path):
    path = tmp_path / "broken.db"
    legacy_database(path)
    with sqlite3.connect(path) as db:
        db.execute("UPDATE conversation_run_events SET run_id='missing'")
    engine = create_engine(f"sqlite:///{path}")
    try:
        with pytest.raises(RuntimeError, match="foreign-key"):
            init_db(engine)
        columns = {c["name"]: c for c in inspect(engine).get_columns("conversation_runs")}
        assert columns["conversation_id"]["nullable"] is False
        assert "kind" not in columns
        assert "conversation_runs_upgrade" not in inspect(engine).get_table_names()
        with engine.connect() as db:
            assert db.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
            assert db.exec_driver_sql("SELECT id FROM conversation_runs").scalar_one() == "run"
    finally:
        engine.dispose()
