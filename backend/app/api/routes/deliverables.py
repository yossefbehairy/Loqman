"""Deliverable drafts: request from a succeeded run, review, approve/reject once, download.

Router is NOT mounted here; the parent adds `app.include_router(deliverables.router)`.
All paths are under /api/, so the existing local-token middleware authenticates them.

Nothing in this module writes to disk or a workspace, and no model is called.
"""
from datetime import datetime
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

from backend.app.core.deliverables import (
    DeliverableManager, DraftConflict, DraftNotFound, DraftTooLarge, MAX_DELIVERABLE_BYTES,
    RunNotEligible, TooManyDrafts,
)

router = APIRouter(tags=["deliverables"])

_manager: Optional[DeliverableManager] = None


def get_deliverable_manager() -> DeliverableManager:
    """Process-wide manager; override via app.dependency_overrides in tests."""
    global _manager
    if _manager is None:
        _manager = DeliverableManager()
    return _manager


class DraftCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")  # content can never be supplied by the client
    filename: Optional[str] = Field(default=None, max_length=200)


class DraftOut(BaseModel):
    id: str
    run_id: str
    filename: str
    mime: str
    status: str
    content_sha256: str
    size_bytes: int
    provenance: dict[str, Any]
    created_at: datetime
    decided_at: Optional[datetime] = None
    decision_note: Optional[str] = None
    written_to_workspace: bool = False
    downloadable: bool = False
    content: Optional[str] = None


class DecisionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["approve", "reject"]
    # Hash of the exact text the reviewer saw; a mismatch is refused.
    content_sha256: str = Field(pattern=r"^[0-9a-fA-F]{64}$")
    note: Optional[str] = Field(default=None, max_length=1000)


def _eligible(exc: RunNotEligible):
    raise HTTPException(status_code=exc.status, detail=exc.reason)


@router.post("/api/conversation-runs/{run_id}/deliverables", response_model=DraftOut, status_code=201)
def create_draft(run_id: str, payload: DraftCreate, manager: DeliverableManager = Depends(get_deliverable_manager)):
    try:
        return manager.create(run_id, payload.filename)
    except RunNotEligible as exc:
        _eligible(exc)
    except DraftTooLarge:
        raise HTTPException(413, f"assistant reply exceeds {MAX_DELIVERABLE_BYTES} bytes")
    except TooManyDrafts:
        raise HTTPException(429, "too many drafts for this run")


@router.get("/api/conversation-runs/{run_id}/deliverables", response_model=list[DraftOut])
def list_drafts(run_id: str, manager: DeliverableManager = Depends(get_deliverable_manager)):
    try:
        return manager.list(run_id)
    except RunNotEligible as exc:
        _eligible(exc)


@router.get("/api/conversation-runs/{run_id}/deliverables/{draft_id}", response_model=DraftOut)
def get_draft(run_id: str, draft_id: str, manager: DeliverableManager = Depends(get_deliverable_manager)):
    try:
        return manager.get(run_id, draft_id)
    except DraftNotFound:
        raise HTTPException(404, "Draft not found")


@router.post("/api/conversation-runs/{run_id}/deliverables/{draft_id}/decision", response_model=DraftOut)
def decide_draft(run_id: str, draft_id: str, payload: DecisionIn,
                 manager: DeliverableManager = Depends(get_deliverable_manager)):
    try:
        return manager.decide(run_id, draft_id, payload.decision, payload.content_sha256, payload.note)
    except DraftNotFound:
        raise HTTPException(404, "Draft not found")
    except DraftConflict as exc:
        if exc.reason == "already_decided":
            raise HTTPException(409, f"Draft already decided ({exc.status})")
        raise HTTPException(409, "content_sha256 does not match the draft")


@router.get("/api/conversation-runs/{run_id}/deliverables/{draft_id}/content")
def download_draft(run_id: str, draft_id: str, manager: DeliverableManager = Depends(get_deliverable_manager)):
    try:
        row = manager.download(run_id, draft_id)
    except DraftNotFound:
        raise HTTPException(404, "Draft not found")
    except DraftConflict as exc:
        if exc.reason == "integrity_check_failed":
            raise HTTPException(500, "stored draft failed its integrity check")
        raise HTTPException(409, f"Draft is not approved ({exc.status})")
    return Response(
        content=row.content.encode("utf-8"),
        media_type=f"{row.mime}; charset=utf-8",
        headers={
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": f'attachment; filename="{row.filename}"',
            "Content-Security-Policy": "sandbox",
            "X-Content-SHA256": row.content_sha256,
            "Cache-Control": "no-store",
        },
    )
