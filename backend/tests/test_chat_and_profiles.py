"""
Tests for Agent Profile Updates and Direct Manager Consultation (R1, R2, R3)
Verifies:
- PUT /api/agent-profiles/{id} updating instructions, models, and execution contracts
- 404 handling for invalid profile requests
- POST /api/chat returning structured Arabic persona guidance for Dr. Sara, Eng. Tarek, Prof. Yasmin, and General Manager
- POST /api/managers/{id}/consult endpoint
- Seeded natural Arabic personas in repository
"""

import json
import pytest
from backend.app.db.repository import AgentProfileRepository


def test_get_agent_profile_by_id_and_alias(client):
    """
    Verifies GET /api/agent-profiles/{id} retrieves profiles by primary key or alias.
    """
    # By primary ID
    res1 = client.get("/api/agent-profiles/planner")
    assert res1.status_code == 200
    p1 = res1.json()
    assert p1["id"] == "planner"
    assert "د. سارة" in p1["name"]

    # By alias
    res2 = client.get("/api/agent-profiles/dr_sara")
    assert res2.status_code == 200
    p2 = res2.json()
    assert p2["id"] == "planner"
    assert "د. سارة" in p2["name"]

    # 404 for non-existent profile
    res_404 = client.get("/api/agent-profiles/non_existent_profile_999")
    assert res_404.status_code == 404
    assert "not found" in res_404.json()["detail"].lower()


def test_update_agent_profile_contract_and_model(client):
    """
    Verifies PUT /api/agent-profiles/{id} updates instructions, models, and contract parameters correctly.
    """
    new_contract = {
        "department": "planning",
        "is_manager": True,
        "timeout": 240,
        "max_retries": 4,
        "strict_json": True,
        "arabic_name": "د. سارة الاستشارية",
        "title": "رئيسة قطاع التخطيط الاستراتيجي"
    }

    update_payload = {
        "name": "Dr. Sara (د. سارة الاستشارية)",
        "description": "Lead Strategic Director with custom prompt",
        "instructions": "توجيهات مخصصة: قومي بتفكيك الأهداف البرمجية إلى متطلبات دقيقة مع معايير قبول صارمة.",
        "default_model_id": "deepseek-v4.1-flash-free",
        "default_variant_id": "max",
        "task_depth": "Deep",
        "settings_json": json.dumps(new_contract)
    }

    # Update planner
    res = client.put("/api/agent-profiles/planner", json=update_payload)
    assert res.status_code == 200
    data = res.json()
    assert data["name"] == "Dr. Sara (د. سارة الاستشارية)"
    assert data["default_model_id"] == "deepseek-v4.1-flash-free"
    assert data["default_variant_id"] == "max"
    assert data["task_depth"] == "Deep"
    assert "توجيهات مخصصة" in data["instructions"]

    # Verify persistence via fresh GET
    get_res = client.get("/api/agent-profiles/planner")
    assert get_res.status_code == 200
    persisted = get_res.json()
    assert persisted["task_depth"] == "Deep"
    parsed_settings = json.loads(persisted["settings_json"])
    assert parsed_settings["timeout"] == 240
    assert parsed_settings["max_retries"] == 4
    assert parsed_settings["strict_json"] is True


def test_update_agent_profile_via_alias(client):
    """
    Verifies PUT /api/agent-profiles/{alias} updates the underlying profile correctly.
    """
    update_payload = {
        "instructions": "تعليمات مخصصة لمهندس التحليل والمخاطر",
        "default_variant_id": "high"
    }
    res = client.put("/api/agent-profiles/eng_tarek", json=update_payload)
    assert res.status_code == 200
    data = res.json()
    assert data["id"] == "analyst"
    assert data["instructions"] == "تعليمات مخصصة لمهندس التحليل والمخاطر"
    assert data["default_variant_id"] == "high"


def test_update_agent_profile_not_found(client):
    """
    Verifies PUT /api/agent-profiles/{id} returns 404 for unknown profile IDs.
    """
    res = client.put("/api/agent-profiles/invalid_manager_id_000", json={"name": "Ghost"})
    assert res.status_code == 404
    assert "not found" in res.json()["detail"].lower()


def test_direct_consultation_dr_sara(client):
    """
    Verifies POST /api/chat returns structured Arabic architectural guidance for Dr. Sara.
    """
    payload = {
        "manager_id": "dr_sara",
        "message": "نريد إطلاق منصة ذكية لتسجيل الطلاب وتوزيع المقررات",
        "history": [
            {"role": "user", "content": "مرحباً د. سارة"},
            {"role": "assistant", "content": "أهلاً بك! كيف يمكنني مساعدتك؟"}
        ]
    }
    res = client.post("/api/chat", json=payload)
    assert res.status_code == 200
    data = res.json()
    assert data["manager_id"] == "dr_sara"
    assert "سارة" in data["manager_name"]
    assert "التخطيط" in data["title"]
    assert len(data["reply"]) > 20
    # Ordinary consultation must not create a mission suggestion.
    assert "سارة" in data["reply"] or "التخطيط" in data["reply"]
    assert data["suggested_mission"] is None


def test_direct_consultation_eng_tarek(client):
    """
    Verifies POST /api/chat returns risk & feasibility guidance for Eng. Tarek.
    """
    payload = {
        "manager_id": "eng_tarek",
        "message": "كيف نتفادى مشاكل الاختناقات في أوقات الذروة؟"
    }
    res = client.post("/api/chat", json=payload)
    assert res.status_code == 200
    data = res.json()
    assert data["manager_id"] == "eng_tarek"
    assert "طارق" in data["manager_name"]
    assert "التحليل" in data["title"] or "المخاطر" in data["title"]
    assert len(data["reply"]) > 20
    assert "طارق" in data["reply"] or "المخاطر" in data["reply"]
    assert data["suggested_mission"] is None


def test_direct_consultation_prof_yasmin(client):
    """
    Verifies POST /api/chat returns quality assurance guidance for Prof. Yasmin.
    """
    payload = {
        "manager_id": "prof_yasmin",
        "message": "ما هي معايير الجودة ومصفوفة الاختبارات المطلوبة؟"
    }
    res = client.post("/api/chat", json=payload)
    assert res.status_code == 200
    data = res.json()
    assert data["manager_id"] == "prof_yasmin"
    assert "ياسمين" in data["manager_name"]
    assert "الجودة" in data["title"] or "الاعتماد" in data["title"]
    assert len(data["reply"]) > 20
    assert "ياسمين" in data["reply"] or "الجودة" in data["reply"]
    assert data["suggested_mission"] is None


def test_direct_consultation_general_manager(client):
    """
    Verifies POST /api/chat returns overall orchestration guidance for General Manager.
    """
    payload = {
        "manager_id": "general_manager",
        "message": "بدء مهمة التحول الرقمي الشامل للمؤسسة"
    }
    res = client.post("/api/chat", json=payload)
    assert res.status_code == 200
    data = res.json()
    assert data["manager_id"] == "general_manager"
    assert "المدير العام" in data["manager_name"]
    assert len(data["reply"]) > 20
    assert "المدير العام" in data["reply"] or "المنظومة" in data["reply"]
    assert data["suggested_mission"] is None


def test_direct_consultation_invalid_manager_404(client):
    """
    Verifies POST /api/chat returns 404 for unknown manager IDs.
    """
    payload = {
        "manager_id": "unknown_manager_999",
        "message": "Hello"
    }
    res = client.post("/api/chat", json=payload)
    assert res.status_code == 404
    assert "not found" in res.json()["detail"].lower()


def test_manager_consultation_path_endpoint(client):
    """
    Verifies POST /api/managers/{manager_id}/consult functions identically.
    """
    payload = {
        "message": "استشارة مباشرة للمسار الإستراتيجي"
    }
    res = client.post("/api/managers/dr_sara/consult", json=payload)
    assert res.status_code == 200
    data = res.json()
    assert data["manager_id"] == "dr_sara"
    assert "سارة" in data["manager_name"]
    assert data["suggested_mission"] is None

    # Invalid manager on path
    res_404 = client.post("/api/managers/invalid_id_xyz/consult", json=payload)
    assert res_404.status_code == 404


def test_default_seeded_organization_hierarchy(db_session):
    """
    Verifies repository seeding includes natural names for managers and specialists:
    - General Manager (المدير العام)
    - Dr. Sara (د. سارة) - Planning & Strategy Manager (👑 Manager)
    - Eng. Tarek (م. طارق) - Analysis & Risk Manager (👑 Manager)
    - Prof. Yasmin (أ. ياسمين) - Quality & Review Manager (👑 Manager)
    - Specialists: Ahmed (أحمد), Khaled (خالد), Layla (ليلى).
    """
    repo = AgentProfileRepository(db_session)
    repo.seed_defaults()

    profiles = repo.list_all()
    profiles_by_id = {p.id: p for p in profiles}

    # Verify Dr. Sara
    planner = profiles_by_id["planner"]
    assert "د. سارة" in planner.name
    planner_cfg = json.loads(planner.settings_json)
    assert planner_cfg["is_manager"] is True
    assert planner_cfg["department"] == "planning"

    # Verify Eng. Tarek
    analyst = profiles_by_id["analyst"]
    assert "م. طارق" in analyst.name
    analyst_cfg = json.loads(analyst.settings_json)
    assert analyst_cfg["is_manager"] is True
    assert analyst_cfg["department"] == "analysis"

    # Verify Prof. Yasmin
    reviewer = profiles_by_id["reviewer"]
    assert "أ. ياسمين" in reviewer.name
    reviewer_cfg = json.loads(reviewer.settings_json)
    assert reviewer_cfg["is_manager"] is True
    assert reviewer_cfg["department"] == "quality"

    # Verify General Manager
    gm = profiles_by_id["custom"]
    assert "المدير العام" in gm.name
    gm_cfg = json.loads(gm.settings_json)
    assert gm_cfg["is_manager"] is True
    assert gm_cfg["department"] == "executive"

    # Verify Specialists: Ahmed, Khaled, Layla
    coder = profiles_by_id["coder"]
    assert "أحمد" in coder.name
    assert json.loads(coder.settings_json)["is_manager"] is False

    researcher = profiles_by_id["researcher"]
    assert "خالد" in researcher.name
    assert json.loads(researcher.settings_json)["is_manager"] is False

    extractor = profiles_by_id["data_extractor"]
    assert "ليلى" in extractor.name
    assert json.loads(extractor.settings_json)["is_manager"] is False


def test_repository_update_methods(db_session):
    """
    Direct unit test for AgentProfileRepository.update and update_profile methods.
    """
    repo = AgentProfileRepository(db_session)
    repo.seed_defaults()

    # Test update with kwargs
    updated = repo.update("planner", description="Updated description via kwargs")
    assert updated is not None
    assert updated.description == "Updated description via kwargs"

    # Test update_profile with dictionary
    updated2 = repo.update_profile("dr_sara", {"description": "Updated via alias dict"})
    assert updated2 is not None
    assert updated2.description == "Updated via alias dict"

    # Test update on non-existent profile
    assert repo.update("non_existent_profile_id") is None
    assert repo.update_profile("non_existent_profile_id", {"name": "test"}) is None
