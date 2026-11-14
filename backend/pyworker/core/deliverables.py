"""Reviewable deliverable drafts built from a run's ACTUAL assistant reply.

Scope and honesty (unchanged): the draft text is copied server-side from the
ConversationMessage the succeeded run produced; the client never supplies
content; no model is called and NOTHING is written to a workspace or file.
"approved" only means the user accepted exactly this text/hash for download.

Content, sha256, size and provenance are immutable after insert; a decision
is one conditional UPDATE bound to the reviewed hash; download re-hashes.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from typing import Any, Dict, List, Optional

from backend.pyworker.core import run_inputs, run_skills
from backend.pyworker.db.core import Database, iso, new_id, ts

MAX_DELIVERABLE_BYTES = 256 * 1024
MAX_DRAFTS_PER_RUN = 10
DECISIONS = {"approve": "approved", "reject": "rejected"}
_EXT_MIME = {".md": "text/markdown", ".txt": "text/plain"}
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


class DraftNotFound(Exception):
    pass


class DraftConflict(Exception):
    def __init__(self, reason: str, status: Optional[str] = None):
        super().__init__(reason)
        self.reason = reason
        self.status = status


class RunNotEligible(Exception):
    def __init__(self, reason: str, status: int = 409):
        super().__init__(reason)
        self.reason = reason
        self.status = status


class DraftTooLarge(Exception):
    pass


class TooManyDrafts(Exception):
    pass


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def safe_filename(requested: Optional[str], run_id: str) -> str:
    """Basename only, [A-Za-z0-9._-], .md/.txt; never a path."""
    default = f"deliverable-{run_id[:8]}.md"
    if not requested:
        return default
    name = re.split(r"[\\/]", requested.strip())[-1]
    name = _SAFE_NAME.sub("_", name).strip("._")[:100]
    stem, dot, ext = name.rpartition(".")
    if not dot or f".{ext.lower()}" not in _EXT_MIME or not stem:
        return default
    return f"{stem}.{ext.lower()}"


def view(row: Dict[str, Any], include_content: bool = False) -> Dict[str, Any]:
    out = {
        "id": row["id"], "run_id": row["run_id"], "filename": row["filename"], "mime": row["mime"],
        "status": row["status"], "content_sha256": row["content_sha256"],
        "size_bytes": row["size_bytes"], "provenance": json.loads(row["provenance_json"] or "{}"),
        "created_at": iso(row["created_at"]), "decided_at": iso(row["decided_at"]),
        "decision_note": row["decision_note"],
        "written_to_workspace": False,  # factual: this feature never touches the filesystem
        "downloadable": row["status"] == "approved",
        "content": None,
    }
    if include_content:
        out["content"] = row["content"]
    return out


class DeliverableManager:
    def __init__(self, db: Database):
        self._db = db

    def create(self, run_id: str, filename: Optional[str] = None) -> Dict[str, Any]:
        db = self._db
        run = db.one("SELECT * FROM conversation_runs WHERE id=?", (run_id,))
        if run is None:
            raise RunNotEligible("run not found", 404)
        if run["status"] != "succeeded":
            raise RunNotEligible(f"run is {run['status']}; only a succeeded run can produce a deliverable")
        if run["assistant_message_seq"] is None:
            raise RunNotEligible("run has no assistant reply")
        msg = db.one("SELECT * FROM conversation_messages WHERE seq=?", (run["assistant_message_seq"],))
        if msg is None or msg["conversation_id"] != run["conversation_id"] or msg["role"] != "assistant":
            raise RunNotEligible("assistant reply is unavailable")
        text = msg["content"]
        if not text or not text.strip():
            raise RunNotEligible("assistant reply is empty", 422)
        data = text.encode("utf-8")
        if len(data) > MAX_DELIVERABLE_BYTES:
            raise DraftTooLarge(len(data))
        name = safe_filename(filename, run["id"])
        digest = hashlib.sha256(data).hexdigest()

        def find():
            return db.one("SELECT * FROM deliverable_drafts WHERE run_id=? AND content_sha256=? AND filename=?",
                          (run_id, digest, name))

        with db.tx():
            existing = find()
            if existing is not None:
                return view(existing)
            count = db.scalar("SELECT COUNT(*) FROM deliverable_drafts WHERE run_id=?", (run_id,))
            if count >= MAX_DRAFTS_PER_RUN:
                raise TooManyDrafts(run_id)
            conv = db.one("SELECT * FROM conversations WHERE id=?", (run["conversation_id"],))
            provenance = {
                "source": "conversation_run_assistant_message",
                "run_id": run["id"], "conversation_id": run["conversation_id"],
                "workspace_id": conv["workspace_id"] if conv else None,
                "assistant_message_seq": msg["seq"], "user_message_seq": run["user_message_seq"],
                "message_created_at": iso(msg["created_at"]),
                "engine": run["engine"], "model_id": run["model_id"], "variant": run["variant"],
                "run_status": run["status"],
                "run_created_at": iso(run["created_at"]), "run_started_at": iso(run["started_at"]),
                "run_finished_at": iso(run["finished_at"]),
                "skills": run_skills.public_meta(run["skills_json"]),
                "input_files": run_inputs.public_meta(run["input_files_json"]),
                "model_called_during_draft_creation": False,
            }
            row = {
                "id": new_id(), "run_id": run["id"], "filename": name,
                "mime": _EXT_MIME[name[name.rfind("."):]], "content": text,
                "content_sha256": digest, "size_bytes": len(data),
                "provenance_json": json.dumps(provenance, ensure_ascii=False),
                "status": "pending_review", "created_at": ts(),
            }
            try:
                db.insert("deliverable_drafts", row)
            except sqlite3.IntegrityError:
                winner = find()
                if winner is None:
                    raise RunNotEligible("run not found", 404)
                return view(winner)
        return view(db.one("SELECT * FROM deliverable_drafts WHERE id=?", (row["id"],)))

    def list(self, run_id: str) -> List[Dict[str, Any]]:
        if self._db.one("SELECT id FROM conversation_runs WHERE id=?", (run_id,)) is None:
            raise RunNotEligible("run not found", 404)
        rows = self._db.all("SELECT * FROM deliverable_drafts WHERE run_id=? ORDER BY created_at, id", (run_id,))
        return [view(r) for r in rows]

    def list_all(self, limit: int = 200, cursor: Optional[str] = None) -> Dict[str, Any]:
        """One page of drafts across every run, newest first, each tagged with the conversation of its run.

        Order is (created_at, id) descending. ``cursor`` is the opaque ``next_cursor`` of the previous page and is
        exclusive: the page starts strictly after that row. ``next_cursor`` is None on the last page.
        """
        limit = max(1, min(int(limit), 500))
        where, args = "", []
        if cursor:
            created, sep, row_id = cursor.partition("|")
            if not sep or not created or not row_id:
                raise ValueError("invalid cursor")
            where = "WHERE (d.created_at < ? OR (d.created_at = ? AND d.id < ?)) "
            args = [created, created, row_id]
        rows = self._db.all(
            "SELECT d.*, r.conversation_id AS conversation_id FROM deliverable_drafts d "
            "LEFT JOIN conversation_runs r ON r.id = d.run_id " + where +
            "ORDER BY d.created_at DESC, d.id DESC LIMIT ?", (*args, limit + 1))
        more = len(rows) > limit
        rows = rows[:limit]
        next_cursor = f"{rows[-1]['created_at']}|{rows[-1]['id']}" if more else None
        return {"drafts": [{**view(r), "conversation_id": r["conversation_id"]} for r in rows],
                "next_cursor": next_cursor}

    def _get(self, run_id: str, draft_id: str) -> Dict[str, Any]:
        row = self._db.one("SELECT * FROM deliverable_drafts WHERE id=?", (draft_id,))
        if row is None or row["run_id"] != run_id:
            raise DraftNotFound(draft_id)
        return row

    def get(self, run_id: str, draft_id: str) -> Dict[str, Any]:
        return view(self._get(run_id, draft_id), include_content=True)

    def decide(self, run_id: str, draft_id: str, decision: str, content_sha256: str,
               note: Optional[str] = None) -> Dict[str, Any]:
        if decision not in DECISIONS:
            raise ValueError(f"decision must be one of {sorted(DECISIONS)}")
        with self._db.tx():
            changed = self._db.update(
                "deliverable_drafts",
                {"status": DECISIONS[decision], "decided_at": ts(), "decision_note": note[:1000] if note else None},
                "id=? AND run_id=? AND status='pending_review' AND content_sha256=?",
                (draft_id, run_id, content_sha256.lower()))
        if changed:
            return view(self._db.one("SELECT * FROM deliverable_drafts WHERE id=?", (draft_id,)))
        row = self._get(run_id, draft_id)
        if row["status"] != "pending_review":
            raise DraftConflict("already_decided", row["status"])
        raise DraftConflict("content_hash_mismatch", row["status"])

    def download(self, run_id: str, draft_id: str) -> Dict[str, Any]:
        row = self._get(run_id, draft_id)
        if row["status"] != "approved":
            raise DraftConflict("not_approved", row["status"])
        if sha256_text(row["content"]) != row["content_sha256"]:
            raise DraftConflict("integrity_check_failed", row["status"])
        return row
