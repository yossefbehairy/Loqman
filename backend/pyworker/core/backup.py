"""
SQLite online backup (export) and offline restore helpers.

Backup uses the SQLite online-backup API on a connection borrowed from the
application engine, so committed WAL content is included and writers are not
blocked for long. The result is a single self-contained file (no -wal/-shm).

Restore is NEVER exposed over HTTP; see restore_database() / the CLI in
backend/app/db/restore_cli.py and docs/BACKUP-RESTORE.md.
"""

from __future__ import annotations

import shutil
import atexit
import os
import re
import sqlite3
import subprocess
import tempfile
import threading
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from backend.pyworker.db.core import Database

# Refuse to export absurdly large databases over HTTP (bounded temp usage).
MAX_BACKUP_BYTES = 1024 * 1024 * 1024  # 1 GiB

_backup_lock = threading.Lock()
_backup_dirs: set[Path] = set()


def _cleanup_at_exit():
    for directory in tuple(_backup_dirs):
        _rm(directory)


atexit.register(_cleanup_at_exit)


def _restrict_windows_directory(directory: Path) -> None:
    if os.name != "nt":
        return
    # Remove inherited ACEs and grant only the current user's SID full access.
    identity = subprocess.run(["whoami", "/user", "/fo", "csv", "/nh"], capture_output=True, text=True, check=True)
    match = re.search(r'S-1-(?:\d+-)+\d+', identity.stdout)
    if not match:
        raise BackupError("Could not determine current user SID")
    subprocess.run(["icacls", str(directory), "/inheritance:r", "/grant:r", f"*{match.group()}:(OI)(CI)F"],
                   capture_output=True, check=True)


class BackupError(Exception):
    """Sanitised error: message never contains filesystem paths."""


class BackupBusy(BackupError):
    pass


class BackupTooLarge(BackupError):
    pass


def _source_size(src: sqlite3.Connection) -> int:
    pages = src.execute("PRAGMA page_count").fetchone()[0]
    size = src.execute("PRAGMA page_size").fetchone()[0]
    return int(pages) * int(size)


def create_backup(db: Database, max_bytes: int = MAX_BACKUP_BYTES) -> tuple[Path, str]:
    """
    Create a consistent backup in a private temp directory.
    Returns (file_path, download_name). Caller must call cleanup_backup(file_path).
    Only one backup may run at a time (BackupBusy otherwise).
    """
    if not _backup_lock.acquire(blocking=False):
        raise BackupBusy("A backup is already in progress")
    tmpdir: Path | None = None
    try:
        tmpdir = Path(tempfile.mkdtemp(prefix="agentos-backup-"))  # mode 0700 on POSIX
        _backup_dirs.add(tmpdir)
        _restrict_windows_directory(tmpdir)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        name = f"agentos-backup-{stamp}.db"
        dest = tmpdir / name
        src = db.conn
        if _source_size(src) > max_bytes:
            raise BackupTooLarge("Database exceeds the maximum exportable size")
        with closing(sqlite3.connect(str(dest))) as dst:
            src.backup(dst)
            dst.execute("PRAGMA journal_mode=DELETE").fetchone()
            ok = dst.execute("PRAGMA integrity_check").fetchone()[0]
            if ok != "ok":
                raise BackupError("Backup failed integrity verification")
        if dest.stat().st_size > max_bytes:
            raise BackupTooLarge("Backup exceeds the maximum exportable size")
        return dest, name
    except BackupError:
        _rm(tmpdir)
        raise
    except Exception as exc:  # sqlite3.Error, OSError: never leak paths
        _rm(tmpdir)
        raise BackupError("Backup failed") from None
    finally:
        _backup_lock.release()


def _rm(tmpdir: Path | None) -> None:
    if tmpdir is not None:
        shutil.rmtree(tmpdir, ignore_errors=True)
        _backup_dirs.discard(tmpdir)


def cleanup_backup(path: Path) -> None:
    """Remove the temp directory created by create_backup."""
    if path.parent.name.startswith("agentos-backup-"):
        _rm(path.parent)


# ---------------------------------------------------------------- restore ---

class RestoreError(Exception):
    pass


def _norm_sql(sql):
    return " ".join(sql.split()).lower()


def _reference_columns():
    """Column names of every table in a freshly created current schema (incl. additive migrations)."""
    from backend.pyworker.db.migrate import init_db
    from backend.pyworker.db.schema import TABLES
    with tempfile.TemporaryDirectory(prefix="agentos-schema-") as folder:
        fresh = Database(str(Path(folder) / "fresh.db"))
        try:
            init_db(fresh)
        finally:
            fresh.close()
        with closing(sqlite3.connect(str(Path(folder) / "fresh.db"))) as ref:
            return {t: {r[1] for r in ref.execute(f'PRAGMA table_info("{t}")')} for t in TABLES}


def _check_schema_objects(con, current: bool) -> None:
    """Only AgentOS tables/indexes may exist; triggers, views and unknown tables never do.

    Older (but supported) schema versions legitimately lack newer columns and indexes
    and may carry older index definitions, so for them an index is accepted by name and
    a table only has to use known column names. A current-version backup must match the
    current index definitions exactly.
    """
    from backend.pyworker.db.schema import INDEXES, TABLES
    expected = {}
    for group in INDEXES.values():
        for sql in group:
            words = sql.split()
            expected[words[3 if words[1].upper() == "UNIQUE" else 2]] = sql
    reference = _reference_columns()
    bad = RestoreError("Backup contains an unexpected schema object")
    for kind, name, table, sql in con.execute("SELECT type, name, tbl_name, sql FROM sqlite_master").fetchall():
        if kind == "table" and name == "sqlite_sequence":
            continue
        if kind == "table" and name in TABLES:
            columns = {r[1] for r in con.execute(f'PRAGMA table_info("{name}")')}
            if not columns <= reference[name]:
                raise RestoreError(f"Backup contains an unexpected schema object ({kind} {name})")
            continue
        if kind == "index" and table in TABLES:
            if sql is None and name.startswith("sqlite_autoindex_"):
                continue
            if name in expected and sql and (not current or _norm_sql(sql) == _norm_sql(expected[name])):
                continue
        raise RestoreError(f"Backup contains an unexpected schema object ({kind} {name})")


def validate_backup_file(path: Path) -> int:
    """Validate integrity and schema of a backup. Returns its user_version."""
    from backend.pyworker.db.schema import SCHEMA_VERSION, TABLES
    from backend.pyworker.core.workspace_fs import canonical_workspace_dir, _norm, WorkspaceFsError
    from backend.pyworker.core.skills_fs import validate_source_dir

    if not path.is_file():
        raise RestoreError("Backup file not found")
    try:
        with closing(sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)) as con:
            con.execute("PRAGMA trusted_schema=OFF")
            if con.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RestoreError("Backup failed integrity_check")
            if con.execute("PRAGMA foreign_key_check").fetchall():
                raise RestoreError("Backup has foreign key violations")
            version = con.execute("PRAGMA user_version").fetchone()[0]
            if not 1 <= version <= SCHEMA_VERSION:
                raise RestoreError(f"Unsupported schema version {version} (supported 1..{SCHEMA_VERSION})")
            _check_schema_objects(con, version == SCHEMA_VERSION)
            tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if version == SCHEMA_VERSION:
                missing = set(TABLES) - tables
                if missing:
                    raise RestoreError(f"Backup is missing tables: {', '.join(sorted(missing))}")
            elif not {"missions", "workflows", "agent_profiles"}.issubset(tables):
                raise RestoreError("Backup is missing required legacy tables")
            for table, validator in (("workspaces", canonical_workspace_dir), ("skill_sources", validate_source_dir)):
                if table in tables:
                    for (stored,) in con.execute(f"SELECT path FROM {table}"):
                        try:
                            validator(stored)
                        except WorkspaceFsError as exc:
                            # A directory that no longer exists (backup moved to another
                            # machine) is inert: every use re-validates and answers 404.
                            if exc.status != 404:
                                raise RestoreError("Backup contains an invalid path") from None
                        except (TypeError, ValueError):
                            raise RestoreError("Backup contains an invalid path") from None
            return version
    except sqlite3.DatabaseError as exc:
        raise RestoreError(f"Not a valid SQLite database: {exc}") from None


def restore_database(backup: Path, target: Path) -> Path:
    """
    Offline restore. Steps: validate backup -> take exclusive lock on target
    (refuses when SQLite reports a conflicting lock) -> pre-restore backup
    next to target -> copy backup pages into target inside the lock.
    Returns the pre-restore backup path (or raises RestoreError).
    """
    validate_backup_file(backup)
    target = target.resolve()
    if not target.is_file():
        raise RestoreError("Target database does not exist; nothing to restore over")
    if target == backup.resolve() or target.samefile(backup):
        raise RestoreError("Backup and target are the same file")

    con = sqlite3.connect(str(target), timeout=0.2, isolation_level=None)
    try:
        try:
            # EXCLUSIVE locking mode in WAL needs the shm lock exclusively, which
            # fails while any other connection (e.g. a running app) is open.
            con.execute("PRAGMA locking_mode=EXCLUSIVE")
            con.execute("BEGIN EXCLUSIVE")
            con.execute("SELECT count(*) FROM sqlite_master").fetchone()
            con.execute("ROLLBACK")
            con.execute("PRAGMA journal_mode").fetchone()
        except sqlite3.OperationalError:
            raise RestoreError("Database is in use; stop AgentOS and any other process using it") from None

        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        pre = target.with_name(f"{target.name}.pre-restore-{stamp}.bak")
        with closing(sqlite3.connect(str(pre))) as pre_con:
            con.backup(pre_con)
        with closing(sqlite3.connect(f"{backup.resolve().as_uri()}?mode=ro", uri=True)) as src:
            src.execute("PRAGMA trusted_schema=OFF")
            # Re-check on the connection that is actually copied (the file could have been swapped since validation).
            from backend.pyworker.db.schema import SCHEMA_VERSION as _CURRENT
            _version = src.execute("PRAGMA user_version").fetchone()[0]
            if not 1 <= _version <= _CURRENT:
                raise RestoreError("Unsupported schema version")
            _check_schema_objects(src, _version == _CURRENT)
            try:
                src.backup(con)
            except sqlite3.Error as exc:
                raise RestoreError(
                    f"Restore failed ({exc}); pre-restore backup kept at {pre}"
                ) from None
        if con.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RestoreError(f"Post-restore integrity check failed; pre-restore backup is at {pre}")
        return pre
    finally:
        con.close()
