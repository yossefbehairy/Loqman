"""
Persisted per-run approval state (infrastructure only).

HONEST SCOPE: no engine adapter (opencode / claude-code / codex / agy) can
currently ask for an approval, so nothing in the product calls `create`. This
module is the durable state machine the future adapters will use; it is not
attached to agent tools. `APPROVAL_CAPABILITIES` reports every engine as
unsupported and must only flip once an adapter really enforces approvals.

Guarantees
- `create` is a backend-service call (no HTTP route): only for an ACTIVE run
  (queued/running), checked inside the same transaction as the insert.
- `decide` is one conditional UPDATE: status must still be 'pending', the
  approval must belong to the given run, must not be past `expires_at`, and the
  run must still be active. Whoever loses (duplicate, late, wrong run) changes
  nothing and gets a distinct error.
- Run lifecycle code calls `expire_for_run` in the SAME transaction as the
  run's terminal transition, so a terminal run never has a pending approval.
"""

from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

from sqlalchemy import and_, exists, select, update
from sqlalchemy.orm import Session

from backend.app.db.models import Approval, ConversationRun

ACTIVE_RUN_STATUSES = ("queued", "running")
DECISIONS = {"approve": "approved", "deny": "denied"}
DEFAULT_TTL_SECONDS = 300
MAX_TTL_SECONDS = 3600

# No adapter can request approvals today; do not claim otherwise.
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


def _now() -> datetime:
    return datetime.now(timezone.utc)


def expire_for_run(db: Session, run_id: str, reason: str) -> int:
    """Expire every pending approval of a run inside the CALLER's transaction.
    The caller commits (together with the run transition)."""
    res = db.execute(
        update(Approval)
        .where(Approval.run_id == run_id, Approval.status == "pending")
        .values(status="expired", decided_at=_now(), expired_reason=reason))
    return res.rowcount or 0


def view(row: Approval) -> Dict[str, Any]:
    return {
        "id": row.id, "run_id": row.run_id, "action_summary": row.action_summary,
        "scope": row.scope, "status": row.status, "expires_at": row.expires_at,
        "created_at": row.created_at, "decided_at": row.decided_at,
        "decision_note": row.decision_note, "expired_reason": row.expired_reason,
    }


class ApprovalManager:
    def __init__(self, session_factory: Optional[Callable[[], Session]] = None):
        self._session_factory = session_factory

    def _session(self) -> Session:
        factory = self._session_factory
        if factory is None:
            from backend.app.db.session import SessionLocal
            factory = SessionLocal
        return factory()

    def create(self, run_id: str, action_summary: str, scope: str = "",
               ttl_seconds: int = DEFAULT_TTL_SECONDS) -> Dict[str, Any]:
        """Service-only. Insert iff the run is active, atomically (the insert
        selects from the run row with the status predicate)."""
        ttl = max(1, min(int(ttl_seconds), MAX_TTL_SECONDS))
        db = self._session()
        try:
            now = _now()
            approval = Approval(run_id=run_id, action_summary=action_summary[:4000],
                                scope=scope[:255], status="pending",
                                created_at=now, expires_at=now + timedelta(seconds=ttl))
            # Hold the write lock before checking the run, so a concurrent
            # terminal transition either precedes (we refuse) or follows (it
            # then expires our row in its own transaction).
            db.execute(update(ConversationRun).where(ConversationRun.id == run_id)
                       .values(status=ConversationRun.status))
            active = db.execute(select(ConversationRun.id).where(
                ConversationRun.id == run_id,
                ConversationRun.status.in_(ACTIVE_RUN_STATUSES))).scalar()
            if active is None:
                db.rollback()
                raise RunNotActive(run_id)
            db.add(approval)
            db.flush()
            out = view(approval)
            db.commit()
            return out
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def expire_due(self) -> int:
        """Mark pending approvals past their expiry as expired."""
        db = self._session()
        try:
            res = db.execute(
                update(Approval)
                .where(Approval.status == "pending", Approval.expires_at <= _now())
                .values(status="expired", decided_at=_now(), expired_reason="timeout"))
            db.commit()
            return res.rowcount or 0
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def list_pending(self, run_id: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
        self.expire_due()
        db = self._session()
        try:
            q = select(Approval).where(Approval.status == "pending")
            if run_id is not None:
                q = q.where(Approval.run_id == run_id)
            rows = db.execute(q.order_by(Approval.created_at, Approval.id)
                              .limit(max(1, min(limit, 500)))).scalars().all()
            return [view(r) for r in rows]
        finally:
            db.close()

    def get(self, approval_id: str, run_id: Optional[str] = None) -> Dict[str, Any]:
        db = self._session()
        try:
            row = db.get(Approval, approval_id)
            if row is None or (run_id is not None and row.run_id != run_id):
                raise ApprovalNotFound(approval_id)
            return view(row)
        finally:
            db.close()

    def decide(self, run_id: str, approval_id: str, decision: str,
               note: Optional[str] = None) -> Dict[str, Any]:
        """Atomic one-time decision, scoped to `run_id`.
        Raises ApprovalNotFound (unknown id or belongs to another run) or
        ApprovalConflict (already decided / expired / run no longer active)."""
        if decision not in DECISIONS:
            raise ValueError(f"decision must be one of {sorted(DECISIONS)}")
        db = self._session()
        try:
            now = _now()
            res = db.execute(
                update(Approval)
                .where(and_(
                    Approval.id == approval_id, Approval.run_id == run_id,
                    Approval.status == "pending", Approval.expires_at > now,
                    exists().where(ConversationRun.id == Approval.run_id,
                                   ConversationRun.status.in_(ACTIVE_RUN_STATUSES))))
                .values(status=DECISIONS[decision], decided_at=now,
                        decision_note=(note[:1000] if note else None)))
            if res.rowcount:
                db.commit()
                return view(db.get(Approval, approval_id))
            db.rollback()
            row = db.get(Approval, approval_id)
            if row is None or row.run_id != run_id:
                raise ApprovalNotFound(approval_id)
            if row.status == "pending":
                # Lost because it timed out or its run ended: close it now.
                run = db.get(ConversationRun, run_id)
                reason = "timeout" if run is not None and run.status in ACTIVE_RUN_STATUSES \
                    else f"run_{run.status if run else 'missing'}"
                db.execute(update(Approval)
                           .where(Approval.id == approval_id, Approval.status == "pending")
                           .values(status="expired", decided_at=_now(), expired_reason=reason))
                db.commit()
                db.refresh(row)
            raise ApprovalConflict(row.status)
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()
