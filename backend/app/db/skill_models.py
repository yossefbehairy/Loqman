"""SkillSource table (kept out of models.py; parent must import this module so
``Base.metadata.create_all`` creates it, and bump the schema version if it tracks one).

Stores only the user's registration: a name and the canonical directory path.
Skill content and credentials are never persisted.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, String, Text

from backend.app.db.session import Base


class SkillSource(Base):
    __tablename__ = "skill_sources"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name = Column(String(255), nullable=False)
    path = Column(Text, nullable=False, unique=True)  # canonical realpath chosen by the user
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None), nullable=False)
