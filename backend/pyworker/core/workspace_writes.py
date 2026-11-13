"""Durable proposals and atomic publication of new workspace reports."""

import hashlib
import os
import tempfile

from backend.pyworker.core import workspace_fs as fs
from backend.pyworker.db.core import ts

MAX_REPORT_BYTES = 128 * 1024


class WriteConflict(Exception):
    pass


def digest(content):
    return hashlib.sha256(content).hexdigest()


def target_path(root, relative):
    parts = fs._clean_relative(relative)
    if len(parts) != 1:
        raise WriteConflict("Reports must use a filename in the workspace root.")
    if fs._norm(fs.canonical_workspace_dir(root)) != fs._norm(root):
        raise WriteConflict("Workspace moved.")
    return os.path.join(root, parts[0])


def propose(db, run_id, root, path, content):
    if not isinstance(content, str) or not isinstance(path, str):
        raise WriteConflict("Path and content must be text.")
    data = content.encode("utf-8")
    if not data or len(data) > MAX_REPORT_BYTES:
        raise WriteConflict("Report must contain 1 to 131072 UTF-8 bytes.")
    target = target_path(root, path)
    key = digest((run_id + "\0" + path + "\0").encode() + data)
    existing = db.one("SELECT * FROM workspace_writes WHERE id=?", (key,))
    if existing:
        return existing
    if os.path.lexists(target):
        raise WriteConflict("A file already exists. Choose a new report filename.")
    db.insert("workspace_writes", {
        "id": key, "run_id": run_id, "root": root, "path": path, "content": content,
        "content_sha256": digest(data), "status": "proposed", "created_at": ts(),
    })
    return db.one("SELECT * FROM workspace_writes WHERE id=?", (key,))


def apply(db, proposal):
    target = target_path(proposal["root"], proposal["path"])
    data = proposal["content"].encode("utf-8")
    if proposal["status"] == "applied":
        real, _ = fs.resolve_in_workspace(proposal["root"], proposal["path"])
        if digest(fs.read_limited(real, proposal["root"])) != proposal["content_sha256"]:
            raise WriteConflict("Published report has changed.")
        return {"path": proposal["path"], "sha256": proposal["content_sha256"], "verified": True}
    if proposal["status"] != "approved":
        raise WriteConflict("Proposal is not approved.")
    if os.path.lexists(target):
        raise WriteConflict("A file appeared at this path after the proposal.")
    fd, temporary = tempfile.mkstemp(prefix=".agentos-report-", dir=proposal["root"])
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        with db.tx():
            changed = db.update("workspace_writes", {"status": "publishing", "temporary_path": temporary},
                                "id=? AND status='approved'", (proposal["id"],))
            if not changed:
                raise WriteConflict("Proposal state changed.")
        target_path(proposal["root"], proposal["path"])
        os.link(temporary, target)
        real, _ = fs.resolve_in_workspace(proposal["root"], proposal["path"])
        if digest(fs.read_limited(real, proposal["root"])) != proposal["content_sha256"]:
            raise WriteConflict("Published file did not pass verification.")
        db.update("workspace_writes", {"status": "applied", "finished_at": ts()},
                  "id=? AND status='publishing'", (proposal["id"],))
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return {"path": proposal["path"], "sha256": proposal["content_sha256"], "verified": True}


def recover(db):
    for proposal in db.all("SELECT * FROM workspace_writes WHERE status='publishing'"):
        status = "interrupted"
        try:
            real, _ = fs.resolve_in_workspace(proposal["root"], proposal["path"])
            if digest(fs.read_limited(real, proposal["root"])) == proposal["content_sha256"]:
                status = "applied"
        except (fs.WorkspaceFsError, OSError):
            pass
        db.update("workspace_writes", {"status": status, "finished_at": ts()}, "id=?", (proposal["id"],))
        temporary = proposal["temporary_path"]
        if temporary and os.path.dirname(temporary) == proposal["root"] and os.path.basename(temporary).startswith(".agentos-report-"):
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
