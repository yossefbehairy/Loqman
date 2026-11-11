"""
Database Session and Engine Management with SQLite Pragmas
Enforces WAL mode, foreign keys, and busy timeout on every connection.
"""

from typing import Generator
from pathlib import Path
from datetime import datetime, timezone
from contextlib import closing
import sqlite3
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import declarative_base, sessionmaker, Session
from backend.app.config import settings

# Create engine with thread-safety for SQLite
connect_args = {"check_same_thread": False} if settings.SQLITE_DB_URL.startswith("sqlite") else {}

engine = create_engine(
    settings.SQLITE_DB_URL,
    connect_args=connect_args,
    echo=settings.DEBUG
)

# Mandatory SQLite Pragmas on Connect
@event.listens_for(Engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    """
    Enforces SQLite foreign keys, WAL journal mode, and busy timeout.
    """
    if "sqlite" in str(dbapi_connection.__class__).lower():
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON;")
        cursor.execute("PRAGMA journal_mode=WAL;")
        cursor.execute("PRAGMA busy_timeout=5000;")
        cursor.execute("PRAGMA synchronous=NORMAL;")
        cursor.close()


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db() -> Generator[Session, None, None]:
    """
    FastAPI dependency yielding a database session per request.
    Ensures session closure and rollback on error.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


SCHEMA_VERSION = 16


def _backup_sqlite(eng) -> str | None:
    """Make a consistent SQLite backup before upgrading an existing file."""
    database = eng.url.database
    if not database or database == ":memory:":
        return None
    source = Path(database).resolve()
    if not source.is_file():
        return None
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    destination = source.with_name(f"{source.name}.pre-v{SCHEMA_VERSION}-{stamp}.bak")
    # The online backup API includes committed WAL contents.
    with closing(sqlite3.connect(str(source))) as src, closing(sqlite3.connect(str(destination))) as dst:
        src.backup(dst)
    return str(destination)


def init_db(target_engine=None):
    """
    Initializes all tables in the SQLite database.
    """
    from backend.app.db import models, skill_models  # noqa: F401 - register tables

    eng = target_engine or engine
    is_sqlite = eng.dialect.name == "sqlite"
    if is_sqlite:
        with eng.connect() as connection:
            version = connection.exec_driver_sql("PRAGMA user_version").scalar_one()
        if version > SCHEMA_VERSION:
            raise RuntimeError(f"Database schema version {version} is newer than supported version {SCHEMA_VERSION}")
        if version < SCHEMA_VERSION and inspect(eng).get_table_names():
            _backup_sqlite(eng)
    Base.metadata.create_all(bind=eng)
    # SQLite create_all does not add columns to tables that already exist.
    if "missions" in inspect(eng).get_table_names():
        columns = {column["name"] for column in inspect(eng).get_columns("missions")}
        if "source_conversation_id" not in columns:
            with eng.begin() as connection:
                connection.execute(text("ALTER TABLE missions ADD COLUMN source_conversation_id VARCHAR(128)"))
                connection.execute(text(
                    "CREATE INDEX IF NOT EXISTS ix_missions_source_conversation_id "
                    "ON missions (source_conversation_id)"
                ))
        if "request_fingerprint" not in columns:
            with eng.begin() as connection:
                connection.execute(text("ALTER TABLE missions ADD COLUMN request_fingerprint VARCHAR(64)"))
    if "mission_tasks" in inspect(eng).get_table_names():
        columns = {column["name"] for column in inspect(eng).get_columns("mission_tasks")}
        if "expected_output" not in columns:
            with eng.begin() as connection:
                connection.execute(text("ALTER TABLE mission_tasks ADD COLUMN expected_output TEXT"))
    if "agent_profiles" in inspect(eng).get_table_names():
        columns = {column["name"] for column in inspect(eng).get_columns("agent_profiles")}
        if "engine" not in columns:
            with eng.begin() as connection:
                connection.execute(text("ALTER TABLE agent_profiles ADD COLUMN engine VARCHAR(32)"))
    if "usage_events" in inspect(eng).get_table_names():
        columns = {column["name"] for column in inspect(eng).get_columns("usage_events")}
        if "tokens_cache_read" not in columns:
            with eng.begin() as connection:
                connection.execute(text("ALTER TABLE usage_events ADD COLUMN tokens_cache_read INTEGER"))
        if "run_id" not in columns:
            with eng.begin() as connection:
                connection.execute(text("ALTER TABLE usage_events ADD COLUMN run_id VARCHAR(36)"))
        with eng.begin() as connection:
            connection.execute(text("CREATE INDEX IF NOT EXISTS ix_usage_events_run_id ON usage_events (run_id)"))
    if "conversation_runs" in inspect(eng).get_table_names():
        from backend.app.db.run_migration import upgrade_run_table
        upgrade_run_table(eng, Base.metadata)
        columns = {column["name"] for column in inspect(eng).get_columns("conversation_runs")}
        with eng.begin() as connection:
            if "variant" not in columns:
                connection.execute(text("ALTER TABLE conversation_runs ADD COLUMN variant VARCHAR(64)"))
            if "request_key" not in columns:
                connection.execute(text("ALTER TABLE conversation_runs ADD COLUMN request_key VARCHAR(128)"))
            if "skills_json" not in columns:
                connection.execute(text("ALTER TABLE conversation_runs ADD COLUMN skills_json TEXT"))
            if "input_files_json" not in columns:
                connection.execute(text("ALTER TABLE conversation_runs ADD COLUMN input_files_json TEXT"))
            connection.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_runs_request_key ON conversation_runs (conversation_id, request_key)"))
            connection.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_conversation_runs_one_active ON conversation_runs (conversation_id) WHERE status IN ('queued', 'running')"))
    if "conversations" in inspect(eng).get_table_names():
        columns = {column["name"] for column in inspect(eng).get_columns("conversations")}
        with eng.begin() as connection:
            if "workspace_id" not in columns:
                connection.execute(text("ALTER TABLE conversations ADD COLUMN workspace_id VARCHAR(36) REFERENCES workspaces(id) ON DELETE SET NULL"))
            connection.execute(text("CREATE INDEX IF NOT EXISTS ix_conversations_workspace_id ON conversations (workspace_id)"))
    if is_sqlite and version < SCHEMA_VERSION:
        with eng.begin() as connection:
            connection.exec_driver_sql(f"PRAGMA user_version = {SCHEMA_VERSION}")
