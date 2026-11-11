"""Persisted per-run approval state (infrastructure only).

HONEST SCOPE: no engine adapter (opencode / claude-code / codex / agy) can
currently ask for an approval, so nothing in the product calls ``create``.
``APPROVAL_CAPABILITIES`` reports every engine as unsupported and must only
flip once an adapter really enforces approvals.

Guarantees (unchanged from the legacy implementation)
- ``create`` is service-only and only for an ACTIVE run, checked in the same
  transaction as the insert.
- ``decide`` is one conditional UPDATE (pending, same run, not expired, run
  still active); losers change nothing and get a distinct error.
- Run lifecycle code calls ``expire_for_run`` in the SAME transaction as the
  run's terminal transition.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any, Dict, List, Optional

from backend.pyworker.db.core import Database, iso, new_id, ts, utcnow

ACTIVE_RUN_STATUSES = ("queued", "running")
DECISIONS = {"approve": "approved", "deny": "denied"}
DEFAULT_TTL_SECONDS = 300
MAX_TTL_SECONDS = 3600

APPROVAL_CAPABILITIES: Dict[str, Dict[str, Any]] = {
    engine: {"tool_approvals": False, "reason": "adapter cannot request approvals; runs with its fixed policy"}
    for engine in ("opencode", "claude-code", "codex", "agy")
}


class ApprovalNotFound(Exception):
    pass


class ApprovalConflict(Exception):
    """Approval exists in this run but is no longer pending (decided/expired)."""

    def __init__(self, status: str):
        super().__init__(status)
        self.status = status


class RunNotActive(Exception):
    """Run missing or terminal: approvals cannot be created for it."""


def expire_for_run(db: Database, run_id: str, reason: str) -> int:
    """Expire pending approvals of a run inside the CALLER's transaction."""
    return db.update("approvals", {"status": "expired", "decided_at": ts(), "expired_reason": reason},
                     "run_id=? AND status='pending'", (run_id,))


def view(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": row["id"], "run_id": row["run_id"], "action_summary": row["action_summary"],
        "scope": row["scope"], "status": row["status"], "expires_at": iso(row["expires_at"]),
        "created_at": iso(row["created_at"]), "decided_at": iso(row["decided_at"]),
        "decision_note": row["decision_note"], "expired_reason": row["expired_reason"],
    }


class ApprovalManager:
    def __init__(self, db: Database):
        self._db = db

    def create(self, run_id: str, action_summary: str, scope: str = "",
               ttl_seconds: int = DEFAULT_TTL_SECONDS) -> Dict[str, Any]:
        ttl = max(1, min(int(ttl_seconds), MAX_TTL_SECONDS))
        now = utcnow()
        with self._db.tx():
            active = self._db.scalar(
                "SELECT id FROM conversation_runs WHERE id=? AND status IN ('queued','running')", (run_id,))
            if active is None:
                raise RunNotActive(run_id)
            row = {
                "id": new_id(), "run_id": run_id, "action_summary": action_summary[:4000],
                "scope": scope[:255], "status": "pending", "created_at": ts(now),
                "expires_at": ts(now + timedelta(seconds=ttl)),
            }
            self._db.insert("approvals", row)
        return view(self._db.one("SELECT * FROM approvals WHERE id=?", (row["id"],)))

    def expire_due(self) -> int:
        now = ts()
        with self._db.tx():
            return self._db.update(
                "approvals", {"status": "expired", "decided_at": now, "expired_reason": "timeout"},
                "status='pending' AND expires_at <= ?", (now,))

    def list_pending(self, run_id: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
        self.expire_due()
        sql = "SELECT * FROM approvals WHERE status='pending'"
        params: list = []
        if run_id is not None:
            sql += " AND run_id=?"
            params.append(run_id)
        sql += " ORDER BY created_at, id LIMIT ?"
        params.append(max(1, min(limit, 500)))
        return [view(r) for r in self._db.all(sql, params)]

    def get(self, approval_id: str, run_id: Optional[str] = None) -> Dict[str, Any]:
        row = self._db.one("SELECT * FROM approvals WHERE id=?", (approval_id,))
        if row is None or (run_id is not None and row["run_id"] != run_id):
            raise ApprovalNotFound(approval_id)
        return view(row)

    def decide(self, run_id: str, approval_id: str, decision: str,
               note: Optional[str] = None) -> Dict[str, Any]:
        if decision not in DECISIONS:
            raise ValueError(f"decision must be one of {sorted(DECISIONS)}")
        now = ts()
        with self._db.tx():
            changed = self._db.update(
                "approvals",
                {"status": DECISIONS[decision], "decided_at": now,
                 "decision_note": note[:1000] if note else None},
                "id=? AND run_id=? AND status='pending' AND expires_at > ? AND EXISTS ("
                "SELECT 1 FROM conversation_runs r WHERE r.id=approvals.run_id "
                "AND r.status IN ('queued','running'))",
                (approval_id, run_id, now))
        if changed:
            return view(self._db.one("SELECT * FROM approvals WHERE id=?", (approval_id,)))
        row = self._db.one("SELECT * FROM approvals WHERE id=?", (approval_id,))
        if row is None or row["run_id"] != run_id:
            raise ApprovalNotFound(approval_id)
        if row["status"] == "pending":
            run = self._db.one("SELECT status FROM conversation_runs WHERE id=?", (run_id,))
            reason = ("timeout" if run is not None and run["status"] in ACTIVE_RUN_STATUSES
                      else f"run_{run['status'] if run else 'missing'}")
            with self._db.tx():
                self._db.update("approvals", {"status": "expired", "decided_at": ts(), "expired_reason": reason},
                                "id=? AND status='pending'", (approval_id,))
            row = self._db.one("SELECT * FROM approvals WHERE id=?", (approval_id,))
        raise ApprovalConflict(row["status"])
