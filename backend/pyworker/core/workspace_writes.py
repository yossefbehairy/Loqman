"""Durable proposals and atomic publication of new workspace reports."""

import hashlib
import os
import secrets

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


def _dir_identity(root):
    info = os.stat(root)
    return info.st_dev, info.st_ino


def _require_same_dir(root, identity):
    try:
        if getattr(os.lstat(root), "st_file_attributes", 0) & 0x400 or os.path.islink(root):
            raise WriteConflict("Workspace directory changed during publication.")
        if _dir_identity(root) != identity or fs._norm(fs.canonical_workspace_dir(root)) != fs._norm(root):
            raise WriteConflict("Workspace directory changed during publication.")
    except (OSError, fs.WorkspaceFsError):
        raise WriteConflict("Workspace directory changed during publication.") from None


def _remove_created(target, temp_stat):
    """Remove the destination only if it is the very file this call linked."""
    try:
        if os.path.samestat(temp_stat, os.lstat(target)):
            os.unlink(target)
    except OSError:
        pass


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
    root = proposal["root"]
    name = fs._clean_relative(proposal["path"])[0]
    # POSIX: every step goes through a pinned directory fd, so a swap cannot redirect it.
    # Windows (no dir_fd): paths are used, but the directory identity and reparse-point
    # status are re-verified immediately before and after the no-overwrite link.
    use_fd = os.link in os.supports_dir_fd and os.open in os.supports_dir_fd and os.unlink in os.supports_dir_fd
    directory = None
    if use_fd:
        directory = os.open(root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0))
    try:
        identity = _dir_identity(root)
        if directory is not None and (os.fstat(directory).st_dev, os.fstat(directory).st_ino) != identity:
            raise WriteConflict("Workspace directory changed during publication.")
        _require_same_dir(root, identity)
        temporary = ".agentos-report-" + secrets.token_hex(16)
        temp_path = os.path.join(root, temporary)
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0)
        if directory is not None:
            fd = os.open(temporary, flags, 0o600, dir_fd=directory)
        else:
            fd = os.open(temp_path, flags, 0o600)
    except BaseException:
        if directory is not None:
            os.close(directory)
        raise

    def unlink(rel, full):
        if directory is not None:
            os.unlink(rel, dir_fd=directory)
        else:
            os.unlink(full)

    def same_as_temp(rel, full):
        if directory is not None:
            return os.path.samestat(temp_stat, os.stat(rel, dir_fd=directory, follow_symlinks=False))
        return os.path.samestat(temp_stat, os.lstat(full))

    temp_stat = os.fstat(fd)
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
        target_path(root, proposal["path"])
        # Re-check the directory immediately before and after creating the destination.
        _require_same_dir(root, identity)
        try:
            if directory is not None:
                os.link(temporary, name, src_dir_fd=directory, dst_dir_fd=directory, follow_symlinks=False)
            else:
                os.link(temp_path, target, follow_symlinks=False)
        except OSError:
            raise WriteConflict("Could not publish the report safely.") from None
        try:
            _require_same_dir(root, identity)
            target_path(root, proposal["path"])
            if not same_as_temp(name, target):
                raise WriteConflict("Published file is not the one that was created.")
            unlink(temporary, temp_path)
            real, _ = fs.resolve_in_workspace(root, proposal["path"])
            if digest(fs.read_limited(real, root)) != proposal["content_sha256"]:
                raise WriteConflict("Published file did not pass verification.")
        except BaseException:
            try:
                if same_as_temp(name, target):
                    unlink(name, target)
            except OSError:
                pass
            raise
        # Verified. From here on a failure to record it must NOT remove the report:
        # recover() finds the published, hash-matching file and marks it applied.
        db.update("workspace_writes", {"status": "applied", "finished_at": ts()},
                  "id=? AND status='publishing'", (proposal["id"],))
    finally:
        try:
            unlink(temporary, temp_path)
        except OSError:
            pass
        if directory is not None:
            os.close(directory)
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
        # Persisted paths are untrusted; recovery never deletes them.
