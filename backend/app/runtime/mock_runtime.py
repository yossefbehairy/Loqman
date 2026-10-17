"""
MockRuntime Implementation
Provides deterministic, zero-cost AI responses for testing and offline demo fallback.
"""

import re
import time
import json
from typing import List, Optional
from backend.app.runtime.base import (
    AgentRuntime,
    RuntimeHealth,
    ModelCatalogEntry,
    ModelCost,
    ExecuteAgentTask,
    AgentResult,
)

# Team-creation phrasing (additive routing for the CEO tools mock branch).
# Listing questions ("who is on the team?") must NOT match.
_TEAM_CREATION_AR = (
    "اعمل فريق", "أعمل فريق", "اعمللي فريق", "انشئ فريق", "أنشئ فريق",
    "انشأ فريق", "كوّن فريق", "كون فريق", "شكّل فريق", "شكل فريق",
    "أسس فريق", "فريق جديد", "فريق مراجعة أمنية", "فريق أمني", "فريق الأمن",
)
# English: a creation verb shortly before the noun "team"
# ("create a security team"); "who is on the team?" never matches.
_EN_TEAM_CREATION_RE = re.compile(
    r"\b(create|build|form|make|start|assemble|set\s?up)\b[^.?!]{0,40}\bteam\b"
)


def _looks_like_team_creation(qtext: str) -> bool:
    if any(marker in qtext for marker in _TEAM_CREATION_AR):
        return True
    return bool(_EN_TEAM_CREATION_RE.search(qtext.lower()))


class MockRuntime:
    """
    Offline deterministic runtime for tests and offline University presentations.
    """
    def __init__(self, default_model_id: str = "mock-muse-spark-free"):
        self.default_model_id = default_model_id
        self._models = [
            ModelCatalogEntry(
                id="deepseek-v4.1-flash-free",
                name="DeepSeek V4.1 Flash Free (Mock)",
                provider_id="opencode",
                cost=ModelCost(input=0.0, output=0.0),
                variants=["low", "high", "max"],
                is_free=True,
                status="active"
            ),
            ModelCatalogEntry(
                id="deepseek-ai/deepseek-v4.1-flash",
                name="DeepSeek V4.1 Flash (Mock)",
                provider_id="nvidia",
                cost=ModelCost(input=0.0, output=0.0),
                variants=["none", "low", "high", "max"],
                is_free=True,
                status="active"
            ),
            ModelCatalogEntry(
                id="deepseek-v4-flash",
                name="DeepSeek V4 Flash via AgentRouter (Mock)",
                provider_id="agentrouter",
                cost=ModelCost(input=0.0, output=0.0),
                variants=["low", "high", "max"],
                is_free=True,
                status="active"
            ),
            ModelCatalogEntry(
                id="muse-spark-1.3-contributor-free",
                name="Muse Spark 1.3 Free (Mock)",
                provider_id="opencode",
                cost=ModelCost(input=0.0, output=0.0),
                variants=["minimal", "low", "medium", "high", "xhigh"],
                is_free=True,
                status="active"
            ),
            ModelCatalogEntry(
                id="nemotron-3.5-super-vl-free",
                name="Nemotron 3.5 Super VL Free (Mock)",
                provider_id="opencode",
                cost=ModelCost(input=0.0, output=0.0),
                variants=["none", "minimal", "low", "medium", "high", "xhigh"],
                is_free=True,
                status="active"
            ),
            ModelCatalogEntry(
                id="cat-jam-free",
                name="Cat Jam Free (Mock)",
                provider_id="opencode",
                cost=ModelCost(input=0.0, output=0.0),
                variants=["low", "medium", "high", "xhigh", "max"],
                is_free=True,
                status="active"
            ),
            ModelCatalogEntry(
                id="step-5-preview-free",
                name="Step 5 Preview Free (Mock)",
                provider_id="opencode",
                cost=ModelCost(input=0.0, output=0.0),
                variants=["low", "medium", "high"],
                is_free=True,
                status="active"
            )
        ]

    async def check_health(self) -> RuntimeHealth:
        return RuntimeHealth(
            status="ok",
            service_url="mock://localhost",
            default_model_status="available",
            models_count=len(self._models),
            free_models_count=len(self._models)
        )

    async def list_available_models(self, free_only: bool = True) -> List[ModelCatalogEntry]:
        if free_only:
            return [m for m in self._models if m.is_free]
        return self._models

    async def execute_task(self, task: ExecuteAgentTask) -> AgentResult:
        t0 = time.time()
        prompt_lower = task.prompt.lower()

        import re as _re

        # 0. CEO self-reasoning loop ("ceo tools" marker) — deterministic tool protocol.
        # Checked FIRST so loop prompts never fall into older branches.
        if "ceo tools" in prompt_lower:
            if "stubborn_loop" in prompt_lower:
                mock_envelope = {"tool_call": {"name": "list_missions", "args": {"limit": 5}}}
            elif "[tool_observation]" in task.prompt:
                tail = task.prompt.rsplit("[tool_observation]", 1)[-1].strip()[:600]
                mock_envelope = {"final": f"Based on live company data: {tail}"}
            elif "glitch_probe" in prompt_lower:
                mock_envelope = {"tool_call": {"name": "no_such_tool", "args": {}}}
            else:
                # Route on the USER QUESTION only (never the tool catalog text).
                _q = _re.search(r"User question:\s*(.+)\s*$", task.prompt, _re.DOTALL)
                _qtext = _q.group(1) if _q else task.prompt
                q = _qtext.lower()
                _mission_words = ("mission", "مهم", "مهام", "مهمة", "مهام")
                ses_match = _re.search(r"ses_[A-Za-z0-9_-]+", _qtext)
                if (("detail" in q or "تفاصيل" in q) and any(w in q for w in _mission_words)
                        and ses_match):
                    mock_envelope = {"tool_call": {"name": "get_mission",
                                                   "args": {"mission_id": ses_match.group(0)}}}
                elif ("تعديل" in _qtext or "عدل" in _qtext or "غير" in _qtext
                        or "سرع" in _qtext or "speed" in q or "update" in q):
                    mock_envelope = {"tool_call": {"name": "propose_update_contract",
                                                   "args": {"profile": "planner",
                                                            "changes": {"default_variant_id": "low"}}}}
                elif ("موظف جديد" in _qtext or "أضف موظف" in _qtext
                        or "new employee" in q or "add employee" in q):
                    mock_envelope = {"tool_call": {"name": "propose_new_profile",
                                                   "args": {"name": "Nadia (نادية)",
                                                            "role": "researcher",
                                                            "instructions": "You are Nadia, research specialist."}}}
                elif _looks_like_team_creation(_qtext):
                    mock_envelope = {"tool_call": {"name": "propose_new_team",
                                                   "args": {
                                                       "name": "Security Review Team (فريق المراجعة الأمنية)",
                                                       "members": [
                                                           {"name": "Hassan (حسن)",
                                                            "role_type": "security_auditor",
                                                            "description": "Lead security auditor.",
                                                            "instructions": "You are Hassan (حسن), Lead Security Auditor. "
                                                                            "Audit artifacts for vulnerabilities and return STRICT JSON.",
                                                            "default_variant_id": "high"},
                                                           {"name": "Farida (فريدة)",
                                                            "role_type": "reviewer",
                                                            "description": "Compliance and quality reviewer.",
                                                            "instructions": "You are Farida (فريدة), Compliance Reviewer. "
                                                                            "Cross-check audit findings against policy and return STRICT JSON.",
                                                            "default_variant_id": "high"},
                                                           {"name": "Kareem (كريم)",
                                                            "role_type": "analyst",
                                                            "description": "Risk analyst for audit findings.",
                                                            "instructions": "You are Kareem (كريم), Risk Analyst. "
                                                                            "Quantify and prioritize audit risks and return STRICT JSON.",
                                                            "default_variant_id": "max"},
                                                       ],
                                                   }}}
                elif (any(w in q for w in _mission_words)):
                    mock_envelope = {"tool_call": {"name": "list_missions", "args": {"limit": 5}}}
                elif ("profile" in q or "team" in q or "فريق" in _qtext
                        or "موظف" in _qtext or "employee" in q):
                    mock_envelope = {"tool_call": {"name": "list_profiles", "args": {}}}
                else:
                    mock_envelope = {"final": "أهلاً بك! اسألني عن المهام أو الفريق وسأجيبك من البيانات الحية."}
            duration_ms = int((time.time() - t0) * 1000) or 5
            return AgentResult(
                text=json.dumps(mock_envelope, ensure_ascii=False),
                reasoning="[Mock CEO tools branch]",
                model_id=task.model_id,
                variant_id=task.variant or "default",
                cost=0.0,
                tokens={"input": 45, "output": 30, "reasoning": 10},
                duration_ms=duration_ms,
                session_id="mock-session-id"
            )
        # 0b. CEO planning / synthesis (orchestrator prompts) — deterministic valid JSON.
        if "ceo planner" in prompt_lower or "structured execution plan" in prompt_lower:
            mock_json = {
                "objective": task.prompt[:300],
                "tasks": [
                    {
                        "key": "planning",
                        "employee_role": "planner",
                        "title": "Implementation plan",
                        "instructions": "Produce a structured implementation plan as strict JSON.",
                        "depends_on": [],
                        "expected_output": "JSON object with steps",
                    },
                    {
                        "key": "quality_review",
                        "employee_role": "reviewer",
                        "title": "Quality review of the plan",
                        "instructions": "Review the planning output for correctness and gaps. Strict JSON only.",
                        "depends_on": ["planning"],
                        "expected_output": "JSON object with verdict",
                    },
                ],
            }
        elif "synthesize the completed" in prompt_lower or "executive summary" in prompt_lower:
            mock_json = {
                "summary": "Mock synthesis: employee outputs were combined deterministically.",
                "completed": ["planning", "quality_review"],
                "failed": [],
                "unverified": ["planning", "quality_review"],
            }
        # 1. Check for interactive direct manager consultation
        elif "consultation" in prompt_lower:
            goal_match = _re.search(r"user (?:message|question/briefing):\s*(.+?)(?:\n|$)", task.prompt, _re.IGNORECASE)
            goal_text = goal_match.group(1).strip() if goal_match else "المشروع المطلوب"

            if "sara" in prompt_lower or "د. سارة" in task.prompt or "planner" in prompt_lower:
                mock_json = {
                    "reply": (
                        "أهلاً بك! بصفتي د. سارة مديرة التخطيط والإستراتيجية، قمت بتحليل الهدف وتفكيكه "
                        "إلى مسارات ومحطات تنفيذية واضحة. سنبدأ أولاً بتحديد المتطلبات المعمارية بدقة، "
                        "ثم وضع خارطة طريق متسلسلة تضمن توجيه المهام بفعالية لفرق التحليل والتطوير."
                    ),
                    "manager_id": "dr_sara",
                    "manager_name": "د. سارة",
                    "title": "مدير التخطيط والإستراتيجية",
                    "suggested_mission": {
                        "title": "خطة تفكيك الأهداف والمراحل التنفيذية",
                        "input_json": {
                            "goal": goal_text,
                            "department": "planning",
                            "phases": [
                                "المرحلة الأولى: التحليل المعماري وتحديد نطاق العمل",
                                "المرحلة الثانية: توزيع المهام البرمجية والتكامل",
                                "المرحلة الثالثة: الاعتماد والتسليم النهائي"
                            ]
                        }
                    }
                }
            elif "tarek" in prompt_lower or "م. طارق" in task.prompt or "analyst" in prompt_lower:
                mock_json = {
                    "reply": (
                        "مرحباً بك! بصفتي م. طارق مدير التحليل والمخاطر، قمت بدراسة الجدوى الفنية وتقييم "
                        "المخاطر المحتملة. نوصي بالتركيز على معالجة اختناقات الأداء ومزامنة العمليات وضمان "
                        "صمود النظام تحت الضغط العالي مع توفير آليات عزل واستعادة سريعة."
                    ),
                    "manager_id": "eng_tarek",
                    "manager_name": "م. طارق",
                    "title": "مدير التحليل والمخاطر",
                    "suggested_mission": {
                        "title": "دراسة الجدوى الفنية وتحليل المخاطر المعمارية",
                        "input_json": {
                            "goal": goal_text,
                            "department": "analysis",
                            "risk_focus": [
                                "أمان البيانات وتكامل واجهات API",
                                "اختناقات الأداء والعمليات المتزامنة",
                                "خطة الصمود والتعافي من الأعطال"
                            ]
                        }
                    }
                }
            elif "yasmin" in prompt_lower or "أ. ياسمين" in task.prompt or "reviewer" in prompt_lower:
                mock_json = {
                    "reply": (
                        "أهلاً بك! بصفتي أ. ياسمين مديرة الجودة والاعتماد، وضعت مصفوفة فحص الجودة وتأكيد المخرجات. "
                        "سنخضع كافة التسليمات لتدقيق منهجي يضمن مطابقة العقود البرمجية وسلامة مخططات JSON والتغطية "
                        "الشاملة لكافة الحالات الطرفية قبل إصدار الاعتماد النهائي."
                    ),
                    "manager_id": "prof_yasmin",
                    "manager_name": "أ. ياسمين",
                    "title": "مدير الجودة والاعتماد",
                    "suggested_mission": {
                        "title": "مصفوفة ضبط الجودة واعتماد مخرجات العمل",
                        "input_json": {
                            "goal": goal_text,
                            "department": "quality",
                            "quality_gates": [
                                "التحقق من صحة مخططات JSON",
                                "فحص تغطية الحالات الطرفية والحدود",
                                "الاعتماد والتقرير التنفيذي النهائي"
                            ]
                        }
                    }
                }
            else:
                mock_json = {
                    "reply": (
                        "مرحباً بك! بصفتي المدير العام للمنظومة، سأقود التنسيق الشامل لتنفيذ هذا الهدف "
                        "عبر توجيه إدارات التخطيط، والتحليل والمخاطر، والجودة لضمان تسليم متكامل وعالي الكفاءة."
                    ),
                    "manager_id": "general_manager",
                    "manager_name": "المدير العام",
                    "title": "المدير العام للمنظومة",
                    "suggested_mission": {
                        "title": "مهمة تنفيذية شاملة عبر الإدارات",
                        "input_json": {
                            "goal": goal_text,
                            "department": "executive",
                            "pipeline": [
                                "د. سارة (التخطيط والإستراتيجية)",
                                "م. طارق (التحليل والمخاطر)",
                                "أ. ياسمين (الجودة والاعتماد)"
                            ]
                        }
                    }
                }

        # 2. DAG workflow execution node simulation
        else:
            # Detect the CURRENT node role from the prompt header
            # ("You are the 'ANALYST' ...") — transcript mentions of teammates
            # must not override it (transcript always contains "planner").
            header_match = _re.search(r"you are the '([a-z_]+)'", prompt_lower)
            current_role = header_match.group(1) if header_match else ""

            # Simulated response matching Planner / Analyst / Reviewer patterns
            if current_role == "planner" or ("planner" in prompt_lower and not current_role):
                mock_json = {
                    "key_objective": "System Architecture & Staging Plan",
                    "plan_steps": [
                        "Define data models and entity relationship contracts",
                        "Configure DAG execution pipeline and topological order",
                        "Conduct integration smoke testing and audit verification"
                    ],
                    "team_message": "Team, I (Planner) kicked off with the architecture plan and staged steps for Analyst and Reviewer to build on."
                }
            elif current_role == "analyst" or ("analyst" in prompt_lower and not current_role):
                mock_json = {
                    "analysis": "Building on Planner's staged steps, analysis confirms feasible design with well-defined service boundaries.",
                    "identified_risks": [
                        "Concurrency race condition during heavy peak enrollment",
                        "Network timeout during external LLM token streaming"
                    ],
                    "feasibility_verdict": "High",
                    "team_message": "Thanks Planner for the staged steps — I (Analyst) audited them and flagged 2 operational risks for Reviewer."
                }
            elif current_role == "reviewer" or ("reviewer" in prompt_lower and not current_role):
                mock_json = {
                    "review_status": "APPROVED",
                    "critique": "Reviewed Planner's plan and Analyst's risk findings: structurally sound, assumptions grounded, risks have mitigations.",
                    "recommended_improvements": ["Add automated snapshot backups before bulk execution"],
                    "team_message": "Thanks Planner and Analyst — I (Reviewer) verified the plan against the findings and approved with one improvement."
                }
            else:
                mock_json = {
                    "summary": "Mock task execution succeeded.",
                    "actions": ["Verify inputs", "Process pipeline", "Return output"]
                }

        duration_ms = int((time.time() - t0) * 1000) or 5
        return AgentResult(
            text=json.dumps(mock_json),
            reasoning="[Mock Runtime Reasoning: Processed prompt deterministically]",
            model_id=task.model_id,
            variant_id=task.variant or "default",
            cost=0.0,
            tokens={"input": 45, "output": 30, "reasoning": 10},
            duration_ms=duration_ms,
            session_id="mock-session-id"
        )

    async def abort_task(self, session_id: str) -> None:
        pass
