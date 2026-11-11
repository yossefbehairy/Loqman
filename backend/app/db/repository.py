"""
Clean Repository Layer for AgentOS Database Operations
Abstracts database access and seeds standard AI employee profiles.
"""

import json
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any
from sqlalchemy.orm import Session
from backend.app.config import settings
from backend.app.db.models import (
    Workflow,
    AgentProfile,
    WorkflowExecution,
    NodeExecution,
    ExecutionEvent,
    Mission,
    MissionTask,
    OpenCodeConversation,
)
from backend.app.db.org_defaults import (
    DEFAULT_EMPLOYEE_PROFILES,
    CEO_ENGLISH_NAME_RULE,
    LEGACY_GM_INSTRUCTIONS,
    LEGACY_GM_NAMES,
    LEGACY_ORG_NAMES,
    LEGACY_ORG_TEAM_NAMES,
    englishify_legacy_text,
)

# Backwards-compatible alias (older modules/tests import the private name).
_LEGACY_GM_INSTRUCTIONS = LEGACY_GM_INSTRUCTIONS


class WorkflowRepository:
    def __init__(self, db: Session):
        self.db = db

    def get_by_id(self, workflow_id: str) -> Optional[Workflow]:
        return self.db.query(Workflow).filter(Workflow.id == workflow_id).first()

    def list_all(self, skip: int = 0, limit: int = 100) -> List[Workflow]:
        return self.db.query(Workflow).order_by(Workflow.updated_at.desc()).offset(skip).limit(limit).all()

    def create(self, name: str, description: Optional[str] = None, definition_json: str = "{}") -> Workflow:
        wf = Workflow(
            name=name,
            description=description,
            definition_json=definition_json,
            version=1
        )
        self.db.add(wf)
        self.db.commit()
        self.db.refresh(wf)
        return wf

    def update(
        self,
        workflow_id: str,
        name: Optional[str] = None,
        description: Optional[str] = None,
        definition_json: Optional[str] = None
    ) -> Optional[Workflow]:
        wf = self.get_by_id(workflow_id)
        if not wf:
            return None
        if name is not None:
            wf.name = name
        if description is not None:
            wf.description = description
        if definition_json is not None:
            wf.definition_json = definition_json
        wf.version += 1
        self.db.commit()
        self.db.refresh(wf)
        return wf

    def delete(self, workflow_id: str) -> bool:
        wf = self.get_by_id(workflow_id)
        if not wf:
            return False
        self.db.delete(wf)
        self.db.commit()
        return True


PROFILE_ALIASES = {
    "dr_sara": "planner",
    "eng_tarek": "analyst",
    "prof_yasmin": "reviewer",
    "general_manager": "custom",
    "loqman": "custom",
    "ceo": "custom",
    "ahmed": "coder",
    "khaled": "researcher",
    "layla": "data_extractor",
    "omar": "summarizer",
    "nour": "report_writer",
    "mona": "translator",
}


class AgentProfileRepository:
    def __init__(self, db: Session):
        self.db = db

    def get_by_id(self, profile_id: str) -> Optional[AgentProfile]:
        prof = self.db.query(AgentProfile).filter(AgentProfile.id == profile_id).first()
        if not prof and profile_id in PROFILE_ALIASES:
            prof = self.db.query(AgentProfile).filter(AgentProfile.id == PROFILE_ALIASES[profile_id]).first()
        return prof

    def list_all(self) -> List[AgentProfile]:
        return self.db.query(AgentProfile).order_by(AgentProfile.name.asc()).all()

    def create(self, data: Dict[str, Any]) -> AgentProfile:
        profile = AgentProfile(**data)
        self.db.add(profile)
        self.db.commit()
        self.db.refresh(profile)
        return profile

    def update(self, profile_id: str, **kwargs) -> Optional[AgentProfile]:
        profile = self.get_by_id(profile_id)
        if not profile:
            return None
        for key, value in kwargs.items():
            if hasattr(profile, key) and (value is not None or key == "engine"):
                setattr(profile, key, value)
        profile.updated_at = datetime.now(timezone.utc)
        self.db.commit()
        self.db.refresh(profile)
        return profile

    def update_profile(self, profile_id: str, data: Dict[str, Any]) -> Optional[AgentProfile]:
        """
        Contract update method accepting dictionary payload.
        """
        return self.update(profile_id, **data)

    def seed_defaults(self) -> int:
        """
        Seeds the Loqman company organization (CEO + two teams) exactly once:
        only when the table is completely empty (fresh install / fresh test DB).
        Never resurrects profiles the user deleted.

        Existing catalogs are upgraded in place, additively:
        - an untouched default CEO is renamed to Loqman and gets the new persona;
        - team membership + the written contract are merged into settings_json
          only when those keys are missing (any user customization wins).
        """
        existing = self.db.query(AgentProfile).all()
        if existing:
            self._upgrade_existing_org(existing)
            return 0
        for profile_data in DEFAULT_EMPLOYEE_PROFILES:
            self.db.add(AgentProfile(**profile_data))
        self.db.commit()
        return len(DEFAULT_EMPLOYEE_PROFILES)

    @staticmethod
    def _read_settings(profile: AgentProfile) -> Dict[str, Any]:
        try:
            data = json.loads(profile.settings_json or "{}")
        except Exception:
            data = {}
        return data if isinstance(data, dict) else {}

    @classmethod
    def _merge_settings(
        cls,
        profile: AgentProfile,
        default_settings: Dict[str, Any],
        force: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Additive settings merge: missing keys are filled, user values win."""
        current = cls._read_settings(profile)
        before = json.dumps(current, sort_keys=True, ensure_ascii=False)
        for key, value in default_settings.items():
            current.setdefault(key, value)
        if force:
            current.update(force)
        if json.dumps(current, sort_keys=True, ensure_ascii=False) == before:
            return False
        profile.settings_json = json.dumps(current, ensure_ascii=False)
        return True

    @classmethod
    def _englishify_settings(cls, value: Any) -> Any:
        """Recursively translate shipped Arabic names, preserving arabic_name."""
        if isinstance(value, str):
            return englishify_legacy_text(value)
        if isinstance(value, list):
            return [cls._englishify_settings(item) for item in value]
        if isinstance(value, dict):
            return {
                key: (item if key == "arabic_name" else cls._englishify_settings(item))
                for key, item in value.items()
            }
        return value

    @classmethod
    def _englishify_profile(cls, profile: AgentProfile) -> bool:
        """
        One-way upgrade for profiles seeded with the first Loqman release:
        translate only the shipped Arabic/bilingual names to English. Any
        custom name or contract text that does not contain a shipped name is
        left exactly as the user wrote it.
        """
        changed = False
        instructions = profile.instructions or ""
        if (
            profile.id == "custom"
            and instructions.startswith("You are Loqman")
            and "Display names in English" not in instructions
        ):
            profile.instructions = instructions.rstrip() + "\n\n" + CEO_ENGLISH_NAME_RULE
            changed = True
        current = cls._read_settings(profile)
        shipped_name = (profile.name or "").strip() in LEGACY_ORG_NAMES
        shipped_team = current.get("team_name") in LEGACY_ORG_TEAM_NAMES
        if not (shipped_name or shipped_team):
            return changed
        name = englishify_legacy_text(profile.name or "")
        if name != (profile.name or ""):
            profile.name = name
            changed = True
        upgraded_instructions = englishify_legacy_text(profile.instructions or "")
        if upgraded_instructions != (profile.instructions or ""):
            profile.instructions = upgraded_instructions
            changed = True
        upgraded = cls._englishify_settings(current)
        if upgraded != current:
            profile.settings_json = json.dumps(upgraded, ensure_ascii=False)
            changed = True
        return changed

    def _upgrade_existing_org(self, profiles: List[AgentProfile]) -> None:
        """Additive migration for catalogs seeded before the Loqman org."""
        by_id = {p.id: p for p in profiles}
        changed = False

        for profile_data in DEFAULT_EMPLOYEE_PROFILES:
            profile = by_id.get(profile_data["id"])
            if profile is None:
                continue
            default_settings = json.loads(profile_data["settings_json"])
            force: Dict[str, Any] = {}

            if profile_data["id"] == "custom":
                current = self._read_settings(profile)
                instructions = (profile.instructions or "").strip()
                untouched_ceo = (
                    (profile.name or "").strip() in LEGACY_GM_NAMES
                    or current.get("arabic_name") in (None, "", "المدير العام")
                    or instructions == LEGACY_GM_INSTRUCTIONS.strip()
                    or instructions.startswith("You are the General Manager")
                )
                if untouched_ceo:
                    profile.name = profile_data["name"]
                    profile.description = profile_data["description"]
                    profile.instructions = profile_data["instructions"]
                    force = {
                        "department": default_settings["department"],
                        "badge": default_settings["badge"],
                        "arabic_name": default_settings["arabic_name"],
                        "title": default_settings["title"],
                    }
                    changed = True

            if self._merge_settings(profile, default_settings, force):
                profile.updated_at = datetime.now(timezone.utc)
                changed = True

            if self._englishify_profile(profile):
                profile.updated_at = datetime.now(timezone.utc)
                changed = True

        if changed:
            self.db.commit()



class ExecutionRepository:
    def __init__(self, db: Session):
        self.db = db

    def create_execution(
        self,
        workflow_id: str,
        input_json: Optional[str] = None,
        workflow_snapshot_json: Optional[str] = None
    ) -> WorkflowExecution:
        now = datetime.now(timezone.utc)
        exec_obj = WorkflowExecution(
            workflow_id=workflow_id,
            status="pending",
            input_json=input_json,
            workflow_snapshot_json=workflow_snapshot_json,
            started_at=now
        )
        self.db.add(exec_obj)
        self.db.commit()
        self.db.refresh(exec_obj)
        return exec_obj

    def get_execution(self, execution_id: str) -> Optional[WorkflowExecution]:
        return self.db.query(WorkflowExecution).filter(WorkflowExecution.id == execution_id).first()

    def list_executions(self, skip: int = 0, limit: int = 50) -> List[WorkflowExecution]:
        return (
            self.db.query(WorkflowExecution)
            .order_by(WorkflowExecution.started_at.desc())
            .offset(skip)
            .limit(limit)
            .all()
        )

    def record_node_execution(self, data: Dict[str, Any]) -> NodeExecution:
        node_exec = NodeExecution(**data)
        self.db.add(node_exec)
        self.db.commit()
        self.db.refresh(node_exec)
        return node_exec

    def record_event(
        self,
        execution_id: str,
        event_type: str,
        event_json: str = "{}",
        node_execution_id: Optional[str] = None
    ) -> ExecutionEvent:
        event = ExecutionEvent(
            execution_id=execution_id,
            node_execution_id=node_execution_id,
            event_type=event_type,
            event_json=event_json
        )
        self.db.add(event)
        self.db.commit()
        self.db.refresh(event)
        return event


class MissionRepository:
    """Persistence for CEO missions + tasks (duplicate-safe)."""

    def __init__(self, db: Session):
        self.db = db

    def create_mission(
        self,
        objective: str,
        title: Optional[str] = None,
        model_id: Optional[str] = None,
        variant: Optional[str] = None,
        idempotency_key: Optional[str] = None,
    ) -> Mission:
        if idempotency_key:
            existing = (
                self.db.query(Mission).filter(Mission.idempotency_key == idempotency_key).first()
            )
            if existing:
                return existing
        m = Mission(
            title=title or objective[:80],
            objective=objective,
            status="queued",
            model_id=model_id,
            variant=variant,
            idempotency_key=idempotency_key,
        )
        self.db.add(m)
        self.db.commit()
        self.db.refresh(m)
        return m

    def get(self, mission_id: str) -> Optional[Mission]:
        return self.db.query(Mission).filter(Mission.id == mission_id).first()

    def list(self, skip: int = 0, limit: int = 50) -> List[Mission]:
        return (
            self.db.query(Mission)
            .order_by(Mission.created_at.desc())
            .offset(skip)
            .limit(limit)
            .all()
        )

    def create_task(
        self,
        mission_id: str,
        task_key: str,
        employee_role: str,
        title: str,
        instructions: str,
        input_context: Optional[str] = None,
        depends_on: Optional[List[str]] = None,
        agent_profile_id: Optional[str] = None,
        model_id: Optional[str] = None,
        variant: Optional[str] = None,
    ) -> MissionTask:
        t = MissionTask(
            mission_id=mission_id,
            task_key=task_key,
            employee_role=employee_role,
            agent_profile_id=agent_profile_id,
            title=title,
            instructions=instructions,
            input_context=input_context,
            depends_on_json=json.dumps(depends_on or []),
            status="queued",
            model_id=model_id,
            variant=variant,
            attempt_count=0,
        )
        self.db.add(t)
        self.db.commit()
        self.db.refresh(t)
        return t


class OpenCodeConversationRepository:
    def __init__(self, db: Session):
        self.db = db

    def get(self, conversation_id: str) -> Optional[OpenCodeConversation]:
        return self.db.query(OpenCodeConversation).filter(
            OpenCodeConversation.conversation_id == conversation_id
        ).first()

    def save(self, conversation_id: str, session_id: str, model_id: str,
             provider_id: str, agent_id: str = settings.CHAT_AGENT_ID) -> OpenCodeConversation:
        allowed_agents = {settings.CHAT_AGENT_ID, settings.WORK_AGENT_ID}
        if agent_id not in allowed_agents:
            raise ValueError(
                f"OpenCode agent '{agent_id}' is not allowed. Allowed: {sorted(allowed_agents)}."
            )
        row = self.get(conversation_id)
        if row:
            agent_changed = row.agent_id != agent_id
            if row.session_id != session_id and not agent_changed:
                raise ValueError("This conversation already belongs to another OpenCode session.")
            if agent_changed:
                # Security migration: a stored conversation pinned to an
                # unrestricted agent (legacy "build") is re-pointed at the
                # locked agent instead of silently reusing the unsafe session.
                row.session_id = session_id
            row.model_id = model_id
            row.provider_id = provider_id
            row.agent_id = agent_id
        else:
            row = OpenCodeConversation(
                conversation_id=conversation_id,
                session_id=session_id,
                model_id=model_id,
                provider_id=provider_id,
                agent_id=agent_id,
            )
            self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        return row
