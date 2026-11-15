"""Schema creation + additive upgrades (port of the legacy ``init_db``).

Compatible with databases created by the SQLAlchemy backend: same tables,
same ``PRAGMA user_version`` (16). Existing files are backed up with the
SQLite online-backup API before any upgrade.
"""

from __future__ import annotations

import os
import re
import sqlite3
from contextlib import closing
from datetime import datetime, timezone

from .core import Database
from .schema import DRIVER_COLUMNS, INDEXES, SCHEMA_VERSION, TABLES


class SchemaTooNew(RuntimeError):
    pass


class ForeignDatabase(RuntimeError):
    """The file is a sqlite database that is not an AgentOS one: never write into it."""


def _columns(db: Database, table: str) -> dict:
    return {r["name"]: r for r in db.all(f"PRAGMA table_info({table})")}


def _tables(db: Database) -> set:
    return {r["name"] for r in db.all("SELECT name FROM sqlite_master WHERE type='table'")}


def _backup(db: Database) -> str | None:
    if not db.path or db.path == ":memory:" or not os.path.isfile(db.path):
        return None
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    dest = f"{db.path}.pre-v{SCHEMA_VERSION}-{stamp}.bak"
    with closing(sqlite3.connect(dest)) as dst:
        db.conn.backup(dst)
    return dest


def _if_not_exists(ddl: str) -> str:
    return re.sub(r"^CREATE (UNIQUE )?INDEX ", lambda m: f"CREATE {m.group(1) or ''}INDEX IF NOT EXISTS ", ddl)


def _add_column(db: Database, table: str, column: str, decl: str) -> None:
    if column not in _columns(db, table):
        db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


def _upgrade_run_table(db: Database) -> None:
    cols = _columns(db, "conversation_runs")
    new_fields = {"kind", "mission_id", "mission_task_id", "attempt", "result_text"}
    if cols["conversation_id"]["notnull"] == 0 and new_fields.issubset(cols):
        return
    wanted = set(_columns_from_ddl(TABLES["conversation_runs"])) | {"tool_config_json", "profile_snapshot_json"}
    if set(cols) - wanted:
        raise RuntimeError("Run table contains unrecognized columns; automatic rebuild refused")
    names = ", ".join(cols)
    enabled = db.scalar("PRAGMA foreign_keys")
    db.execute("PRAGMA foreign_keys=OFF")
    try:
        db.execute("BEGIN IMMEDIATE")
        try:
            db.execute(TABLES["conversation_runs"].replace(
                "CREATE TABLE conversation_runs", "CREATE TABLE conversation_runs_upgrade", 1))
            db.execute(f"INSERT INTO conversation_runs_upgrade ({names}) SELECT {names} FROM conversation_runs")
            db.execute("DROP TABLE conversation_runs")
            db.execute("ALTER TABLE conversation_runs_upgrade RENAME TO conversation_runs")
            for ddl in INDEXES.get("conversation_runs", []):
                db.execute(_if_not_exists(ddl))
            if db.one("PRAGMA foreign_key_check") is not None:
                raise RuntimeError("Run schema migration failed foreign-key validation")
            db.execute("COMMIT")
        except BaseException:
            db.execute("ROLLBACK")
            raise
    finally:
        db.execute(f"PRAGMA foreign_keys={1 if enabled else 0}")


def _columns_from_ddl(ddl: str) -> list:
    out = []
    for line in ddl.splitlines()[1:]:
        line = line.strip().rstrip(",")
        if not line or line.startswith(("PRIMARY", "FOREIGN", "UNIQUE", "CONSTRAINT", ")")):
            continue
        out.append(line.split()[0])
    return out


def init_db(db: Database) -> None:
    version = db.scalar("PRAGMA user_version") or 0
    if version > SCHEMA_VERSION:
        raise SchemaTooNew(f"Database schema version {version} is newer than supported version {SCHEMA_VERSION}")
    existing = _tables(db)
    own = existing - {"sqlite_sequence"}
    if own:
        # These columns were introduced by additive migrations below.
        added = {
            "missions": {"source_conversation_id", "request_fingerprint"},
            "mission_tasks": {"expected_output"},
            "agent_profiles": {"engine"},
            "usage_events": {"tokens_cache_read", "run_id"},
            "conversations": {"workspace_id"},
            "conversation_runs": {
                "variant", "request_key", "skills_json", "input_files_json",
                "kind", "mission_id", "mission_task_id", "attempt", "result_text",
                "user_message_seq", "assistant_message_seq", "error",
                "started_at", "finished_at", "tool_config_json", "profile_snapshot_json",
            },
        }
        for table, columns in DRIVER_COLUMNS.items():
            added.setdefault(table, set()).update(columns)
        recognized = own & set(TABLES)
        if not recognized:
            raise ForeignDatabase("The file is not an AgentOS database; refusing to modify it.")
        for name in recognized:
            columns = _columns(db, name)
            required = set(_columns_from_ddl(TABLES[name])) - added.get(name, set())
            if not required.issubset(columns) or not any(c["pk"] for c in columns.values()):
                raise ForeignDatabase(
                    f"Table {name} does not match an AgentOS schema; refusing to modify it.")
    db.enable_wal()
    if version < SCHEMA_VERSION and own:
        _backup(db)
    created = []
    for name, ddl in TABLES.items():
        if name not in existing:
            db.execute(ddl)
            created.append(name)
    # Like SQLAlchemy create_all: a new table brings its indexes.
    for name in created:
        for ddl in INDEXES.get(name, []):
            db.execute(_if_not_exists(ddl))
    existing = _tables(db)

    if "missions" in existing:
        _add_column(db, "missions", "source_conversation_id", "VARCHAR(128)")
        _add_column(db, "missions", "request_fingerprint", "VARCHAR(64)")
        _add_column(db, "mission_tasks", "expected_output", "TEXT")
    if "agent_profiles" in existing:
        _add_column(db, "agent_profiles", "engine", "VARCHAR(32)")
    if "usage_events" in existing:
        _add_column(db, "usage_events", "tokens_cache_read", "INTEGER")
        _add_column(db, "usage_events", "run_id", "VARCHAR(36)")
    if "conversation_runs" in existing:
        _upgrade_run_table(db)
        _add_column(db, "conversation_runs", "variant", "VARCHAR(64)")
        _add_column(db, "conversation_runs", "request_key", "VARCHAR(128)")
        _add_column(db, "conversation_runs", "skills_json", "TEXT")
        _add_column(db, "conversation_runs", "input_files_json", "TEXT")
        _add_column(db, "conversation_runs", "tool_config_json", "TEXT")
        _add_column(db, "conversation_runs", "profile_snapshot_json", "TEXT")
    if "conversations" in existing:
        _add_column(db, "conversations", "workspace_id",
                    "VARCHAR(36) REFERENCES workspaces(id) ON DELETE SET NULL")
        _add_column(db, "conversations", "profile_id", "VARCHAR(64)")
    for table, columns in DRIVER_COLUMNS.items():
        for column, declaration in columns.items():
            _add_column(db, table, column, declaration)
    # Indexes the legacy upgrade path added explicitly to pre-existing tables.
    explicit = {"conversations": ("ix_conversations_workspace_id",),
                "missions": ("ix_missions_source_conversation_id",),
                "usage_events": ("ix_usage_events_run_id",),
                "conversation_runs": ("uq_runs_request_key", "uq_conversation_runs_one_active"),
                "conversation_run_events": ("uq_run_driver_event",),
                "approvals": ("uq_approval_runtime_request",)}
    for table, names in explicit.items():
        if table in existing and table not in created:
            for ddl in INDEXES.get(table, []):
                if any(f" {n} " in ddl for n in names):
                    db.execute(_if_not_exists(ddl))
    if version < SCHEMA_VERSION:
        db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
