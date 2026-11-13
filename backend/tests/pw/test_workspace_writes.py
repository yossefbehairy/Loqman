import pytest

from backend.pyworker.core import workspace_writes as writes


def proposal(api, root):
    cid = "write-test-conversation"
    api.db.execute("INSERT INTO conversations(id,title,created_at,updated_at) VALUES(?,?,?,?)",
                   (cid, "Write test", "now", "now"))
    api.db.execute("INSERT INTO conversation_runs(id,conversation_id,engine,model_id,timeout,status,created_at) "
                   "VALUES(?,?,?,?,?,?,?)", ("write-test", cid, "claude-code", "m", 90, "running", "now"))
    return writes.propose(api.db, "write-test", str(root), "report.txt", "correct")


def test_recovery_after_publication_before_commit(api, tmp_path, monkeypatch):
    root = tmp_path / "reports"
    root.mkdir()
    row = proposal(api, root)
    row["status"] = "approved"
    api.db.update("workspace_writes", {"status": "approved"}, "id=?", (row["id"],))
    original = api.db.update

    def crash(table, values, where, params=()):
        if table == "workspace_writes" and values.get("status") == "applied":
            raise RuntimeError("Simulated interruption after publication")
        return original(table, values, where, params)

    monkeypatch.setattr(api.db, "update", crash)
    with pytest.raises(RuntimeError):
        writes.apply(api.db, row)
    assert (root / "report.txt").read_text() == "correct"
    monkeypatch.setattr(api.db, "update", original)
    writes.recover(api.db)
    recovered = api.db.one("SELECT * FROM workspace_writes WHERE id=?", (row["id"],))
    assert recovered["status"] == "applied"
    before = (root / "report.txt").stat().st_mtime_ns
    writes.apply(api.db, recovered)
    assert before == (root / "report.txt").stat().st_mtime_ns


def test_publication_preserves_file_created_after_approval(api, tmp_path):
    root = tmp_path / "reports"
    root.mkdir()
    row = proposal(api, root)
    row["status"] = "approved"
    api.db.update("workspace_writes", {"status": "approved"}, "id=?", (row["id"],))
    (root / "report.txt").write_text("user's newer file")
    with pytest.raises(writes.WriteConflict):
        writes.apply(api.db, row)
    assert (root / "report.txt").read_text() == "user's newer file"
