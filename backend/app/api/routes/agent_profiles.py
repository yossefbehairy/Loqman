"""
Agent Profile Routes
Lists pre-configured AI employee roles, retrieves individual profiles, supports creating custom
employee profiles, and updating execution contracts, system prompts, and models.
"""

import json
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session
from backend.app.db.session import get_db
from backend.app.db.models import AgentProfile
from backend.app.db.repository import AgentProfileRepository
from backend.app.schemas.agent_profile import AgentProfileCreate, AgentProfileResponse

router = APIRouter(prefix="/api/agent-profiles", tags=["agent-profiles"])


class AgentProfileUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    role_type: Optional[str] = Field(default=None, min_length=1, max_length=64)
    description: Optional[str] = None
    instructions: Optional[str] = Field(default=None, min_length=1)
    input_schema_json: Optional[str] = None
    output_schema_json: Optional[str] = None
    default_model_id: Optional[str] = None
    default_variant_id: Optional[str] = None
    task_depth: Optional[str] = None
    settings_json: Optional[str] = None

    @field_validator("settings_json")
    @classmethod
    def validate_settings_json(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        if not v.strip():
            return "{}"
        try:
            json.loads(v)
        except Exception as exc:
            raise ValueError(f"settings_json must be valid JSON: {exc}")
        return v


@router.get("", response_model=List[AgentProfileResponse])
def list_agent_profiles(db: Session = Depends(get_db)):
    """
    Returns all registered AI employee roles and custom profiles.
    """
    repo = AgentProfileRepository(db)
    return repo.list_all()


@router.get("/{profile_id}", response_model=AgentProfileResponse)
def get_agent_profile(profile_id: str, db: Session = Depends(get_db)):
    """
    Returns a single AI employee profile by ID or recognized alias.
    """
    repo = AgentProfileRepository(db)
    profile = repo.get_by_id(profile_id)
    if not profile:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent profile '{profile_id}' not found"
        )
    return profile


@router.post("", response_model=AgentProfileResponse, status_code=status.HTTP_201_CREATED)
def create_agent_profile(payload: AgentProfileCreate, db: Session = Depends(get_db)):
    """
    Creates a new custom AI employee profile.
    """
    repo = AgentProfileRepository(db)
    data = payload.model_dump(exclude_unset=True)
    if not data.get("id"):
        import uuid
        data["id"] = f"custom-{uuid.uuid4().hex[:8]}"
    
    if repo.get_by_id(data["id"]):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Agent profile with id '{data['id']}' already exists"
        )
    return repo.create(data)


@router.delete("/{profile_id}", status_code=status.HTTP_200_OK)
def delete_agent_profile(profile_id: str, db: Session = Depends(get_db)):
    """
    Permanently removes an employee profile. The seeder only fills an empty
    catalog, so deleted profiles are never resurrected on restart.
    """
    repo = AgentProfileRepository(db)
    existing = repo.get_by_id(profile_id)
    if not existing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent profile '{profile_id}' not found"
        )
    db.query(AgentProfile).filter(AgentProfile.id == existing.id).delete()
    db.commit()
    return {"deleted": existing.id}


@router.put("/{profile_id}", response_model=AgentProfileResponse)
def update_agent_profile(
    profile_id: str,
    payload: AgentProfileUpdate,
    db: Session = Depends(get_db)
):
    """
    Updates an existing AI employee profile, including natural name, instructions (system prompt),
    default model, variant, task depth, and settings_json (execution contracts).
    """
    repo = AgentProfileRepository(db)
    existing = repo.get_by_id(profile_id)
    if not existing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent profile '{profile_id}' not found"
        )
    
    update_data = payload.model_dump(exclude_unset=True)
    updated = repo.update(existing.id, **update_data)
    return updated
