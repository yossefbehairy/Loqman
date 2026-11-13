"""Conversations, persisted runs, workspaces/artifacts, skill sources,
approvals and deliverables."""

from __future__ import annotations
import hashlib
import json

import base64
import mimetypes
import os
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict

from backend.pyworker.core import run_inputs, run_skills
from backend.pyworker.core import skills_fs as sfs
from backend.pyworker.core import workspace_fs as fs
from backend.pyworker.core.approvals import (
    APPROVAL_CAPABILITIES, ApprovalConflict, ApprovalNotFound,
)
from backend.pyworker.core.conversation_runs import (
    ActiveRunExists, ConversationNotFound, RequestKeyConflict, RunNotFound, TooManyRuns, UnsupportedEngine,
)
from backend.pyworker.core.deliverables import (
    MAX_DELIVERABLE_BYTES, DraftConflict, DraftNotFound, DraftTooLarge, RunNotEligible, TooManyDrafts,
)
from backend.pyworker.core.workspace_fs import WorkspaceFsError
from backend.pyworker.db.core import iso, iso_utc, new_id, parse_ts, ts, utcnow
from backend.pyworker.errors import api_error
from backend.pyworker.validate import (
    bad, check_keys, opt_enum, opt_int, opt_list, opt_str, req_enum, req_str,
)

from .registry import method

SHA256_RE = __import__("re").compile(r"^[0-9a-fA-F]{64}$")


def _fs_error(exc: WorkspaceFsError):
    return api_error(exc.status, exc.detail)


# -- conversations ------------------------------------------------------------

def _conv_out(r: Dict[str, Any]) -> Dict[str, Any]:
    return {"id": r["id"], "title": r["title"], "workspace_id": r["workspace_id"],
            "profile_id": r.get("profile_id"),
            "created_at": iso(r["created_at"]), "updated_at": iso(r["updated_at"])}


def _msg_out(r: Dict[str, Any]) -> Dict[str, Any]:
    return {"seq": r["seq"], "conversation_id": r["conversation_id"], "role": r["role"],
            "content": r["content"], "client_message_id": r["client_message_id"],
            "created_at": iso(r["created_at"])}


def _conversation(ctx, conversation_id: str) -> Dict[str, Any]:
    row = ctx.db.one("SELECT * FROM conversations WHERE id=?", (conversation_id,))
    if not row:
        raise api_error(404, "Conversation not found")
    return row


@method("conversations.create")
async def conversations_create(ctx, params):
    check_keys(params, {"title"}, ("workspace_id", "profile_id"))
    title = req_str(params, "title", 1, 255)
    workspace_id = opt_str(params, "workspace_id", 0, 36)
    profile_id = opt_str(params, "profile_id", 1, 64)
    if profile_id:
        from backend.pyworker.core.profiles import get_by_id
        profile = get_by_id(ctx.db, profile_id)
        if profile is None:
            raise api_error(404, "Profile not found")
        profile_id = profile["id"]
    if workspace_id and not ctx.db.one("SELECT id FROM workspaces WHERE id=?", (workspace_id,)):
        raise api_error(404, "Workspace not found")
    now, cid = ts(), new_id()
    ctx.db.insert("conversations", {"id": cid, "workspace_id": workspace_id or None, "title": title,
                                    "profile_id": profile_id,
                                    "created_at": now, "updated_at": now})
    return _conv_out(_conversation(ctx, cid))


@method("conversations.list")
async def conversations_list(ctx, params):
    check_keys(params)
    rows = ctx.db.all("SELECT * FROM conversations ORDER BY updated_at DESC, id LIMIT 500")
    return {"conversations": [_conv_out(r) for r in rows]}


@method("conversations.import_ceo")
async def conversations_import_ceo(ctx, params):
    """Copy legacy CEO messages once while retaining the original history."""
    check_keys(params)
    db, cid = ctx.db, "legacy-ceo-history"
    with db.tx():
        if not db.one("SELECT id FROM conversations WHERE id=?", (cid,)):
            now = ts()
            db.insert("conversations", {"id": cid, "title": "CEO history", "created_at": now, "updated_at": now})
        existing = {r["client_message_id"] for r in db.all(
            "SELECT client_message_id FROM conversation_messages WHERE conversation_id=?", (cid,))}
        imported = 0
        for old in db.all("SELECT * FROM ceo_messages ORDER BY seq, id"):
            key = f"ceo:{old['id']}"
            if key in existing or old["role"] not in ("user", "assistant"):
                continue
            db.insert("conversation_messages", {
                "conversation_id": cid, "client_message_id": key, "role": old["role"],
                "content": old["content"], "created_at": old["created_at"]})
            imported += 1
        if imported:
            db.update("conversations", {"updated_at": ts()}, "id=?", (cid,))
    return {"conversation_id": cid, "imported": imported}


@method("conversations.messages")
async def conversations_messages(ctx, params):
    check_keys(params, {"conversation_id"}, ("after", "limit"))
    cid = req_str(params, "conversation_id", 1, 64)
    after = opt_int(params, "after", 0, 0, 2**62)
    limit = opt_int(params, "limit", 200, 1, 500)
    _conversation(ctx, cid)
    rows = ctx.db.all("SELECT * FROM conversation_messages WHERE conversation_id=? AND seq>? "
                      "ORDER BY seq LIMIT ?", (cid, after, limit))
    return {"messages": [_msg_out(r) for r in rows]}


@method("conversations.add_message")
async def conversations_add_message(ctx, params):
    check_keys(params, {"conversation_id", "role", "content"}, ("client_message_id",))
    cid = req_str(params, "conversation_id", 1, 64)
    role = req_enum(params, "role", ("user", "assistant"))
    content = req_str(params, "content", 1, 60000)
    client_id = opt_str(params, "client_message_id", 1, 128)
    _conversation(ctx, cid)
    db = ctx.db

    def existing():
        if not client_id:
            return None
        return db.one("SELECT * FROM conversation_messages WHERE conversation_id=? AND client_message_id=?",
                      (cid, client_id))

    row = existing()
    if row:
        if row["role"] != role or row["content"] != content:
            raise api_error(409, "Message id already used for different content")
        return _msg_out(row)
    try:
        with db.tx():
            now = ts()
            cur = db.execute(
                "INSERT INTO conversation_messages(conversation_id,client_message_id,role,content,created_at) "
                "VALUES(?,?,?,?,?)", (cid, client_id, role, content, now))
            db.update("conversations", {"updated_at": now}, "id=?", (cid,))
    except sqlite3.IntegrityError:
        row = existing()
        if row and row["role"] == role and row["content"] == content:
            return _msg_out(row)
        raise api_error(409, "Message id already used for different content")
    return _msg_out(db.one("SELECT * FROM conversation_messages WHERE seq=?", (cur.lastrowid,)))


# -- persisted runs --------------------------------------------------------------

def _skill_ref(item):
    if not isinstance(item, dict) or set(item) - {"source_id", "skill_id"}:
        raise bad("each skill must be an object with exactly source_id and skill_id")
    req_str(item, "source_id", 1, 64)
    req_str(item, "skill_id", 1, 100)


@method("runs.start_turn")
async def runs_start_turn(ctx, params):
    check_keys(params, {"conversation_id", "engine", "model_id", "prompt"},
               ("skills", "input_files", "timeout", "variant", "request_key", "tool_mode", "computer_window"))
    cid = req_str(params, "conversation_id", 1, 64)
    engine = req_enum(params, "engine", ("claude-code", "codex", "opencode", "antigravity", "mock"))
    model_id = req_str(params, "model_id", 1, 128)
    prompt = req_str(params, "prompt", 1, 24000)
    timeout = opt_int(params, "timeout", 90, 5, 600)
    variant = opt_str(params, "variant", 0, 64)
    request_key = opt_str(params, "request_key", 1, 128)
    skills = opt_list(params, "skills", 3, _skill_ref) or []
    input_files = opt_list(params, "input_files", 3, lambda f: req_str({"f": f}, "f", 1)) or []
    tool_mode = opt_enum(params, "tool_mode", ("none", "read", "write", "browser", "full", "computer"), "none")
    computer_window = opt_int(params, "computer_window", None, 1, 2**63-1)
    try:
        return ctx.runs.start_turn(cid, engine, model_id, prompt, timeout, variant, request_key,
                                   [(s["source_id"], s["skill_id"]) for s in skills], input_files, tool_mode, computer_window)
    except (run_skills.SkillSelectionError, run_inputs.InputFileError) as exc:
        raise api_error(exc.status, exc.detail)
    except ConversationNotFound:
        raise api_error(404, "Conversation not found")
    except ActiveRunExists:
        raise api_error(409, "Conversation already has an active run")
    except RequestKeyConflict:
        raise api_error(409, "Request key already used for different input")
    except TooManyRuns:
        raise api_error(429, "Too many pending runs")
    except UnsupportedEngine:
        raise api_error(422, "Unsupported engine")


@method("runs.get")
async def runs_get(ctx, params):
    check_keys(params, {"run_id"})
    try:
        return ctx.runs.get_run(req_str(params, "run_id", 1, 64))
    except RunNotFound:
        raise api_error(404, "Run not found")


@method("runs.write_proposals")
async def runs_write_proposals(ctx, params):
    check_keys(params, {"run_id"})
    run_id = req_str(params, "run_id", 1, 64)
    try:
        ctx.runs.get_run(run_id)
    except RunNotFound:
        raise api_error(404, "Run not found")
    return {"proposals": ctx.db.all(
        "SELECT id,run_id,path,content,content_sha256,status,approval_id,created_at,finished_at "
        "FROM workspace_writes WHERE run_id=? ORDER BY created_at,id", (run_id,))}


@method("runs.tool_control")
async def runs_tool_control(ctx, params):
    check_keys(params, {"run_id", "owner"})
    run_id = req_str(params, "run_id", 1, 64)
    owner = req_enum(params, "owner", ("user", "agent"))
    session = ctx.runs._tool_sessions.get(run_id)
    if session is None:
        raise api_error(409, "Run has no active browser session.")
    session.browser.human_control = owner == "user"
    if session.computer is not None:
        session.computer.human_control = owner == "user"
    if session.browser.cdp is not None:
        await session.browser.evaluate("delete window.__agentosElements")
    session.event("control.changed", owner=owner)
    return {"run_id": run_id, "owner": owner}


@method("runs.cancel")
async def runs_cancel(ctx, params):
    check_keys(params, {"run_id"})
    try:
        run = await ctx.runs.cancel(req_str(params, "run_id", 1, 64))
    except RunNotFound:
        raise api_error(404, "Run not found")
    if run["status"] not in ("cancelled",):
        raise api_error(409, f"Run already {run['status']}")
    return run


@method("runs.steer")
async def runs_steer(ctx, params):
    """Stop the current attempt and start one idempotent corrected turn."""
    check_keys(params, {"run_id", "correction"})
    run_id = req_str(params, "run_id", 1, 64)
    correction = req_str(params, "correction", 1, 4000)
    try:
        ctx.runs.get_run(run_id)
    except RunNotFound:
        raise api_error(404, "Run not found")
    original = ctx.db.one("SELECT * FROM conversation_runs WHERE id=?", (run_id,))
    if original["kind"] != "conversation":
        raise api_error(422, "Corrections require a conversation run.")
    message = ctx.db.scalar("SELECT content FROM conversation_messages WHERE seq=?",
                            (original["user_message_seq"],))
    prompt = f"Original task:\n{message}\n\nUser correction (apply this to the task):\n{correction}"
    if len(prompt) > 24000:
        raise api_error(422, "Corrected task exceeds the prompt limit.")
    config = json.loads(original.get("tool_config_json") or "{}")
    key = "steer-" + hashlib.sha256((run_id + "\0" + correction).encode()).hexdigest()
    await ctx.runs.cancel(run_id)
    next_params = {
        "conversation_id": original["conversation_id"], "engine": original["engine"],
        "model_id": original["model_id"], "prompt": prompt, "timeout": original["timeout"],
        "variant": original["variant"], "request_key": key,
        "skills": [{"source_id": source, "skill_id": skill}
                   for source, skill in run_skills.stored_refs(original["skills_json"])],
        "input_files": run_inputs.stored_refs(original["input_files_json"]),
        "tool_mode": config.get("mode", "none"),
    }
    if config.get("computer_window"):
        next_params["computer_window"] = config["computer_window"]["handle"]
    return await runs_start_turn(ctx, next_params)


@method("runs.events")
async def runs_events(ctx, params):
    """Persisted lifecycle events after ``after_id`` (the pull form of the SSE stream)."""
    check_keys(params, {"run_id"}, ("after_id",))
    try:
        events = ctx.runs.list_events(req_str(params, "run_id", 1, 64), opt_int(params, "after_id", 0, 0, 2**62))
    except RunNotFound:
        raise api_error(404, "Run not found")
    return {"events": events}


# -- workspaces & artifacts -------------------------------------------------------

def _ws_out(r: Dict[str, Any]) -> Dict[str, Any]:
    return {"id": r["id"], "name": r["name"], "path": r["path"], "created_at": iso(r["created_at"])}


def _art_out(r: Dict[str, Any]) -> Dict[str, Any]:
    return {"id": r["id"], "workspace_id": r["workspace_id"], "run_id": r["run_id"],
            "relative_path": r["relative_path"], "mime": r["mime"], "size": r["size"],
            "created_at": iso(r["created_at"])}


def _workspace(ctx, workspace_id: str) -> Dict[str, Any]:
    ws = ctx.db.one("SELECT * FROM workspaces WHERE id=?", (workspace_id,))
    if ws is None:
        raise api_error(404, "workspace not found")
    return ws


@method("workspaces.create")
async def workspaces_create(ctx, params):
    check_keys(params, {"path"}, ("name",))
    try:
        real = fs.canonical_workspace_dir(req_str(params, "path", 1, 4096))
    except WorkspaceFsError as exc:
        raise _fs_error(exc)
    name = opt_str(params, "name", 1, 255) or os.path.basename(real.rstrip("\\/")) or real
    wid = new_id()
    try:
        ctx.db.insert("workspaces", {"id": wid, "name": name, "path": real, "created_at": ts()})
    except sqlite3.IntegrityError:
        raise api_error(409, "workspace already registered")
    return _ws_out(_workspace(ctx, wid))


@method("workspaces.list")
async def workspaces_list(ctx, params):
    check_keys(params)
    return {"workspaces": [_ws_out(r) for r in
                           ctx.db.all("SELECT * FROM workspaces ORDER BY created_at, id LIMIT 500")]}


@method("workspaces.get")
async def workspaces_get(ctx, params):
    check_keys(params, {"workspace_id"})
    return _ws_out(_workspace(ctx, req_str(params, "workspace_id", 1, 64)))


@method("artifacts.register")
async def artifacts_register(ctx, params):
    check_keys(params, {"workspace_id", "path"}, ("run_id",))
    ws = _workspace(ctx, req_str(params, "workspace_id", 1, 64))
    path = req_str(params, "path", 1, 4096)
    run_id = opt_str(params, "run_id", 0, 36)
    if run_id:
        run = ctx.db.one("SELECT * FROM conversation_runs WHERE id=?", (run_id,))
        if run is None:
            raise api_error(404, "run not found")
        conv = (ctx.db.one("SELECT * FROM conversations WHERE id=?", (run["conversation_id"],))
                if run["conversation_id"] else None)
        if conv is None or conv["workspace_id"] != ws["id"]:
            raise api_error(409, "run does not belong to this workspace")
    try:
        real, rel = fs.resolve_in_workspace(ws["path"], path)
        size = fs.file_size(real)
    except WorkspaceFsError as exc:
        raise _fs_error(exc)
    except OSError:
        raise api_error(403, "file not accessible")
    mime = mimetypes.guess_type(rel)[0] or "application/octet-stream"
    aid = new_id()
    try:
        ctx.db.insert("artifacts", {"id": aid, "workspace_id": ws["id"], "run_id": run_id or None,
                                    "relative_path": rel, "mime": mime, "size": size, "created_at": ts()})
    except sqlite3.IntegrityError:
        raise api_error(409, "artifact already registered")
    return _art_out(ctx.db.one("SELECT * FROM artifacts WHERE id=?", (aid,)))


@method("artifacts.list")
async def artifacts_list(ctx, params):
    check_keys(params, {"workspace_id"})
    wid = req_str(params, "workspace_id", 1, 64)
    _workspace(ctx, wid)
    rows = ctx.db.all("SELECT * FROM artifacts WHERE workspace_id=? ORDER BY created_at, id LIMIT 500", (wid,))
    return {"artifacts": [_art_out(r) for r in rows]}


def _artifact(ctx, artifact_id: str) -> Dict[str, Any]:
    row = ctx.db.one("SELECT * FROM artifacts WHERE id=?", (artifact_id,))
    if row is None:
        raise api_error(404, "artifact not found")
    return row


@method("artifacts.get")
async def artifacts_get(ctx, params):
    check_keys(params, {"artifact_id"})
    return _art_out(_artifact(ctx, req_str(params, "artifact_id", 1, 64)))


@method("artifacts.content")
async def artifacts_content(ctx, params):
    """Re-validates containment on every call. Bytes are returned base64-encoded."""
    check_keys(params, {"artifact_id"})
    row = _artifact(ctx, req_str(params, "artifact_id", 1, 64))
    ws = _workspace(ctx, row["workspace_id"])
    try:
        real, _ = fs.resolve_in_workspace(ws["path"], row["relative_path"])
        data = fs.read_limited(real, ws["path"])
    except WorkspaceFsError as exc:
        raise _fs_error(exc)
    except OSError:
        raise api_error(403, "file not accessible")
    return {"content_base64": base64.b64encode(data).decode("ascii"), "size": len(data),
            "media_type": row["mime"], "disposition": "attachment",
            "headers": {"X-Content-Type-Options": "nosniff", "Content-Security-Policy": "sandbox"}}


# -- skill sources ------------------------------------------------------------------

def _src_out(r: Dict[str, Any]) -> Dict[str, Any]:
    return {"id": r["id"], "name": r["name"], "path": r["path"], "created_at": iso(r["created_at"])}


def _source(ctx, source_id: str) -> Dict[str, Any]:
    src = ctx.db.one("SELECT * FROM skill_sources WHERE id=?", (source_id,))
    if not src:
        raise api_error(404, "skill source not found")
    return src


def _skill_out(s) -> Dict[str, Any]:
    return {"id": s.id, "name": s.name, "description": s.description, "relative_path": s.relative_path,
            "size_bytes": s.size_bytes, "metadata_valid": s.metadata_valid, "warnings": s.warnings}


@method("skills.sources.register")
async def skills_register(ctx, params):
    check_keys(params, {"path"}, ("name",))
    try:
        real = sfs.validate_source_dir(req_str(params, "path", 1, 4096))
    except WorkspaceFsError as exc:
        raise _fs_error(exc)
    name = (opt_str(params, "name", 1, 255) or os.path.basename(real) or "skills").strip()[:255]
    sid = new_id()
    try:
        ctx.db.insert("skill_sources", {"id": sid, "name": name, "path": real, "created_at": ts()})
    except sqlite3.IntegrityError:
        raise api_error(409, "skill source already registered")
    return _src_out(_source(ctx, sid))


@method("skills.sources.list")
async def skills_sources_list(ctx, params):
    check_keys(params)
    return {"sources": [_src_out(r) for r in
                        ctx.db.all("SELECT * FROM skill_sources ORDER BY created_at, id")]}


@method("skills.sources.delete")
async def skills_sources_delete(ctx, params):
    """Removes only the registration; files on disk are untouched."""
    check_keys(params, {"source_id"})
    src = _source(ctx, req_str(params, "source_id", 1, 64))
    ctx.db.execute("DELETE FROM skill_sources WHERE id=?", (src["id"],))
    return {"deleted": True}


@method("skills.list")
async def skills_list(ctx, params):
    check_keys(params, {"source_id"})
    src = _source(ctx, req_str(params, "source_id", 1, 64))
    try:
        scan = sfs.scan_source(src["path"])
    except WorkspaceFsError as exc:
        raise _fs_error(exc)
    return {"source": _src_out(src), "skills": [_skill_out(s) for s in scan.skills],
            "skipped": [{"entry": s.entry, "reason": s.reason} for s in scan.skipped],
            "truncated": scan.truncated, "notice": sfs.GUIDANCE_NOTICE}


@method("skills.read")
async def skills_read(ctx, params):
    check_keys(params, {"source_id", "skill_id"}, ("max_chars",))
    src = _source(ctx, req_str(params, "source_id", 1, 64))
    skill_id = req_str(params, "skill_id", 1, 100)
    max_chars = opt_int(params, "max_chars", sfs.DEFAULT_LOAD_CHARS, 1, sfs.MAX_LOAD_CHARS)
    try:
        loaded = sfs.load_skill(src["path"], skill_id, max_chars)
    except WorkspaceFsError as exc:
        raise _fs_error(exc)
    return {
        "skill": _skill_out(loaded.summary), "content": loaded.content,
        "content_truncated": loaded.content_truncated,
        "provenance": {
            "source_id": src["id"], "source_name": src["name"], "source_path": src["path"],
            "relative_path": loaded.summary.relative_path, "sha256": loaded.sha256,
            "bytes_read": loaded.bytes_read,
            "modified_at": datetime.fromtimestamp(loaded.mtime, tz=timezone.utc).isoformat()},
        "authority": "guidance_only", "grants_permissions": False, "auto_applied": False,
        "notice": sfs.GUIDANCE_NOTICE,
    }


# -- approvals ------------------------------------------------------------------------

@method("approvals.capabilities")
async def approvals_capabilities(ctx, params):
    check_keys(params)
    return APPROVAL_CAPABILITIES


@method("approvals.list")
async def approvals_list(ctx, params):
    check_keys(params, optional=("run_id",))
    return {"approvals": ctx.approvals.list_pending(opt_str(params, "run_id"))}


@method("approvals.decide")
async def approvals_decide(ctx, params):
    check_keys(params, {"run_id", "approval_id", "decision"}, ("note",))
    try:
        return ctx.approvals.decide(
            req_str(params, "run_id", 1, 64), req_str(params, "approval_id", 1, 64),
            req_enum(params, "decision", ("approve", "deny")), opt_str(params, "note", 0, 1000))
    except ApprovalNotFound:
        raise api_error(404, "Approval not found")
    except ApprovalConflict as exc:
        raise api_error(409, f"Approval is no longer pending ({exc.status})")


# -- deliverables -----------------------------------------------------------------------

def _eligible(exc: RunNotEligible):
    return api_error(exc.status, exc.reason)


@method("deliverables.create")
async def deliverables_create(ctx, params):
    check_keys(params, {"run_id"}, ("filename",))
    try:
        return ctx.deliverables.create(req_str(params, "run_id", 1, 64), opt_str(params, "filename", 0, 200))
    except RunNotEligible as exc:
        raise _eligible(exc)
    except DraftTooLarge:
        raise api_error(413, f"assistant reply exceeds {MAX_DELIVERABLE_BYTES} bytes")
    except TooManyDrafts:
        raise api_error(429, "too many drafts for this run")


@method("deliverables.list")
async def deliverables_list(ctx, params):
    check_keys(params, {"run_id"})
    try:
        return {"drafts": ctx.deliverables.list(req_str(params, "run_id", 1, 64))}
    except RunNotEligible as exc:
        raise _eligible(exc)


@method("deliverables.get")
async def deliverables_get(ctx, params):
    check_keys(params, {"run_id", "draft_id"})
    try:
        return ctx.deliverables.get(req_str(params, "run_id", 1, 64), req_str(params, "draft_id", 1, 64))
    except DraftNotFound:
        raise api_error(404, "Draft not found")


@method("deliverables.decide")
async def deliverables_decide(ctx, params):
    check_keys(params, {"run_id", "draft_id", "decision", "content_sha256"}, ("note",))
    sha = req_str(params, "content_sha256", 64, 64)
    if not SHA256_RE.match(sha):
        raise bad("'content_sha256' must be 64 hex characters.")
    try:
        return ctx.deliverables.decide(
            req_str(params, "run_id", 1, 64), req_str(params, "draft_id", 1, 64),
            req_enum(params, "decision", ("approve", "reject")), sha, opt_str(params, "note", 0, 1000))
    except DraftNotFound:
        raise api_error(404, "Draft not found")
    except DraftConflict as exc:
        if exc.reason == "already_decided":
            raise api_error(409, f"Draft already decided ({exc.status})")
        raise api_error(409, "content_sha256 does not match the draft")


@method("deliverables.content")
async def deliverables_content(ctx, params):
    check_keys(params, {"run_id", "draft_id"})
    try:
        row = ctx.deliverables.download(req_str(params, "run_id", 1, 64), req_str(params, "draft_id", 1, 64))
    except DraftNotFound:
        raise api_error(404, "Draft not found")
    except DraftConflict as exc:
        if exc.reason == "integrity_check_failed":
            raise api_error(500, "stored draft failed its integrity check")
        raise api_error(409, f"Draft is not approved ({exc.status})")
    return {"content": row["content"], "media_type": f"{row['mime']}; charset=utf-8",
            "filename": row["filename"], "content_sha256": row["content_sha256"],
            "headers": {"X-Content-Type-Options": "nosniff", "Content-Security-Policy": "sandbox",
                        "Cache-Control": "no-store"}}
