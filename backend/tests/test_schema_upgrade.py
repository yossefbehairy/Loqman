import sqlite3

import pytest
from sqlalchemy import create_engine, inspect

from backend.app.db.session import SCHEMA_VERSION, init_db


def test_existing_sqlite_is_backed_up_before_upgrade(tmp_path):
    path = tmp_path / "old.db"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE missions (id VARCHAR PRIMARY KEY)")
        db.execute("INSERT INTO missions (id) VALUES ('existing')")
    engine = create_engine(f"sqlite:///{path}")
    try:
        init_db(engine)
        assert "source_conversation_id" in {c["name"] for c in inspect(engine).get_columns("missions")}
        with engine.connect() as db:
            assert db.exec_driver_sql("PRAGMA user_version").scalar_one() == SCHEMA_VERSION
            assert db.exec_driver_sql("SELECT id FROM missions").scalar_one() == "existing"
        backups = list(tmp_path.glob("old.db.pre-v*.bak"))
        assert len(backups) == 1
        with sqlite3.connect(backups[0]) as db:
            assert db.execute("SELECT id FROM missions").fetchone() == ("existing",)
            assert "source_conversation_id" not in {row[1] for row in db.execute("PRAGMA table_info(missions)")}
        init_db(engine)
        assert len(list(tmp_path.glob("old.db.pre-v*.bak"))) == 1
    finally:
        engine.dispose()


def test_newer_schema_fails_before_changes(tmp_path):
    path = tmp_path / "future.db"
    with sqlite3.connect(path) as db:
        db.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
        db.execute("CREATE TABLE marker (value TEXT)")
    engine = create_engine(f"sqlite:///{path}")
    try:
        with pytest.raises(RuntimeError, match="newer"):
            init_db(engine)
        assert inspect(engine).get_table_names() == ["marker"]
    finally:
        engine.dispose()


def test_run_table_gets_request_fields_on_upgrade(tmp_path):
    path = tmp_path / "version4.db"
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA user_version = 4")
        db.execute("CREATE TABLE conversations (id VARCHAR(36) PRIMARY KEY, title VARCHAR(255), created_at DATETIME, updated_at DATETIME)")
        db.execute("INSERT INTO conversations VALUES ('conversation', 'old', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)")
        db.execute("CREATE TABLE conversation_runs (id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, status TEXT NOT NULL, engine VARCHAR(32) NOT NULL, model_id VARCHAR(128) NOT NULL, timeout INTEGER NOT NULL, created_at DATETIME NOT NULL)")
        db.execute("INSERT INTO conversation_runs VALUES ('old-run', 'conversation', 'succeeded', 'codex', 'model', 90, CURRENT_TIMESTAMP)")
    engine = create_engine(f"sqlite:///{path}")
    try:
        init_db(engine)
        with engine.connect() as db:
            row = db.exec_driver_sql("SELECT id, request_key, variant FROM conversation_runs").one()
            assert tuple(row) == ("old-run", None, None)
            assert db.exec_driver_sql("PRAGMA user_version").scalar_one() == SCHEMA_VERSION
        assert len(list(tmp_path.glob("version4.db.pre-v*.bak"))) == 1
    finally:
        engine.dispose()
