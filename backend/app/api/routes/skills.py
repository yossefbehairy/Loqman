"""Skills library: user-selected skill directories, metadata listing, bounded read.

Router is NOT mounted here; the parent adds ``app.include_router(skills.router)``
(under /api/, so the local-token middleware applies) and imports
``backend.app.db.skill_models`` so the table exists.

Nothing here writes to disk, executes scripts, applies a skill to an agent, or
grants permissions. Registering a source only records its path in the DB.
"""

import os
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.core import skills_fs as sfs
from backend.app.core.workspace_fs import WorkspaceFsError
from backend.app.db.session import get_db
from backend.app.db.skill_models import SkillSource

router = APIRouter(prefix="/api/skill-sources", tags=["skills"])


class SkillSourceCreate(BaseModel):
    path: str = Field(min_length=1, max_length=4096)
    name: Optional[str] = Field(default=None, min_length=1, max_length=255)


class SkillSourceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    path: str
    created_at: datetime


class SkillOut(BaseModel):
    id: str
    name: str
    description: str
    relative_path: str
    size_bytes: int
    metadata_valid: bool
    warnings: list[str]


class SkippedOut(BaseModel):
    entry: str
    reason: str


class SkillListOut(BaseModel):
    source: SkillSourceOut
    skills: list[SkillOut]
    skipped: list[SkippedOut]
    truncated: bool
    notice: str = sfs.GUIDANCE_NOTICE


class Provenance(BaseModel):
    source_id: str
    source_name: str
    source_path: str
    relative_path: str
    sha256: str
    bytes_read: int
    modified_at: datetime


class SkillContentOut(BaseModel):
    skill: SkillOut
    content: str
    content_truncated: bool
    provenance: Provenance
    authority: str = "guidance_only"
    grants_permissions: bool = False
    auto_applied: bool = False
    notice: str = sfs.GUIDANCE_NOTICE


def _http(exc: WorkspaceFsError) -> HTTPException:
    return HTTPException(status_code=exc.status, detail=exc.detail)


def _get(db: Session, source_id: str) -> SkillSource:
    src = db.get(SkillSource, source_id)
    if not src:
        raise HTTPException(status_code=404, detail="skill source not found")
    return src


def _skill_out(s: sfs.SkillSummary) -> SkillOut:
    return SkillOut(
        id=s.id, name=s.name, description=s.description, relative_path=s.relative_path,
        size_bytes=s.size_bytes, metadata_valid=s.metadata_valid, warnings=s.warnings,
    )


@router.post("", response_model=SkillSourceOut, status_code=201)
def register_source(body: SkillSourceCreate, db: Session = Depends(get_db)):
    try:
        real = sfs.validate_source_dir(body.path)
    except WorkspaceFsError as exc:
        raise _http(exc)
    name = (body.name or os.path.basename(real) or "skills").strip()[:255]
    src = SkillSource(name=name, path=real)
    db.add(src)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="skill source already registered")
    db.refresh(src)
    return src


@router.get("", response_model=list[SkillSourceOut])
def list_sources(db: Session = Depends(get_db)):
    return db.query(SkillSource).order_by(SkillSource.created_at, SkillSource.id).all()


@router.delete("/{source_id}", status_code=204)
def unregister_source(source_id: str, db: Session = Depends(get_db)):
    """Removes only the registration; files on disk are untouched."""
    db.delete(_get(db, source_id))
    db.commit()


@router.get("/{source_id}/skills", response_model=SkillListOut)
def list_skills(source_id: str, db: Session = Depends(get_db)):
    src = _get(db, source_id)
    try:
        scan = sfs.scan_source(src.path)
    except WorkspaceFsError as exc:
        raise _http(exc)
    return SkillListOut(
        source=SkillSourceOut.model_validate(src),
        skills=[_skill_out(s) for s in scan.skills],
        skipped=[SkippedOut(entry=s.entry, reason=s.reason) for s in scan.skipped],
        truncated=scan.truncated,
    )


@router.get("/{source_id}/skills/{skill_id}", response_model=SkillContentOut)
def read_skill(
    source_id: str,
    skill_id: str,
    max_chars: int = Query(default=sfs.DEFAULT_LOAD_CHARS, ge=1, le=sfs.MAX_LOAD_CHARS),
    db: Session = Depends(get_db),
):
    src = _get(db, source_id)
    try:
        loaded = sfs.load_skill(src.path, skill_id, max_chars)
    except WorkspaceFsError as exc:
        raise _http(exc)
    return SkillContentOut(
        skill=_skill_out(loaded.summary),
        content=loaded.content,
        content_truncated=loaded.content_truncated,
        provenance=Provenance(
            source_id=src.id,
            source_name=src.name,
            source_path=src.path,
            relative_path=loaded.summary.relative_path,
            sha256=loaded.sha256,
            bytes_read=loaded.bytes_read,
            modified_at=datetime.fromtimestamp(loaded.mtime, tz=timezone.utc),
        ),
    )
