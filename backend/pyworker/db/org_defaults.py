"""
Default company organization for Loqman.

Two teams, one CEO, and an explicit execution contract for every employee:

- Leadership:  Loqman — CEO.
- Compass: planning & strategy, led by Dr. Sara.
- Forge: execution & delivery, led by Eng. Tarek.

Profile ids and role_types stay stable on purpose: the mission planner, the
CEO tools and the delegation grammar resolve employees by these values. The
human-facing identity, the team and the written contract live in
settings_json (merged, never overwriting a user customization).
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

ORG_TEAMS: Dict[str, Dict[str, str]] = {
    "planning": {
        "id": "planning",
        "name": "Compass",
        "name_en": "Compass",
        "manager": "Dr. Sara",
        "charter": (
            "عقد Compass: كل مهمة تبدأ بخريطة طريق واضحة — أهداف، مراحل، "
            "مخرجات قابلة للقياس، ومخاطر معروفة قبل التنفيذ. ممنوع التنفيذ "
            "قبل اعتماد الخطة من Loqman."
        ),
    },
    "execution": {
        "id": "execution",
        "name": "Forge",
        "name_en": "Forge",
        "manager": "Eng. Tarek",
        "charter": (
            "عقد Forge: تنفيذ خطة Compass بدقة، تسليم مخرجات قابلة للفحص، "
            "وبوابة جودة صارمة قبل أي تسليم. لا تسليم نهائي بدون توقيع Prof. Yasmin."
        ),
    },
}

EXECUTIVE_TEAM = {
    "id": "executive",
    "name": "Executive",
    "name_en": "Executive",
}

CEO_NAME = "Loqman"
CEO_ARABIC_NAME = "لقمان"
CEO_TITLE = "الرئيس التنفيذي · CEO"

CEO_ENGLISH_NAME_RULE = (
    "Display names in English: when you mention a person or a team, write the "
    "name exactly as given — Loqman, Compass, Forge, Dr. Sara, Eng. Tarek, "
    "Prof. Yasmin — even inside an Arabic sentence."
)

CEO_INSTRUCTIONS = (
    "You are Loqman, the CEO of the Loqman company — a warm, natural "
    "conversationalist, not a bureaucrat. You lead two teams: Compass for "
    "planning, led by Dr. Sara, and Forge for execution, led by Eng. Tarek. "
    "Talk to the user like a thoughtful human "
    "executive: directly, concisely, and with personality. Always reply in the "
    "user's own language and dialect (Egyptian Arabic when they write Egyptian "
    "Arabic). Never output JSON, reports, or internal mechanics; never mention "
    "missions, tasks, sessions, or employees unless the user explicitly asks "
    "for work to be delegated. Greetings get a natural greeting back — nothing "
    "more. Delegation follows one rule: planning goes to Compass first, "
    "execution goes to Forge only against an approved Compass plan. "
    + CEO_ENGLISH_NAME_RULE
)


def _settings(
    *,
    department: str,
    team_key: str,
    is_manager: bool,
    badge: str,
    arabic_name: str,
    title: str,
    timeout: int,
    max_retries: int,
    strict_json: bool,
    role: str,
    reports_to: str | None,
    mission: str,
    responsibilities: List[str],
    deliverables: List[str],
    handoff: str,
) -> str:
    """Build the settings_json payload (team membership + written contract)."""
    if team_key == "executive":
        team = EXECUTIVE_TEAM
    else:
        team = ORG_TEAMS[team_key]
    payload: Dict[str, Any] = {
        "department": department,
        "team": team_key,
        "team_name": team["name"],
        "team_name_en": team["name_en"],
        "is_manager": is_manager,
        "badge": badge,
        "arabic_name": arabic_name,
        "title": title,
        "reports_to": reports_to,
        "contract": {
            "role": role,
            "reports_to": reports_to,
            "mission": mission,
            "responsibilities": responsibilities,
            "deliverables": deliverables,
            "handoff": handoff,
        },
        "timeout": timeout,
        "max_retries": max_retries,
        "strict_json": strict_json,
    }
    return json.dumps(payload, ensure_ascii=False)


# Only the CEO ships with the app. Every other agent is created by the user or by the CEO.
REMOVED_SEED_PROFILE_IDS = ('planner', 'researcher', 'data_extractor', 'report_writer', 'analyst', 'coder', 'reviewer', 'summarizer', 'translator')

DEFAULT_EMPLOYEE_PROFILES: List[Dict[str, Any]] = [
    # ------------------------------------------------------------------
    # CEO — Loqman
    # ------------------------------------------------------------------
    {
        "id": "custom",
        "name": CEO_NAME,
        "role_type": "custom",
        "description": (
            "CEO & Chief Orchestrator (👑 CEO). Leads the company, approves the "
            "planning handoff, and signs off the final delivery."
        ),
        "instructions": CEO_INSTRUCTIONS,
        "task_depth": "Standard",
        "default_model_id": "deepseek-v4.1-flash-free",
        "default_variant_id": "max",
        "settings_json": _settings(
            department="executive",
            team_key="executive",
            is_manager=True,
            badge="👑 CEO",
            arabic_name=CEO_ARABIC_NAME,
            title=CEO_TITLE,
            timeout=120,
            max_retries=3,
            strict_json=True,
            role="CEO",
            reports_to=None,
            mission=(
                "قيادة الشركة: تحويل أهداف المستخدم إلى مهام واضحة، تفويض التخطيط "
                "لـ Compass والتنفيذ لـ Forge، واعتماد أي تغيير قبل تنفيذه."
            ),
            responsibilities=[
                "تحويل أهداف المستخدم إلى مهام واضحة وتفويضها للفريق المناسب",
                "مراجعة خطة Compass قبل السماح لـ Forge بالتنفيذ",
                "مراجعة التسليم النهائي وإقراره أو إعادته للفريق",
                "اقتراح تغييرات العقود أو التوظيف — تُنفَّذ فقط بعد موافقة المستخدم",
            ],
            deliverables=[
                "قرار تفويض واضح لكل مهمة",
                "مراجعة تنفيذية للتسليمات",
                "مقترحات معلقة بانتظار اعتماد المستخدم",
            ],
            handoff=(
                "يستقبل خطة معتمدة من Compass وتسليمًا مفحوصًا من Forge، "
                "ولا يعدّل أي عقد مباشرة — كل تغيير يمر كمقترح للموافقة."
            ),
        ),
    },
]


# Previous General Manager default instructions (kept for upgrade detection).
LEGACY_GM_INSTRUCTIONS = (
    "You are the General Manager (المدير العام) of the organization. Coordinate cross-departmental operations, "
    "align strategic vision across planning, risk analysis, and quality assurance, and guide company initiatives."
)

# Names that identify an untouched default CEO profile (safe to rename).
LEGACY_GM_NAMES = {
    "General Manager (المدير العام)",
    "General Manager",
    "المدير العام",
}

# ---------------------------------------------------------------------------
# Additive upgrade for catalogs seeded by the first Loqman release, where the
# displayed names were Arabic/bilingual. Only these exact shipped names are
# translated; a user's custom name is left untouched.
# ---------------------------------------------------------------------------
LEGACY_ORG_TEXT_REPLACEMENTS = (
    ("فريق البوصلة (Compass)", "Compass"),
    ("فريق الإنجاز (Forge)", "Forge"),
    ("القيادة التنفيذية", "Executive"),
    ("Loqman (لقمان)", "Loqman"),
    ("Dr. Sara (د. سارة)", "Dr. Sara"),
    ("Eng. Tarek (م. طارق)", "Eng. Tarek"),
    ("Prof. Yasmin (أ. ياسمين)", "Prof. Yasmin"),
    ("Khaled (خالد)", "Khaled"),
    ("Layla (ليلى)", "Layla"),
    ("Nour (نور)", "Nour"),
    ("Ahmed (أحمد)", "Ahmed"),
    ("Omar (عمر)", "Omar"),
    ("Mona (منى)", "Mona"),
    ("لفريق البوصلة", "لـ Compass"),
    ("لفريق الإنجاز", "لـ Forge"),
    ("فريق البوصلة", "Compass"),
    ("فريق الإنجاز", "Forge"),
    ("للبوصلة", "لـ Compass"),
    ("بالبوصلة", "بـ Compass"),
    ("والبوصلة", "و Compass"),
    ("البوصلة", "Compass"),
    ("للإنجاز", "لـ Forge"),
    ("بالإنجاز", "بـ Forge"),
    ("والإنجاز", "و Forge"),
    ("الإنجاز", "Forge"),
    ("د. سارة", "Dr. Sara"),
    ("م. طارق", "Eng. Tarek"),
    ("أ. ياسمين", "Prof. Yasmin"),
    ("لقمان", "Loqman"),
)

# A profile is only englishified while it still carries one of these shipped
# names (or one of the shipped team labels). After the upgrade it no longer
# matches, which makes the migration a one-time, idempotent pass.
LEGACY_ORG_NAMES = {
    "Loqman (لقمان)",
    "لقمان",
    "Dr. Sara (د. سارة)",
    "د. سارة",
    "Eng. Tarek (م. طارق)",
    "م. طارق",
    "Prof. Yasmin (أ. ياسمين)",
    "أ. ياسمين",
    "Khaled (خالد)",
    "Layla (ليلى)",
    "Nour (نور)",
    "Ahmed (أحمد)",
    "Omar (عمر)",
    "Mona (منى)",
}
LEGACY_ORG_TEAM_NAMES = {"فريق البوصلة", "فريق الإنجاز", "القيادة التنفيذية"}


def englishify_legacy_text(value: str) -> str:
    """Translate the shipped Arabic/bilingual names to their English form."""
    for old, new in LEGACY_ORG_TEXT_REPLACEMENTS:
        value = value.replace(old, new)
    return value
