"""Isolated temp-file tests for SQLite backup export and offline restore."""

import sqlite3
import threading
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from backend.app.api.routes import backup as backup_route
from backend.app.db import backup as bk
from backend.app.db.session import SCHEMA_VERSION, init_db


@pytest.fixture
def file_engine(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path / 'live.db'}", connect_args={"check_same_thread": False})
    init_db(eng)
    with eng.begin() as c:
        c.execute(text("CREATE TABLE marker (id INTEGER PRIMARY KEY, v TEXT)"))
        c.execute(text("INSERT INTO marker (v) VALUES ('committed-in-wal')"))
    yield eng
    eng.dispose()


@pytest.fixture
def api(file_engine):
    app = FastAPI()
    app.include_router(backup_route.router)
    app.dependency_overrides[backup_route.get_backup_engine] = lambda: file_engine
    return TestClient(app)


def _rows(path, sql="SELECT v FROM marker"):
    con = sqlite3.connect(str(path))
    try:
        return [r[0] for r in con.execute(sql)]
    finally:
        con.close()


def test_backup_includes_wal_data_and_cleans_up(api, file_engine, tmp_path):
    with file_engine.connect() as c:
        assert c.exec_driver_sql("PRAGMA journal_mode").scalar_one() == "wal"
    assert (tmp_path / "live.db-wal").exists()
    seen = {}
    orig = bk.cleanup_backup

    def spy(p):
        seen["dir"] = p.parent
        orig(p)
    backup_route.cleanup_backup = spy
    try:
        r = api.post("/api/backup")
    finally:
        backup_route.cleanup_backup = orig
    assert r.status_code == 200
    assert r.headers["cache-control"] == "no-store"
    assert str(tmp_path) not in r.headers.get("content-disposition", "")
    out = tmp_path / "dl.db"
    out.write_bytes(r.content)
    assert _rows(out) == ["committed-in-wal"]
    assert _rows(out, "PRAGMA user_version") == [SCHEMA_VERSION]
    assert not seen["dir"].exists()


def test_backup_consistent_under_concurrent_writes(file_engine):
    stop = threading.Event()

    def writer():
        while not stop.is_set():
            with file_engine.begin() as c:
                c.execute(text("INSERT INTO marker (v) VALUES ('x')"))
    t = threading.Thread(target=writer)
    t.start()
    try:
        path, _ = bk.create_backup(file_engine)
    finally:
        stop.set()
        t.join()
    try:
        assert _rows(path, "PRAGMA integrity_check") == ["ok"]
        assert "committed-in-wal" in _rows(path)
    finally:
        bk.cleanup_backup(path)
    assert not path.parent.exists()


def test_backup_too_large_413_and_no_leftover(api, monkeypatch, tmp_path):
    monkeypatch.setattr(bk, "MAX_BACKUP_BYTES", 1)
    monkeypatch.setattr(backup_route, "create_backup", lambda e: bk.create_backup(e, max_bytes=1))
    r = api.post("/api/backup")
    assert r.status_code == 413
    assert str(tmp_path) not in r.text


def test_backup_busy_409(api):
    assert bk._backup_lock.acquire(blocking=False)
    try:
        assert api.post("/api/backup").status_code == 409
    finally:
        bk._backup_lock.release()
    assert api.post("/api/backup").status_code == 200  # lock released after failure


def test_backup_error_is_sanitised_and_releases_lock(file_engine, monkeypatch):
    class Boom:
        def raw_connection(self):
            raise sqlite3.OperationalError("unable to open C:\\secret\\path.db")
    with pytest.raises(bk.BackupError) as e:
        bk.create_backup(Boom())
    assert "secret" not in str(e.value)
    assert bk._backup_lock.acquire(blocking=False)
    bk._backup_lock.release()


def test_restore_roundtrip_and_pre_restore_backup(file_engine, tmp_path):
    path, _ = bk.create_backup(file_engine)
    with file_engine.begin() as c:
        c.execute(text("INSERT INTO marker (v) VALUES ('after')"))
    file_engine.dispose()
    keep = tmp_path / "keep.db"
    keep.write_bytes(path.read_bytes())
    bk.cleanup_backup(path)
    pre = bk.restore_database(keep, tmp_path / "live.db")
    assert _rows(tmp_path / "live.db") == ["committed-in-wal"]
    assert _rows(pre) == ["committed-in-wal", "after"]


def test_restore_refuses_while_in_use(file_engine, tmp_path):
    path, _ = bk.create_backup(file_engine)
    keep = tmp_path / "keep.db"
    keep.write_bytes(path.read_bytes())
    bk.cleanup_backup(path)
    with file_engine.begin() as c:
        c.execute(text("INSERT INTO marker (v) VALUES ('after')"))
    holder = sqlite3.connect(str(tmp_path / "live.db"))
    holder.execute("SELECT count(*) FROM marker").fetchone()  # open WAL reader
    try:
        with pytest.raises(bk.RestoreError, match="in use"):
            bk.restore_database(keep, tmp_path / "live.db")
    finally:
        holder.close()
    assert "after" in _rows(tmp_path / "live.db")


def test_restore_rejects_bad_backups(tmp_path):
    target = tmp_path / "t.db"
    sqlite3.connect(str(target)).close()
    with pytest.raises(bk.RestoreError):
        bk.validate_backup_file(tmp_path / "missing.db")
    junk = tmp_path / "junk.db"
    junk.write_bytes(b"not sqlite" * 100)
    with pytest.raises(bk.RestoreError):
        bk.restore_database(junk, target)
    empty = tmp_path / "empty.db"
    sqlite3.connect(str(empty)).close()
    with pytest.raises(bk.RestoreError, match="schema version"):
        bk.restore_database(empty, target)
    future = tmp_path / "future.db"
    con = sqlite3.connect(str(future))
    con.execute("CREATE TABLE a(x)")
    con.execute(f"PRAGMA user_version={SCHEMA_VERSION + 1}")
    con.commit()
    con.close()
    with pytest.raises(bk.RestoreError, match="schema version"):
        bk.restore_database(future, target)
    partial = tmp_path / "partial.db"
    con = sqlite3.connect(str(partial))
    con.execute("CREATE TABLE a(x)")
    con.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
    con.commit()
    con.close()
    with pytest.raises(bk.RestoreError, match="missing tables"):
        bk.restore_database(partial, target)
    assert not list(tmp_path.glob("*.pre-restore-*"))
