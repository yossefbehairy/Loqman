import os
import asyncio
import json
import sqlite3

import pytest

from backend.pyworker.core import backup, browser, workspace_writes
from backend.pyworker.runtime.child_env import child_env


def test_child_env_limits_prefixes_and_keeps_network_settings(monkeypatch):
    for name in ("LC_SECRET", "PROGRAMFILES_SECRET", "HTTPS_PROXY", "SSL_CERT_FILE", "GOOGLE_APPLICATION_CREDENTIALS"):
        monkeypatch.setenv(name, "value")
    env = child_env()
    assert "LC_SECRET" not in env and "PROGRAMFILES_SECRET" not in env
    assert all(env[name] == "value" for name in ("HTTPS_PROXY", "SSL_CERT_FILE", "GOOGLE_APPLICATION_CREDENTIALS"))


def test_legacy_schema_is_checked_by_column_names_not_name_alone(tmp_path):
    path = tmp_path / "old.db"
    with sqlite3.connect(path) as con:
        con.execute("CREATE TABLE missions(id TEXT, not_a_real_column TEXT)")
        con.execute("PRAGMA user_version=1")
    with pytest.raises(backup.RestoreError, match="unexpected schema"):
        backup.validate_backup_file(path)


@pytest.mark.skipif(os.name != "nt", reason="uses an NTFS junction")
def test_windows_publication_rejects_a_junction_workspace(tmp_path):
    import subprocess
    real, link = tmp_path / "real", tmp_path / "link"
    real.mkdir()
    if subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(real)], capture_output=True).returncode:
        pytest.skip("junctions unavailable")
    with pytest.raises(workspace_writes.WriteConflict):
        workspace_writes._require_same_dir(str(link), workspace_writes._dir_identity(str(link)))


def test_cdp_blocks_private_redirect(monkeypatch):
    async def run():
        cdp = browser.Cdp()
        cdp.next_id = 0
        sent = []
        messages = iter([
            {"method": "Fetch.requestPaused", "params": {"requestId": "r", "request": {"url": "http://127.0.0.1/"}}},
            {"id": 1, "result": {}},
        ])
        async def send(data, opcode=1):
            sent.append(json.loads(data))
        async def receive():
            return next(messages)
        cdp.send, cdp.receive = send, receive
        await cdp.call("Page.navigate", {"url": "https://example.com"})
        assert sent[1]["method"] == "Fetch.failRequest"
    asyncio.run(run())
