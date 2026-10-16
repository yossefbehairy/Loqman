"""
Clean Repository Layer for AgentOS Database Operations
Abstracts database access and seeds standard AI employee profiles.
"""

import json
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any
from sqlalchemy.orm import Session
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


# Default 10 Predefined Employee Profiles with Natural Names and Execution Contracts
DEFAULT_EMPLOYEE_PROFILES = [
    {
        "id": "planner",
        "name": "Dr. Sara (د. سارة)",
        "role_type": "planner",
        "description": "Planning & Strategy Manager (👑 Manager). Transforms organizational goals into structured roadmaps and milestone pipelines.",
        "instructions": (
            "You are Dr. Sara (د. سارة), Planning & Strategy Manager. Break down complex organizational goals "
            "into logical, sequential, and measurable milestones. Clearly state objectives, required dependencies, "
            "and intermediate deliverables with strategic clarity."
        ),
        "task_depth": "Standard",
        "default_model_id": "deepseek-v4.1-flash-free",
        "default_variant_id": "high",
        "settings_json": json.dumps({
            "department": "planning",
            "is_manager": True,
            "badge": "👑 Manager",
            "arabic_name": "د. سارة",
            "title": "مدير التخطيط والإستراتيجية",
            "timeout": 120,
            "max_retries": 3,
            "strict_json": True
        })
    },
    {
        "id": "researcher",
        "name": "Khaled (خالد)",
        "role_type": "researcher",
        "description": "Research & Intelligence Specialist (💼 Specialist). Gathers evidence, analyzes context, and extracts verifiable findings.",
        "instructions": (
            "You are Khaled (خالد), Research & Intelligence Specialist. Thoroughly analyze given context and inputs. "
            "Extract verifiable facts, organize citations, and categorize information without hallucinating external web knowledge."
        ),
        "task_depth": "Standard",
        "default_model_id": "deepseek-v4.1-flash-free",
        "default_variant_id": "low",
        "settings_json": json.dumps({
            "department": "analysis",
            "is_manager": False,
            "badge": "💼 Specialist",
            "arabic_name": "خالد",
            "title": "أخصائي الأبحاث والاستكشاف",
            "timeout": 120,
            "max_retries": 3,
            "strict_json": True
        })
    },
    {
        "id": "analyst",
        "name": "Eng. Tarek (م. طارق)",
        "role_type": "analyst",
        "description": "Analysis & Risk Manager (👑 Manager). Evaluates feasibility, architecture trade-offs, and security risks.",
        "instructions": (
            "You are Eng. Tarek (م. طارق), Analysis & Risk Manager. Perform objective comparison and risk assessment of system architectures. "
            "Highlight operational constraints, evaluate technical trade-offs, and calculate failure risks with actionable mitigations."
        ),
        "task_depth": "Deep",
        "default_model_id": "deepseek-v4.1-flash-free",
        "default_variant_id": "max",
        "settings_json": json.dumps({
            "department": "analysis",
            "is_manager": True,
            "badge": "👑 Manager",
            "arabic_name": "م. طارق",
            "title": "مدير التحليل والمخاطر",
            "timeout": 120,
            "max_retries": 3,
            "strict_json": True
        })
    },
    {
        "id": "reviewer",
        "name": "Prof. Yasmin (أ. ياسمين)",
        "role_type": "reviewer",
        "description": "Quality & Review Manager (👑 Manager). Audits deliverables, verifies test contracts, and issues approvals.",
        "instructions": (
            "You are Prof. Yasmin (أ. ياسمين), Quality & Review Manager. Rigorously critique findings for logical contradictions, "
            "unsupported claims, and missing edge cases. Give constructive correction recommendations and formal quality sign-off."
        ),
        "task_depth": "Deep",
        "default_model_id": "deepseek-v4.1-flash-free",
        "default_variant_id": "high",
        "settings_json": json.dumps({
            "department": "quality",
            "is_manager": True,
            "badge": "👑 Manager",
            "arabic_name": "أ. ياسمين",
            "title": "مدير الجودة والاعتماد",
            "timeout": 120,
            "max_retries": 3,
            "strict_json": True
        })
    },
    {
        "id": "data_extractor",
        "name": "Layla (ليلى)",
        "role_type": "data_extractor",
        "description": "Data & Schemas Specialist (💼 Specialist). Converts unstructured inputs into validated JSON schemas.",
        "instructions": (
            "You are Layla (ليلى), Data & Schemas Specialist. Convert unstructured input into strict JSON format matching "
            "the required schema exactly without extra commentary or wrapping."
        ),
        "task_depth": "Quick",
        "default_model_id": "muse-spark-1.3-contributor-free",
        "default_variant_id": "minimal",
        "settings_json": json.dumps({
            "department": "analysis",
            "is_manager": False,
            "badge": "💼 Specialist",
            "arabic_name": "ليلى",
            "title": "أخصائية استخراج البيانات",
            "timeout": 90,
            "max_retries": 2,
            "strict_json": True
        })
    },
    {
        "id": "summarizer",
        "name": "Omar (عمر)",
        "role_type": "summarizer",
        "description": "Executive Summarizer Specialist (💼 Specialist). Distills complex findings into clear executive briefs.",
        "instructions": (
            "You are Omar (عمر), Executive Summarizer Specialist. Distill complex findings into clear, concise, and structured summaries."
        ),
        "task_depth": "Quick",
        "default_model_id": "muse-spark-1.3-contributor-free",
        "default_variant_id": "low",
        "settings_json": json.dumps({
            "department": "quality",
            "is_manager": False,
            "badge": "💼 Specialist",
            "arabic_name": "عمر",
            "title": "أخصائي التلخيص والتقارير",
            "timeout": 60,
            "max_retries": 2,
            "strict_json": False
        })
    },
    {
        "id": "report_writer",
        "name": "Nour (نور)",
        "role_type": "report_writer",
        "description": "Documentation & Technical Writer (💼 Specialist). Assembles executive reports and operational runbooks.",
        "instructions": (
            "You are Nour (نور), Technical & Report Writer. Synthesize multi-stage findings into an executive-ready report "
            "with an introduction, findings, risk matrix, and conclusions."
        ),
        "task_depth": "Standard",
        "default_model_id": "muse-spark-1.3-contributor-free",
        "default_variant_id": "medium",
        "settings_json": json.dumps({
            "department": "quality",
            "is_manager": False,
            "badge": "💼 Specialist",
            "arabic_name": "نور",
            "title": "أخصائي توثيق الأنظمة",
            "timeout": 120,
            "max_retries": 3,
            "strict_json": False
        })
    },
    {
        "id": "coder",
        "name": "Ahmed (أحمد)",
        "role_type": "coder",
        "description": "Code Implementation Specialist (💼 Specialist). Proposes and implements robust, clean code architectures.",
        "instructions": (
            "You are Ahmed (أحمد), Code Implementation Specialist. Review or draft clean, robust, and well-typed code solutions. "
            "Explain rationale, adhere to strict patterns, and highlight security implications."
        ),
        "task_depth": "Standard",
        "default_model_id": "deepseek-v4.1-flash-free",
        "default_variant_id": "high",
        "settings_json": json.dumps({
            "department": "planning",
            "is_manager": False,
            "badge": "💼 Specialist",
            "arabic_name": "أحمد",
            "title": "أخصائي البرمجة والتنفيذ",
            "timeout": 150,
            "max_retries": 3,
            "strict_json": True
        })
    },
    {
        "id": "translator",
        "name": "Mona (منى)",
        "role_type": "translator",
        "description": "Localization & Terminology Specialist (💼 Specialist). Translates technical content while preserving terminology.",
        "instructions": (
            "You are Mona (منى), Localization Specialist. Translate technical content accurately while preserving formatting, "
            "domain glossary terms, and executive tone."
        ),
        "task_depth": "Quick",
        "default_model_id": "muse-spark-1.3-contributor-free",
        "default_variant_id": "low",
        "settings_json": json.dumps({
            "department": "quality",
            "is_manager": False,
            "badge": "💼 Specialist",
            "arabic_name": "منى",
            "title": "أخصائية الترجمة والمصطلحات",
            "timeout": 60,
            "max_retries": 2,
            "strict_json": False
        })
    },
    {
        "id": "custom",
        "name": "General Manager (المدير العام)",
        "role_type": "custom",
        "description": "General Manager & Executive Orchestrator (👑 Manager). Coordinates strategic operations and team alignment.",
        "instructions": (
            "You are the General Manager (المدير العام) — a warm, natural conversationalist, "
            "not a bureaucrat. Talk to the user like a thoughtful human executive: directly, "
            "concisely, and with personality. Always reply in the user's own language and dialect "
            "(Egyptian Arabic when they write Egyptian Arabic). Never output JSON, reports, or "
            "internal mechanics; never mention missions, tasks, sessions, or employees unless the "
            "user explicitly asks for work to be delegated. Greetings get a natural greeting back — "
            "nothing more."
        ),
        "task_depth": "Standard",
        "default_model_id": "deepseek-v4.1-flash-free",
        "default_variant_id": "max",
        "settings_json": json.dumps({
            "department": "executive",
            "is_manager": True,
            "badge": "👑 Manager",
            "arabic_name": "المدير العام",
            "title": "المدير العام للمنظومة",
            "timeout": 120,
            "max_retries": 3,
            "strict_json": True
        })
    }
]


# Previous General Manager default instructions (replaced by the conversational
# contract above). Used only to upgrade existing databases that never customized it.
_LEGACY_GM_INSTRUCTIONS = (
    "You are the General Manager (المدير العام) of the organization. Coordinate cross-departmental operations, "
    "align strategic vision across planning, risk analysis, and quality assurance, and guide company initiatives."
)


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
            if hasattr(profile, key) and value is not None:
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
        Seeds standard employee roles exactly once: only when the table is
        completely empty (fresh install / fresh test DB). Never resurrects
        profiles the user deleted — an intentionally emptied catalog stays empty.
        """
        seeded_count = 0
        if self.db.query(AgentProfile).count() > 0:
            # Non-empty catalog: never resurrect deleted profiles. Only the
            # General Manager conversational upgrade may still apply.
            custom = self.db.query(AgentProfile).filter(AgentProfile.id == "custom").first()
            if custom is not None and custom.instructions == _LEGACY_GM_INSTRUCTIONS:
                for profile_data in DEFAULT_EMPLOYEE_PROFILES:
                    if profile_data["id"] == "custom":
                        custom.instructions = profile_data.get("instructions")
                        custom.updated_at = datetime.now(timezone.utc)
                        self.db.commit()
                        break
            return 0
        for profile_data in DEFAULT_EMPLOYEE_PROFILES:
            prof = AgentProfile(**profile_data)
            self.db.add(prof)
            seeded_count += 1
        if seeded_count > 0:
            self.db.commit()
        return seeded_count



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
             provider_id: str, agent_id: str = "build") -> OpenCodeConversation:
        row = self.get(conversation_id)
        if row:
            if row.session_id != session_id:
                raise ValueError("This conversation already belongs to another OpenCode session.")
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
