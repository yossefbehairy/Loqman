"""
Usage Ledger API — read-only views over every recorded AI call.

GET /api/usage/events   : recent raw events (filters: kind, model_id, success)
GET /api/usage/summary  : totals + per-model and per-kind aggregates
"""

import uuid
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import cast, func, Integer, case, or_
from sqlalchemy.orm import Session

from backend.app.db.models import UsageEvent
from backend.app.db.session import get_db

router = APIRouter(prefix="/api/usage", tags=["usage"])


class UsageEventOut(BaseModel):
    id: str
    run_id: Optional[str] = None
    created_at: datetime
    kind: str
    agent_id: str
    model_id: str
    provider_id: str
    variant_id: Optional[str] = None
    session_id: Optional[str] = None
    duration_ms: int
    tokens_in: Optional[int] = None
    tokens_out: Optional[int] = None
    tokens_cache_read: Optional[int] = None
    success: bool
    error_kind: Optional[str] = None
    error_detail: Optional[str] = None


class UsageGroup(BaseModel):
    key: str
    events: int
    succeeded: int
    failed: int
    total_duration_ms: int
    avg_duration_ms: int
    tokens_in: int
    tokens_out: int
    tokens_cache_read: int
    token_usage_unknown_events: int


class UsageTotals(BaseModel):
    events: int
    succeeded: int
    failed: int
    total_duration_ms: int
    avg_duration_ms: int
    tokens_in: int
    tokens_out: int
    tokens_cache_read: int
    token_usage_unknown_events: int


class UsageSummary(BaseModel):
    totals: UsageTotals
    by_model: List[UsageGroup]
    by_kind: List[UsageGroup]


# SQLite stores booleans as 0/1. sum() over a Boolean column is typed Boolean
# by SQLAlchemy (2 -> True), so cast to Integer first for real arithmetic.
def _sums():
    return (
        func.count(UsageEvent.id),
        func.coalesce(func.sum(UsageEvent.duration_ms), 0),
        func.coalesce(func.sum(UsageEvent.tokens_in), 0),
        func.coalesce(func.sum(UsageEvent.tokens_out), 0),
        func.coalesce(func.sum(cast(UsageEvent.success, Integer)), 0),
        func.coalesce(func.sum(UsageEvent.tokens_cache_read), 0),
        func.coalesce(func.sum(case((or_(UsageEvent.tokens_in.is_(None), UsageEvent.tokens_out.is_(None)), 1), else_=0)), 0),
    )


def _usage_totals(
    events: int, duration: int, succeeded: int, tokens_in: int, tokens_out: int,
    tokens_cache_read: int, unknown: int,
) -> UsageTotals:
    events = int(events or 0)
    succeeded = int(succeeded or 0)
    duration = int(duration or 0)
    return UsageTotals(
        events=events,
        succeeded=succeeded,
        failed=events - succeeded,
        total_duration_ms=duration,
        avg_duration_ms=(duration // events) if events else 0,
        tokens_in=int(tokens_in or 0),
        tokens_out=int(tokens_out or 0),
        tokens_cache_read=int(tokens_cache_read or 0),
        token_usage_unknown_events=int(unknown or 0),
    )


@router.get("/events", response_model=List[UsageEventOut])
def list_events(
    limit: int = Query(default=100, ge=1, le=500),
    kind: Optional[str] = None,
    model_id: Optional[str] = None,
    success: Optional[bool] = None,
    db: Session = Depends(get_db),
):
    q = db.query(UsageEvent)
    if kind:
        q = q.filter(UsageEvent.kind == kind)
    if model_id:
        q = q.filter(UsageEvent.model_id == model_id)
    if success is not None:
        q = q.filter(UsageEvent.success.is_(success))
    rows = (
        q.order_by(UsageEvent.created_at.desc(), UsageEvent.id.desc())
        .limit(limit)
        .all()
    )
    return [_to_out(r) for r in rows]


@router.get("/summary", response_model=UsageSummary)
def usage_summary(db: Session = Depends(get_db)):
    def _aggregate(rows) -> List[UsageGroup]:
        groups: List[UsageGroup] = []
        # Column order MUST mirror _sums(): count, duration, tokens_in,
        # tokens_out, succeeded, tokens_cache_read. (A mismatch here produced
        # token sums where success counts belong — caught live.)
        for key, events, duration, tin, tout, succeeded, tcache, unknown in rows:
            groups.append(UsageGroup(
                key=key or "unknown",
                events=int(events or 0),
                succeeded=int(succeeded or 0),
                failed=int(events or 0) - int(succeeded or 0),
                total_duration_ms=int(duration or 0),
                avg_duration_ms=(int(duration or 0) // int(events or 1)) if events else 0,
                tokens_in=int(tin or 0),
                tokens_out=int(tout or 0),
                tokens_cache_read=int(tcache or 0),
                token_usage_unknown_events=int(unknown or 0),
            ))
        return groups

    by_model = _aggregate(
        db.query(
            UsageEvent.model_id, *_sums()
        ).group_by(UsageEvent.model_id).order_by(func.count(UsageEvent.id).desc()).limit(25).all()
    )
    by_kind = _aggregate(
        db.query(
            UsageEvent.kind, *_sums()
        ).group_by(UsageEvent.kind).order_by(func.count(UsageEvent.id).desc()).limit(25).all()
    )
    events, duration, tin, tout, succeeded, tcache, unknown = db.query(*_sums()).one()

    return UsageSummary(
        totals=_usage_totals(events, duration, succeeded, tin, tout, tcache, unknown),
        by_model=by_model,
        by_kind=by_kind,
    )


def _to_out(r: UsageEvent) -> UsageEventOut:
    return UsageEventOut(
        id=r.id or str(uuid.uuid4()),
        run_id=r.run_id,
        created_at=r.created_at or datetime.now().replace(tzinfo=None),
        kind=r.kind,
        agent_id=r.agent_id,
        model_id=r.model_id,
        provider_id=r.provider_id,
        variant_id=r.variant_id,
        session_id=r.session_id,
        duration_ms=r.duration_ms or 0,
        tokens_in=r.tokens_in,
        tokens_out=r.tokens_out,
        tokens_cache_read=r.tokens_cache_read,
        success=bool(r.success),
        error_kind=r.error_kind,
        error_detail=r.error_detail,
    )
