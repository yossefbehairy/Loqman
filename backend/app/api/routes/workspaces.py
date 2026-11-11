"""Workspace registry and read-only artifact access.

Router is NOT mounted here; the parent adds `app.include_router(workspaces.router)`
(and `artifacts_router`). Both live under /api/, so the existing local-token
middleware in main.py authenticates them.

No endpoint writes, creates or deletes files on disk. Registering an artifact
only records a reference; it implies no approval or review of the content.
"""

import mimetypes
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.core import workspace_fs as fs
from backend.app.db.models import Artifact, Workspace, Conversation, ConversationRun
from backend.app.db.session import get_db

router = APIRouter(prefix="/api/workspaces", tags=["workspaces"])
artifacts_router = APIRouter(prefix="/api/artifacts", tags=["artifacts"])


class WorkspaceCreate(BaseModel):
    path: str = Field(min_length=1, max_length=4096)
    name: Optional[str] = Field(default=None, min_length=1, max_length=255)


class WorkspaceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    path: str
    created_at: datetime


class ArtifactCreate(BaseModel):
    path: str = Field(min_length=1, max_length=4096, description="Workspace-relative file path")
    run_id: Optional[str] = Field(default=None, max_length=36)


class ArtifactOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    workspace_id: str
    run_id: Optional[str]
    relative_path: str
    mime: str
    size: int
    created_at: datetime


def _raise(exc: fs.WorkspaceFsError):
    raise HTTPException(status_code=exc.status, detail=exc.detail)


def _get_workspace(db: Session, workspace_id: str) -> Workspace:
    ws = db.get(Workspace, workspace_id)
    if ws is None:
        raise HTTPException(404, "workspace not found")
    return ws


@router.post("", response_model=WorkspaceOut, status_code=201)
def create_workspace(payload: WorkspaceCreate, db: Session = Depends(get_db)):
    try:
        real = fs.canonical_workspace_dir(payload.path)
    except fs.WorkspaceFsError as exc:
        _raise(exc)
    import os
    name = payload.name or os.path.basename(real.rstrip("\\/")) or real
    row = Workspace(name=name, path=real)
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "workspace already registered")
    db.refresh(row)
    return row


@router.get("", response_model=list[WorkspaceOut])
def list_workspaces(db: Session = Depends(get_db)):
    return db.query(Workspace).order_by(Workspace.created_at, Workspace.id).limit(500).all()


@router.get("/{workspace_id}", response_model=WorkspaceOut)
def get_workspace(workspace_id: str, db: Session = Depends(get_db)):
    return _get_workspace(db, workspace_id)


@router.post("/{workspace_id}/artifacts", response_model=ArtifactOut, status_code=201)
def register_artifact(workspace_id: str, payload: ArtifactCreate, db: Session = Depends(get_db)):
    ws = _get_workspace(db, workspace_id)
    if payload.run_id:
        run = db.get(ConversationRun, payload.run_id)
        if run is None:
            raise HTTPException(404, "run not found")
        conversation = db.get(Conversation, run.conversation_id)
        if conversation is None or conversation.workspace_id != workspace_id:
            raise HTTPException(409, "run does not belong to this workspace")
    try:
        real, rel = fs.resolve_in_workspace(ws.path, payload.path)
        size = fs.file_size(real)
    except fs.WorkspaceFsError as exc:
        _raise(exc)
    except OSError:
        raise HTTPException(403, "file not accessible")
    mime = mimetypes.guess_type(rel)[0] or "application/octet-stream"
    row = Artifact(workspace_id=ws.id, run_id=payload.run_id, relative_path=rel, mime=mime, size=size)
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "artifact already registered")
    db.refresh(row)
    return row


@router.get("/{workspace_id}/artifacts", response_model=list[ArtifactOut])
def list_artifacts(workspace_id: str, db: Session = Depends(get_db)):
    _get_workspace(db, workspace_id)
    return (
        db.query(Artifact).filter(Artifact.workspace_id == workspace_id)
        .order_by(Artifact.created_at, Artifact.id).limit(500).all()
    )


def _get_artifact(db: Session, artifact_id: str) -> Artifact:
    row = db.get(Artifact, artifact_id)
    if row is None:
        raise HTTPException(404, "artifact not found")
    return row


@artifacts_router.get("/{artifact_id}", response_model=ArtifactOut)
def get_artifact(artifact_id: str, db: Session = Depends(get_db)):
    return _get_artifact(db, artifact_id)


@artifacts_router.get("/{artifact_id}/content")
def download_artifact(artifact_id: str, db: Session = Depends(get_db)):
    """Re-validates containment on every call; the file may have changed since registration."""
    row = _get_artifact(db, artifact_id)
    ws = _get_workspace(db, row.workspace_id)
    try:
        real, _ = fs.resolve_in_workspace(ws.path, row.relative_path)
        data = fs.read_limited(real, ws.path)
    except fs.WorkspaceFsError as exc:
        _raise(exc)
    except OSError:
        raise HTTPException(403, "file not accessible")
    return Response(
        content=data,
        media_type=row.mime,
        headers={
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": "attachment",
            "Content-Security-Policy": "sandbox",
        },
    )
