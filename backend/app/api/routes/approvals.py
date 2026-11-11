"""Approval state API (list pending, scoped decision). Not yet wired in main.py.

There is deliberately NO create endpoint: approvals are created only by backend
services for an active run. No engine adapter can request approvals today, so
`GET /api/approvals/capabilities` reports every engine as unsupported.
"""
from datetime import datetime
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from backend.app.core.approvals import (
    APPROVAL_CAPABILITIES, ApprovalConflict, ApprovalManager, ApprovalNotFound,
)

router = APIRouter(tags=["approvals"])

_manager: Optional[ApprovalManager] = None


def get_approval_manager() -> ApprovalManager:
    """Process-wide manager; override via app.dependency_overrides in tests."""
    global _manager
    if _manager is None:
        _manager = ApprovalManager()
    return _manager


class ApprovalOut(BaseModel):
    id: str
    run_id: str
    action_summary: str
    scope: str
    status: str
    expires_at: datetime
    created_at: datetime
    decided_at: Optional[datetime] = None
    decision_note: Optional[str] = None
    expired_reason: Optional[str] = None


class DecisionIn(BaseModel):
    decision: Literal["approve", "deny"]
    note: Optional[str] = Field(default=None, max_length=1000)


@router.get("/api/approvals/capabilities")
def approval_capabilities():
    return APPROVAL_CAPABILITIES


@router.get("/api/approvals", response_model=list[ApprovalOut])
def list_pending(run_id: Optional[str] = None, manager: ApprovalManager = Depends(get_approval_manager)):
    return manager.list_pending(run_id)


@router.get("/api/conversation-runs/{run_id}/approvals", response_model=list[ApprovalOut])
def list_run_pending(run_id: str, manager: ApprovalManager = Depends(get_approval_manager)):
    return manager.list_pending(run_id)


@router.post("/api/conversation-runs/{run_id}/approvals/{approval_id}/decision", response_model=ApprovalOut)
def decide(run_id: str, approval_id: str, payload: DecisionIn,
           manager: ApprovalManager = Depends(get_approval_manager)):
    try:
        return manager.decide(run_id, approval_id, payload.decision, payload.note)
    except ApprovalNotFound:
        raise HTTPException(status_code=404, detail="Approval not found")
    except ApprovalConflict as exc:
        raise HTTPException(status_code=409, detail=f"Approval is no longer pending ({exc.status})")
