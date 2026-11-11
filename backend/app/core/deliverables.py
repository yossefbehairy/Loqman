"""Reviewable deliverable drafts built from a run's ACTUAL assistant reply.

Scope and honesty
- The draft text is copied server-side from the ConversationMessage that the
  succeeded run itself produced (`run.assistant_message_seq`). The client can
  never supply content. No model is called and NOTHING is written to a
  workspace or any file: "approved" only means the user accepted exactly this
  text/hash for download from the API.
- Separate from `Approval` on purpose: that row expires when its run ends, but
  a deliverable is reviewed AFTER the run succeeded.

Guarantees
- Content, sha256, size and provenance are immutable after insert.
- A decision is one conditional UPDATE (`status='pending_review'`, matching
  draft id, run id and the caller-supplied content sha256). Duplicate, late,
  cross-run or stale-hash decisions change nothing (no replay).
- Download re-hashes the stored text and refuses on mismatch.
"""

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.core import run_inputs, run_skills
from backend.app.db.models import (
    Conversation, ConversationMessage, ConversationRun, DeliverableDraft,
)

MAX_DELIVERABLE_BYTES = 256 * 1024
MAX_DRAFTS_PER_RUN = 10
DECISIONS = {"approve": "approved", "reject": "rejected"}
_EXT_MIME = {".md": "text/markdown", ".txt": "text/plain"}
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


class DraftNotFound(Exception):
    pass


class DraftConflict(Exception):
    """Draft exists in this run but cannot take this decision/read."""

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


def view(row: DeliverableDraft, include_content: bool = False) -> Dict[str, Any]:
    out = {
        "id": row.id, "run_id": row.run_id, "filename": row.filename, "mime": row.mime,
        "status": row.status, "content_sha256": row.content_sha256,
        "size_bytes": row.size_bytes, "provenance": json.loads(row.provenance_json or "{}"),
        "created_at": row.created_at, "decided_at": row.decided_at,
        "decision_note": row.decision_note,
        # Factual: this feature never touches the filesystem.
        "written_to_workspace": False,
        "downloadable": row.status == "approved",
    }
    if include_content:
        out["content"] = row.content
    return out


def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value else None


class DeliverableManager:
    def __init__(self, session_factory: Optional[Callable[[], Session]] = None):
        self._session_factory = session_factory

    def _session(self) -> Session:
        factory = self._session_factory
        if factory is None:
            from backend.app.db.session import SessionLocal
            factory = SessionLocal
        return factory()

    def create(self, run_id: str, filename: Optional[str] = None) -> Dict[str, Any]:
        """Snapshot the run's real assistant reply. Idempotent per
        (run, content hash, filename): repeating returns the existing draft
        in whatever state it is in (a rejected draft is never reopened)."""
        db = self._session()
        try:
            run = db.get(ConversationRun, run_id)
            if run is None:
                raise RunNotEligible("run not found", 404)
            if run.status != "succeeded":
                raise RunNotEligible(f"run is {run.status}; only a succeeded run can produce a deliverable")
            if run.assistant_message_seq is None:
                raise RunNotEligible("run has no assistant reply")
            msg = db.get(ConversationMessage, run.assistant_message_seq)
            if msg is None or msg.conversation_id != run.conversation_id or msg.role != "assistant":
                raise RunNotEligible("assistant reply is unavailable")
            text = msg.content
            if not text or not text.strip():
                raise RunNotEligible("assistant reply is empty", 422)
            data = text.encode("utf-8")
            if len(data) > MAX_DELIVERABLE_BYTES:
                raise DraftTooLarge(len(data))
            name = safe_filename(filename, run.id)
            digest = hashlib.sha256(data).hexdigest()

            # Serialize the per-run draft count and insert on SQLite.
            db.execute(update(ConversationRun).where(ConversationRun.id == run_id)
                       .values(status=ConversationRun.status))
            existing = db.execute(select(DeliverableDraft).where(
                DeliverableDraft.run_id == run_id, DeliverableDraft.content_sha256 == digest,
                DeliverableDraft.filename == name)).scalar()
            if existing is not None:
                return view(existing)
            count = len(db.execute(select(DeliverableDraft.id).where(
                DeliverableDraft.run_id == run_id)).all())
            if count >= MAX_DRAFTS_PER_RUN:
                raise TooManyDrafts(run_id)

            conv = db.get(Conversation, run.conversation_id)
            provenance = {
                "source": "conversation_run_assistant_message",
                "run_id": run.id, "conversation_id": run.conversation_id,
                "workspace_id": conv.workspace_id if conv else None,
                "assistant_message_seq": msg.seq, "user_message_seq": run.user_message_seq,
                "message_created_at": _iso(msg.created_at),
                "engine": run.engine, "model_id": run.model_id, "variant": run.variant,
                "run_status": run.status,
                "run_created_at": _iso(run.created_at), "run_started_at": _iso(run.started_at),
                "run_finished_at": _iso(run.finished_at),
                "skills": run_skills.public_meta(run.skills_json),
                "input_files": run_inputs.public_meta(run.input_files_json),
                "model_called_during_draft_creation": False,
            }
            row = DeliverableDraft(
                run_id=run.id, filename=name,
                mime=_EXT_MIME[name[name.rfind("."):]], content=text,
                content_sha256=digest, size_bytes=len(data),
                provenance_json=json.dumps(provenance, ensure_ascii=False),
                status="pending_review", created_at=datetime.now(timezone.utc))
            db.add(row)
            try:
                db.flush()
                out = view(row)
                db.commit()
                return out
            except IntegrityError:
                # Lost a creation race (or the run vanished): return the winner.
                db.rollback()
                winner = db.execute(select(DeliverableDraft).where(
                    DeliverableDraft.run_id == run_id,
                    DeliverableDraft.content_sha256 == digest,
                    DeliverableDraft.filename == name)).scalar()
                if winner is None:
                    raise RunNotEligible("run not found", 404)
                return view(winner)
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def list(self, run_id: str) -> List[Dict[str, Any]]:
        db = self._session()
        try:
            if db.get(ConversationRun, run_id) is None:
                raise RunNotEligible("run not found", 404)
            rows = db.execute(select(DeliverableDraft).where(DeliverableDraft.run_id == run_id)
                              .order_by(DeliverableDraft.created_at, DeliverableDraft.id)).scalars().all()
            return [view(r) for r in rows]
        finally:
            db.close()

    def _get(self, db: Session, run_id: str, draft_id: str) -> DeliverableDraft:
        row = db.get(DeliverableDraft, draft_id)
        if row is None or row.run_id != run_id:
            raise DraftNotFound(draft_id)
        return row

    def get(self, run_id: str, draft_id: str) -> Dict[str, Any]:
        """Full review view including the exact text."""
        db = self._session()
        try:
            return view(self._get(db, run_id, draft_id), include_content=True)
        finally:
            db.close()

    def decide(self, run_id: str, draft_id: str, decision: str, content_sha256: str,
               note: Optional[str] = None) -> Dict[str, Any]:
        """One-time decision bound to the exact reviewed hash."""
        if decision not in DECISIONS:
            raise ValueError(f"decision must be one of {sorted(DECISIONS)}")
        db = self._session()
        try:
            res = db.execute(
                update(DeliverableDraft)
                .where(DeliverableDraft.id == draft_id, DeliverableDraft.run_id == run_id,
                       DeliverableDraft.status == "pending_review",
                       DeliverableDraft.content_sha256 == content_sha256.lower())
                .values(status=DECISIONS[decision], decided_at=datetime.now(timezone.utc),
                        decision_note=(note[:1000] if note else None)))
            if res.rowcount:
                db.commit()
                return view(db.get(DeliverableDraft, draft_id))
            db.rollback()
            row = self._get(db, run_id, draft_id)
            if row.status != "pending_review":
                raise DraftConflict("already_decided", row.status)
            raise DraftConflict("content_hash_mismatch", row.status)
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def download(self, run_id: str, draft_id: str) -> DeliverableDraft:
        """Return the approved row (detached) after verifying stored integrity."""
        db = self._session()
        try:
            row = self._get(db, run_id, draft_id)
            if row.status != "approved":
                raise DraftConflict("not_approved", row.status)
            if sha256_text(row.content) != row.content_sha256:
                raise DraftConflict("integrity_check_failed", row.status)
            db.expunge(row)
            return row
        finally:
            db.close()
