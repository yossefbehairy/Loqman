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
import sqlite3
import tempfile
import threading
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.engine import Engine

# Refuse to export absurdly large databases over HTTP (bounded temp usage).
MAX_BACKUP_BYTES = 1024 * 1024 * 1024  # 1 GiB

_backup_lock = threading.Lock()


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


def create_backup(engine: Engine, max_bytes: int = MAX_BACKUP_BYTES) -> tuple[Path, str]:
    """
    Create a consistent backup in a private temp directory.
    Returns (file_path, download_name). Caller must call cleanup_backup(file_path).
    Only one backup may run at a time (BackupBusy otherwise).
    """
    if not _backup_lock.acquire(blocking=False):
        raise BackupBusy("A backup is already in progress")
    tmpdir: Path | None = None
    raw = None
    try:
        tmpdir = Path(tempfile.mkdtemp(prefix="agentos-backup-"))  # mode 0700 on POSIX
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        name = f"agentos-backup-{stamp}.db"
        dest = tmpdir / name
        raw = engine.raw_connection()
        src = raw.driver_connection
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
        if raw is not None:
            raw.close()
        _backup_lock.release()


def _rm(tmpdir: Path | None) -> None:
    if tmpdir is not None:
        shutil.rmtree(tmpdir, ignore_errors=True)


def cleanup_backup(path: Path) -> None:
    """Remove the temp directory created by create_backup."""
    if path.parent.name.startswith("agentos-backup-"):
        _rm(path.parent)


# ---------------------------------------------------------------- restore ---

class RestoreError(Exception):
    pass


def validate_backup_file(path: Path) -> int:
    """Validate integrity and schema of a backup. Returns its user_version."""
    from backend.app.db import models, skill_models  # noqa: F401  (register tables)
    from backend.app.db.session import Base, SCHEMA_VERSION

    if not path.is_file():
        raise RestoreError("Backup file not found")
    try:
        with closing(sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)) as con:
            if con.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RestoreError("Backup failed integrity_check")
            if con.execute("PRAGMA foreign_key_check").fetchall():
                raise RestoreError("Backup has foreign key violations")
            version = con.execute("PRAGMA user_version").fetchone()[0]
            if not 1 <= version <= SCHEMA_VERSION:
                raise RestoreError(f"Unsupported schema version {version} (supported 1..{SCHEMA_VERSION})")
            tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if version == SCHEMA_VERSION:
                missing = set(Base.metadata.tables) - tables
                if missing:
                    raise RestoreError(f"Backup is missing tables: {', '.join(sorted(missing))}")
            elif not {"missions", "workflows", "agent_profiles"}.issubset(tables):
                raise RestoreError("Backup is missing required legacy tables")
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
