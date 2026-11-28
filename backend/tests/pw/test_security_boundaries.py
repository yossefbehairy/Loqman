"""Regression checks for worker security boundaries; no engine is launched."""
import os
import socket
import sqlite3

import pytest

from backend.pyworker.core import backup, browser, skills_fs, workspace_fs, workspace_writes
from backend.pyworker.runtime import child_env
from backend.pyworker.runtime.codex_runtime import CodexCliRuntime
from backend.pyworker.runtime.agy_acp_runtime import AgyAcpRuntime


def test_child_environment_excludes_unknown_secrets(monkeypatch):
    monkeypatch.setenv("SOME_PROVIDER_SECRET", "secret")
    monkeypatch.setenv("CODEX_HOME", "config-directory")
    for env in (child_env.child_env(), CodexCliRuntime()._env(), AgyAcpRuntime()._env()):
        assert "SOME_PROVIDER_SECRET" not in env
        assert env["CODEX_HOME"] == "config-directory"


def test_backup_rejects_trigger(tmp_path):
    path = tmp_path / "bad.db"
    with sqlite3.connect(path) as db:
        db.executescript("CREATE TABLE missions(id TEXT); CREATE TRIGGER evil AFTER INSERT ON missions BEGIN SELECT 1; END;")
        db.execute("PRAGMA user_version=1")
    with pytest.raises(backup.RestoreError, match="unexpected schema"):
        backup.validate_backup_file(path)


def test_recovery_does_not_unlink_stored_path(tmp_path):
    victim = tmp_path / ".agentos-report-victim"
    victim.write_text("keep")
    class DB:
        def all(self, query):
            return [{"root": str(tmp_path), "path": "missing", "content_sha256": "", "id": "x", "temporary_path": str(victim)}]
        def update(self, *args):
            pass
    workspace_writes.recover(DB())
    assert victim.read_text() == "keep"


def test_root_rejected_on_use():
    root = os.path.abspath(os.sep)
    with pytest.raises(workspace_fs.WorkspaceFsError):
        workspace_fs.resolve_in_workspace(root, "anything")
    with pytest.raises(workspace_fs.WorkspaceFsError):
        skills_fs._source_root(root)


def test_hardlinks_rejected(tmp_path):
    source = tmp_path / "source"
    source.write_text("secret")
    linked = tmp_path / "linked"
    try:
        os.link(source, linked)
    except OSError:
        pytest.skip("filesystem does not support hard links")
    with pytest.raises(workspace_fs.WorkspaceFsError):
        workspace_fs.read_limited(str(linked), str(tmp_path))
    with pytest.raises(workspace_fs.WorkspaceFsError):
        skills_fs._read_file(str(linked), str(tmp_path), 100)


def test_browser_rejects_private_resolution(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("127.0.0.1", 80))])
    with pytest.raises(browser.BrowserError):
        browser._check_public_url("http://example.test/")


# -- S2: API-key opt-in survives the environment allowlist ------------------------------------------

@pytest.mark.parametrize("cls,key", [(CodexCliRuntime, "OPENAI_API_KEY"), (AgyAcpRuntime, "GEMINI_API_KEY")])
def test_api_key_passes_only_when_user_opted_in(monkeypatch, cls, key):
    monkeypatch.setenv(key, "value")
    monkeypatch.setenv("AGENTOS_LOCAL_TOKEN", "token")
    assert key not in cls()._env()
    opted_in = cls(allow_api_key_env=True)._env()
    assert opted_in[key] == "value"
    assert "AGENTOS_LOCAL_TOKEN" not in opted_in  # never, even when opted in


# -- S1: backups ----------------------------------------------------------------------------------

def _old_backup(path, version=7):
    """A believable pre-migration backup: older columns, older index set, legacy tables."""
    from backend.pyworker.db.schema import TABLES
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE missions (id VARCHAR(36) NOT NULL, title VARCHAR(255) NOT NULL, "
                   "objective TEXT NOT NULL, status VARCHAR(32) NOT NULL, model_id VARCHAR(128), variant VARCHAR(64), "
                   "idempotency_key VARCHAR(128), plan_json TEXT, final_summary TEXT, error_summary TEXT, "
                   "created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL, started_at DATETIME, "
                   "finished_at DATETIME, PRIMARY KEY (id))")
        db.execute(TABLES["workflows"])
        db.execute(TABLES["agent_profiles"].replace("\tengine VARCHAR(32), \n", ""))
        db.execute("CREATE INDEX ix_missions_status ON missions (status)")
        db.execute(f"PRAGMA user_version={version}")
    return path


def test_older_supported_schema_backup_is_accepted_and_restorable(tmp_path):
    from backend.pyworker.db.core import Database
    from backend.pyworker.db.migrate import init_db
    old = _old_backup(tmp_path / "old.db")
    assert backup.validate_backup_file(old) == 7
    target = tmp_path / "target.db"
    db = Database(str(target))
    init_db(db)
    db.close()
    backup.restore_database(old, target)  # must not be refused for lacking newer columns/indexes
    db = Database(str(target))
    init_db(db)  # and the normal upgrade path then brings it to the current schema
    assert db.scalar("SELECT count(*) FROM pragma_table_info('missions') WHERE name='source_conversation_id'") == 1
    db.close()


def test_current_backup_made_after_migration_is_accepted(tmp_path):
    from backend.pyworker.db.core import Database
    from backend.pyworker.db.migrate import init_db
    from backend.pyworker.db.schema import SCHEMA_VERSION
    _old_backup(tmp_path / "old.db")
    db = Database(str(tmp_path / "old.db"))
    init_db(db)
    path, _ = backup.create_backup(db)
    try:
        assert backup.validate_backup_file(path) == SCHEMA_VERSION
    finally:
        backup.cleanup_backup(path)
        db.close()


@pytest.mark.parametrize("extra", [
    "CREATE TRIGGER evil AFTER INSERT ON missions BEGIN SELECT 1; END",
    "CREATE VIEW v AS SELECT * FROM missions",
    "CREATE TABLE unknown_table (x)",
    "CREATE INDEX ix_unknown ON missions (title)",
])
def test_unexpected_objects_rejected_even_in_older_backups(tmp_path, extra):
    old = _old_backup(tmp_path / "old.db")
    with sqlite3.connect(old) as db:
        db.execute(extra)
    with pytest.raises(backup.RestoreError, match="unexpected schema"):
        backup.validate_backup_file(old)


def test_backup_directory_is_removed_at_process_exit(tmp_path):
    import subprocess
    import sys
    from pathlib import Path
    script = (
        "from backend.pyworker.db.core import Database\n"
        "from backend.pyworker.db.migrate import init_db\n"
        "from backend.pyworker.core import backup\n"
        f"db = Database(r'{tmp_path / 'live.db'}'); init_db(db)\n"
        "path, _ = backup.create_backup(db)\n"
        "print(path)\n"  # deliberately never calls cleanup_backup
    )
    root = Path(__file__).resolve().parents[3]
    out = subprocess.run([sys.executable, "-c", script], cwd=root, capture_output=True, text=True, check=True)
    leftover = Path(out.stdout.strip().splitlines()[-1])
    assert leftover.name.startswith("agentos-backup-")
    assert not leftover.exists() and not leftover.parent.exists()


def test_cleanup_hook_removes_tracked_directories(tmp_path):
    folder = tmp_path / "agentos-backup-x"
    folder.mkdir()
    backup._backup_dirs.add(folder)
    backup._cleanup_at_exit()
    assert not folder.exists() and folder not in backup._backup_dirs


# -- S6: report publication ----------------------------------------------------------------------

def _proposal(api, root, name="report.txt"):
    cid = "swap-conversation"
    api.db.execute("INSERT INTO conversations(id,title,created_at,updated_at) VALUES(?,?,?,?)", (cid, "t", "now", "now"))
    api.db.execute("INSERT INTO conversation_runs(id,conversation_id,engine,model_id,timeout,status,created_at) "
                   "VALUES(?,?,?,?,?,?,?)", ("swap-run", cid, "claude-code", "m", 90, "running", "now"))
    row = workspace_writes.propose(api.db, "swap-run", str(root), name, "payload")
    api.db.update("workspace_writes", {"status": "approved"}, "id=?", (row["id"],))
    row["status"] = "approved"
    return row


@pytest.mark.skipif(os.name != "nt", reason="uses an NTFS junction")
def test_publication_into_swapped_directory_is_undone(api, tmp_path, monkeypatch):
    import subprocess
    root, elsewhere = tmp_path / "reports", tmp_path / "elsewhere"
    root.mkdir()
    elsewhere.mkdir()
    row = _proposal(api, root)
    real_link = os.link

    def swap_then_link(src, dst, **kw):
        # The race: the checked directory becomes a junction right before the destination is created.
        os.rename(root, tmp_path / "reports-original")
        made = subprocess.run(["cmd", "/c", "mklink", "/J", str(root), str(elsewhere)], capture_output=True)
        if made.returncode:
            os.rename(tmp_path / "reports-original", root)
            pytest.skip("junctions unavailable")
        return real_link(src, dst, **kw)

    monkeypatch.setattr(workspace_writes.os, "link", swap_then_link)
    with pytest.raises(workspace_writes.WriteConflict):
        workspace_writes.apply(api.db, row)
    assert list(elsewhere.iterdir()) == []  # the destination it created was removed
    assert [p.name for p in tmp_path.iterdir() if p.name.startswith("agentos-report-")] == []


def test_publication_is_atomic_and_leaves_no_temp_file(api, tmp_path):
    root = tmp_path / "reports"
    root.mkdir()
    row = _proposal(api, root)
    assert workspace_writes.apply(api.db, row)["verified"] is True
    assert (root / "report.txt").read_text() == "payload"
    assert [p.name for p in root.iterdir()] == ["report.txt"]
    assert not [p.name for p in tmp_path.iterdir() if p.name.startswith("agentos-report-")]


# -- S7: browser ---------------------------------------------------------------------------------

@pytest.mark.parametrize("url", ["http://127.0.0.1/", "http://[::1]/", "http://169.254.169.254/latest",
                                 "http://10.0.0.5/", "http://192.168.1.1:8080/", "file:///C:/Windows/win.ini"])
def test_browser_refuses_non_public_targets(url):
    with pytest.raises(browser.BrowserError):
        browser._check_public_url(url)


def test_browser_accepts_public_literal():
    browser._check_public_url("https://93.184.216.34/")


# -- S9: computer click ---------------------------------------------------------------------------

@pytest.mark.skipif(os.name != "nt", reason="Windows-only module")
@pytest.mark.parametrize("flip_at,sent_messages", [(3, 0), (5, 1)])
def test_click_rechecks_foreground_before_every_message(flip_at, sent_messages):
    from ctypes import wintypes
    from unittest.mock import MagicMock
    from backend.pyworker.core import computer
    session = object.__new__(computer.ComputerSession)
    session.handle, session.human_control, session.pid = 100, False, 1
    session.check = lambda: None
    session.rect = lambda: wintypes.RECT(0, 0, 50, 50)
    user = session.user = MagicMock()
    calls = {"n": 0}

    def foreground():
        calls["n"] += 1
        return 100 if calls["n"] < flip_at else 999  # another window takes focus mid-click

    user.GetForegroundWindow.side_effect = foreground
    user.WindowFromPoint.return_value = 55
    user.GetAncestor.return_value = 100
    user.ScreenToClient.return_value = 1
    user.SendMessageTimeoutW.return_value = 1

    def cursor(ref):
        ref._obj.x, ref._obj.y = 5, 5
        return 1

    def client_rect(_hwnd, ref):
        ref._obj.right, ref._obj.bottom = 800, 600
        return 1

    user.GetClientRect.side_effect = client_rect
    user.GetCursorPos.side_effect = cursor
    with pytest.raises(computer.ComputerError, match="[Ff]oreground"):
        session.call("computer_click", {"x": 5, "y": 5})
    assert user.SendMessageTimeoutW.call_count == sent_messages
    assert not user.mouse_event.called  # never the global input stream
