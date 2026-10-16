"""
Empirical Stress-Testing & Adversarial Challenge Suite for Milestone 1 Backend APIs
Tests:
1. PUT /api/agent-profiles/{profile_id} with stress payloads (empty, partial, length limits, special chars, Arabic, aliases, 404s).
2. POST /api/chat with all 4 manager IDs (dr_sara, eng_tarek, prof_yasmin, general_manager) and primary keys.
3. Structured suggested_mission and persona verification.
4. Clean 404 responses for invalid manager IDs.
5. Edge cases: long messages, multi-turn history, malformed inputs.
6. Bug hunting & vulnerability reproduction: settings_json non-dict types causing unhandled AttributeError in chat.
"""

import json
import pytest
from fastapi.testclient import TestClient


# ============================================================================
# 1. Agent Profile Update Stress Tests (PUT /api/agent-profiles/{profile_id})
# ============================================================================

def test_stress_profile_empty_payload(client: TestClient):
    """
    Stress test: PUT with completely empty payload {}.
    Expected: 200 OK, existing fields remain unchanged.
    """
    before = client.get("/api/agent-profiles/planner").json()
    res = client.put("/api/agent-profiles/planner", json={})
    assert res.status_code == 200
    after = res.json()
    assert after["name"] == before["name"]
    assert after["instructions"] == before["instructions"]
    assert after["default_model_id"] == before["default_model_id"]


def test_stress_profile_partial_payloads(client: TestClient):
    """
    Stress test: PUT with isolated single-field updates.
    Verifies that unmentioned fields are not cleared or corrupted.
    """
    before = client.get("/api/agent-profiles/analyst").json()

    # Update only instructions
    new_instructions = "تعليمات تجريبية محدثة لأخصائي التحليل والمخاطر"
    res1 = client.put("/api/agent-profiles/analyst", json={"instructions": new_instructions})
    assert res1.status_code == 200
    d1 = res1.json()
    assert d1["instructions"] == new_instructions
    assert d1["name"] == before["name"]
    assert d1["role_type"] == before["role_type"]

    # Update only task_depth
    res2 = client.put("/api/agent-profiles/analyst", json={"task_depth": "Quick"})
    assert res2.status_code == 200
    d2 = res2.json()
    assert d2["task_depth"] == "Quick"
    assert d2["instructions"] == new_instructions

    # Update only default_variant_id
    res3 = client.put("/api/agent-profiles/analyst", json={"default_variant_id": "minimal"})
    assert res3.status_code == 200
    assert res3.json()["default_variant_id"] == "minimal"


def test_stress_profile_string_length_boundaries(client: TestClient):
    """
    Stress test: Boundary conditions for string length limits.
    name: max_length 255
    role_type: max_length 64
    instructions: long text (50,000 chars)
    """
    # 1. Name boundary: exactly 255 chars -> 200 OK
    name_255 = "A" * 255
    res_ok = client.put("/api/agent-profiles/reviewer", json={"name": name_255})
    assert res_ok.status_code == 200
    assert res_ok.json()["name"] == name_255

    # 2. Name boundary: 256 chars -> 422 Unprocessable Entity
    name_256 = "A" * 256
    res_err = client.put("/api/agent-profiles/reviewer", json={"name": name_256})
    assert res_err.status_code == 422

    # 3. Role_type boundary: exactly 64 chars -> 200 OK
    role_64 = "R" * 64
    res_role_ok = client.put("/api/agent-profiles/reviewer", json={"role_type": role_64})
    assert res_role_ok.status_code == 200
    assert res_role_ok.json()["role_type"] == role_64

    # 4. Role_type boundary: 65 chars -> 422 Unprocessable Entity
    role_65 = "R" * 65
    res_role_err = client.put("/api/agent-profiles/reviewer", json={"role_type": role_65})
    assert res_role_err.status_code == 422

    # 5. Very long instructions (50,000 characters) -> 200 OK
    long_instructions = "خطوات تفصيلية: " + ("اختبار " * 5000)
    res_long = client.put("/api/agent-profiles/reviewer", json={"instructions": long_instructions})
    assert res_long.status_code == 200
    assert len(res_long.json()["instructions"]) == len(long_instructions)


def test_stress_profile_special_characters_and_injections(client: TestClient):
    """
    Stress test: Special characters, SQL injection patterns, XSS snippets, emojis, control chars.
    Ensures safe persistence without SQL errors or data corruption.
    """
    adversarial_payload = {
        "name": "👑 د. سارة '; DROP TABLE agent_profiles; -- <script>alert('xss')</script>",
        "description": "Risk & Security Director \\' \" ; -- /* comment */",
        "instructions": "تعليمات متقدمة:\n\t- خطوة 1\n\t- خطوة 2\r\nSpecial: ™®©§¶• \u200b\u200e\u200f",
        "settings_json": json.dumps({
            "badge": "👑 Manager",
            "injection_attempt": "1; DROP TABLE execution_events;",
            "nested": {"array": [1, 2, "test' OR '1'='1"]}
        })
    }
    res = client.put("/api/agent-profiles/dr_sara", json=adversarial_payload)
    assert res.status_code == 200
    data = res.json()
    assert "'; DROP TABLE agent_profiles; --" in data["name"]
    assert "<script>alert('xss')</script>" in data["name"]
    assert "👑" in data["name"]

    # Verify database is intact by querying another profile
    check_db = client.get("/api/agent-profiles/analyst")
    assert check_db.status_code == 200


def test_stress_profile_arabic_diacritics_and_rtl(client: TestClient):
    """
    Stress test: Full Arabic tashkeel (diacritics) and complex RTL expressions.
    """
    tashkeel_name = "د. سَارَةُ المُسْتَشَارَةُ الإِسْتِرَاتِيجِيَّةُ"
    tashkeel_instructions = "تَوْجِيهَاتٌ صَارِمَةٌ: تَفْكِيكُ الأَهْدَافِ وَتَحْلِيلُ المَخَاطِرِ بِدِقَّةٍ عَالِيَةٍ."
    res = client.put("/api/agent-profiles/dr_sara", json={
        "name": tashkeel_name,
        "instructions": tashkeel_instructions
    })
    assert res.status_code == 200
    data = res.json()
    assert data["name"] == tashkeel_name
    assert data["instructions"] == tashkeel_instructions

    # Verify persistence on roundtrip GET
    get_res = client.get("/api/agent-profiles/dr_sara")
    assert get_res.status_code == 200
    assert get_res.json()["name"] == tashkeel_name


def test_stress_profile_empty_string_validation(client: TestClient):
    """
    Stress test: Empty strings on fields with min_length=1 must return 422.
    """
    # Empty name
    res1 = client.put("/api/agent-profiles/dr_sara", json={"name": ""})
    assert res1.status_code == 422

    # Empty instructions
    res2 = client.put("/api/agent-profiles/dr_sara", json={"instructions": ""})
    assert res2.status_code == 422

    # Empty role_type
    res3 = client.put("/api/agent-profiles/dr_sara", json={"role_type": ""})
    assert res3.status_code == 422


def test_stress_profile_all_aliases_update(client: TestClient):
    """
    Stress test: Updating each of the 10 recognized profile aliases.
    dr_sara, eng_tarek, prof_yasmin, general_manager, ahmed, khaled, layla, omar, nour, mona.
    """
    aliases = [
        ("dr_sara", "planner"),
        ("eng_tarek", "analyst"),
        ("prof_yasmin", "reviewer"),
        ("general_manager", "custom"),
        ("ahmed", "coder"),
        ("khaled", "researcher"),
        ("layla", "data_extractor"),
        ("omar", "summarizer"),
        ("nour", "report_writer"),
        ("mona", "translator"),
    ]
    for alias, expected_canonical_id in aliases:
        res = client.put(f"/api/agent-profiles/{alias}", json={"description": f"Updated via alias {alias}"})
        assert res.status_code == 200, f"Failed updating via alias {alias}"
        data = res.json()
        assert data["id"] == expected_canonical_id
        assert data["description"] == f"Updated via alias {alias}"


def test_stress_profile_not_found(client: TestClient):
    """
    Stress test: PUT on unknown profile IDs returns clean 404 with detail message.
    """
    res = client.put("/api/agent-profiles/unknown_profile_999", json={"name": "Ghost"})
    assert res.status_code == 404
    assert "not found" in res.json()["detail"].lower()


# ============================================================================
# 2. Executive Chat Consultation Tests (POST /api/chat & /api/managers/{id}/consult)
# ============================================================================

def test_stress_chat_all_4_managers_personas_and_suggested_missions(client: TestClient):
    """
    Empirically verifies POST /api/chat for all 4 required manager personas:
    1. dr_sara -> Planning & Strategy Manager (د. سارة)
    2. eng_tarek -> Analysis & Risk Manager (م. طارق)
    3. prof_yasmin -> Quality & Review Manager (أ. ياسمين)
    4. general_manager -> General Manager (المدير العام)

    Verifies persona-specific reply, manager metadata, and structured suggested_mission.
    """
    test_cases = [
        {
            "manager_id": "dr_sara",
            "name_keyword": "سارة",
            "title_keyword": "التخطيط",
            "expected_department": "planning",
            "mission_key": "phases",
        },
        {
            "manager_id": "eng_tarek",
            "name_keyword": "طارق",
            "title_keyword": "التحليل",
            "expected_department": "analysis",
            "mission_key": "risk_focus",
        },
        {
            "manager_id": "prof_yasmin",
            "name_keyword": "ياسمين",
            "title_keyword": "الجودة",
            "expected_department": "quality",
            "mission_key": "quality_gates",
        },
        {
            "manager_id": "general_manager",
            "name_keyword": "المدير العام",
            "title_keyword": "المدير العام",
            "expected_department": "executive",
            "mission_key": "pipeline",
        },
    ]

    for tc in test_cases:
        goal_msg = f"مهمة استشارية خاصة لإدارة {tc['expected_department']}"
        payload = {
            "manager_id": tc["manager_id"],
            "message": goal_msg,
            "history": [
                {"role": "user", "content": "مرحباً"},
                {"role": "assistant", "content": "أهلاً بك، كيف يمكنني مساعدتك؟"}
            ]
        }
        res = client.post("/api/chat", json=payload)
        assert res.status_code == 200, f"Chat failed for manager {tc['manager_id']}"
        data = res.json()

        # 1. Metadata check
        assert data["manager_id"] == tc["manager_id"]
        assert tc["name_keyword"] in data["manager_name"]
        assert tc["title_keyword"] in data["title"]

        # 2. Reply persona check
        assert len(data["reply"]) > 20
        assert tc["name_keyword"] in data["reply"] or tc["title_keyword"] in data["reply"]

        # 3. Structured suggested_mission check
        assert data["suggested_mission"] is not None
        mission = data["suggested_mission"]
        assert isinstance(mission["title"], str) and len(mission["title"]) > 5
        assert isinstance(mission["input_json"], dict)
        assert mission["input_json"]["department"] == tc["expected_department"]
        assert tc["mission_key"] in mission["input_json"]
        assert isinstance(mission["input_json"][tc["mission_key"]], list)
        assert len(mission["input_json"][tc["mission_key"]]) > 0


def test_stress_chat_with_primary_keys(client: TestClient):
    """
    Verifies that calling POST /api/chat with primary database IDs
    (planner, analyst, reviewer, custom) also yields the corresponding personas.
    """
    primary_map = [
        ("planner", "سارة", "planning"),
        ("analyst", "طارق", "analysis"),
        ("reviewer", "ياسمين", "quality"),
        ("custom", "المدير العام", "executive"),
    ]
    for pk, expected_name, expected_dept in primary_map:
        payload = {"manager_id": pk, "message": "استشارة تجريبية"}
        res = client.post("/api/chat", json=payload)
        assert res.status_code == 200
        data = res.json()
        assert expected_name in data["manager_name"]
        assert data["suggested_mission"]["input_json"]["department"] == expected_dept


def test_stress_chat_path_endpoint(client: TestClient):
    """
    Verifies POST /api/managers/{manager_id}/consult for all 4 managers.
    """
    for mid in ["dr_sara", "eng_tarek", "prof_yasmin", "general_manager"]:
        payload = {"message": "استشارة عبر مسار الـ URL"}
        res = client.post(f"/api/managers/{mid}/consult", json=payload)
        assert res.status_code == 200
        data = res.json()
        assert data["manager_id"] == mid
        assert data["suggested_mission"] is not None


def test_stress_chat_clean_404_for_invalid_manager_ids(client: TestClient):
    """
    Verifies clean 404 responses for invalid manager IDs across both endpoints:
    - POST /api/chat with body manager_id
    - POST /api/managers/{id}/consult with path manager_id
    """
    invalid_ids = [
        "unknown_manager_999",
        "invalid_hacker'; --",
        "random_id_xyz",
        "non_existent",
    ]
    for inv_id in invalid_ids:
        # 1. Via POST /api/chat
        res1 = client.post("/api/chat", json={"manager_id": inv_id, "message": "Hello"})
        assert res1.status_code == 404
        assert "detail" in res1.json()
        assert "not found" in res1.json()["detail"].lower()

        # 2. Via POST /api/managers/{id}/consult
        res2 = client.post(f"/api/managers/{inv_id}/consult", json={"message": "Hello"})
        assert res2.status_code == 404
        assert "detail" in res2.json()
        assert "not found" in res2.json()["detail"].lower()


def test_stress_chat_payload_edge_cases(client: TestClient):
    """
    Stress test: Boundary conditions, long inputs, empty fields, and multi-turn history.
    """
    # 1. Missing message field -> 422
    res_no_msg = client.post("/api/chat", json={"manager_id": "dr_sara"})
    assert res_no_msg.status_code == 422

    # 2. Empty message string -> 422
    res_empty_msg = client.post("/api/chat", json={"manager_id": "dr_sara", "message": ""})
    assert res_empty_msg.status_code == 422

    # 3. Missing manager_id defaults cleanly to general_manager -> 200 OK
    res_default_gm = client.post("/api/chat", json={"message": "هدف عام للمنظومة"})
    assert res_default_gm.status_code == 200
    assert "المدير العام" in res_default_gm.json()["manager_name"]

    # 4. Null manager_id defaults cleanly to general_manager -> 200 OK
    res_null_gm = client.post("/api/chat", json={"manager_id": None, "message": "هدف عام للمنظومة"})
    assert res_null_gm.status_code == 200
    assert "المدير العام" in res_null_gm.json()["manager_name"]

    # 5. Very long user message (15,000 characters)
    long_msg = "وصف تفصيلي جداً للمشروع: " + ("نطاق العمل والتحديات التقنية " * 500)
    res_long = client.post("/api/chat", json={"manager_id": "dr_sara", "message": long_msg})
    assert res_long.status_code == 200
    assert res_long.json()["suggested_mission"] is not None

    # 6. Deep conversation history (20 turns)
    history = []
    for i in range(20):
        role = "user" if i % 2 == 0 else "assistant"
        history.append({"role": role, "content": f"Turn {i}: رسالة تاريخية برقم {i}"})
    res_hist = client.post("/api/chat", json={
        "manager_id": "eng_tarek",
        "message": "استفسار بعد تاريخ محادثة طويل",
        "history": history
    })
    assert res_hist.status_code == 200
    assert "طارق" in res_hist.json()["manager_name"]


# ============================================================================
# 3. Vulnerability Remediation Verification: Non-dict settings_json handled safely
# ============================================================================

def test_vulnerability_settings_json_non_dict_causes_chat_crash(client: TestClient):
    """
    REMEDIATION VERIFICATION:
    Verifies that when an agent's settings_json contains non-dict JSON (e.g. '[1, 2, 3]'),
    POST /api/chat does NOT crash with an unhandled AttributeError / 500,
    and instead gracefully falls back to profile defaults and returns 200 OK.
    """
    # Step 1: User updates an agent's settings_json with a JSON list
    put_res = client.put("/api/agent-profiles/dr_sara", json={"settings_json": "[1, 2, 3]"})
    assert put_res.status_code == 200

    # Step 2: User consults the manager via POST /api/chat
    chat_res = client.post("/api/chat", json={"manager_id": "dr_sara", "message": "طلب استشارة معمارية"})
    assert chat_res.status_code == 200
    data = chat_res.json()
    assert data["manager_id"] == "dr_sara"
    assert "سارة" in data["manager_name"]
    assert data["suggested_mission"] is not None


def test_vulnerability_settings_json_null_and_scalars_cause_chat_crash(client: TestClient):
    """
    REMEDIATION VERIFICATION:
    Verifies that JSON null, int, string, and bool scalar types in settings_json
    do NOT cause AttributeError / 500 crashes and return 200 OK with correct persona.
    """
    scalar_cases = ["null", "12345", '"raw_string"', "true"]
    for raw_json in scalar_cases:
        put_res = client.put("/api/agent-profiles/eng_tarek", json={"settings_json": raw_json})
        assert put_res.status_code == 200

        chat_res = client.post("/api/chat", json={"manager_id": "eng_tarek", "message": "استشارة"})
        assert chat_res.status_code == 200, f"Failed for {raw_json}: got status {chat_res.status_code}"
        assert "طارق" in chat_res.json()["manager_name"]


def test_vulnerability_path_consult_endpoint_crashes_on_non_dict_settings_json(client: TestClient):
    """
    REMEDIATION VERIFICATION:
    Verifies that POST /api/managers/{id}/consult does NOT crash on non-dict settings_json.
    """
    put_res = client.put("/api/agent-profiles/prof_yasmin", json={"settings_json": "[10, 20]"})
    assert put_res.status_code == 200

    res = client.post("/api/managers/prof_yasmin/consult", json={"message": "مصفوفة الجودة"})
    assert res.status_code == 200
    assert "ياسمين" in res.json()["manager_name"]


def test_agent_profile_settings_json_malformed_rejected(client: TestClient):
    """
    Verifies that malformed JSON strings in settings_json are rejected with HTTP 422 Unprocessable Entity.
    """
    res = client.put("/api/agent-profiles/dr_sara", json={"settings_json": "{not valid json"})
    assert res.status_code == 422


# ============================================================================
# 4. Rapid Sequential Stress & Concurrency Simulation
# ============================================================================

def test_stress_rapid_profile_updates_and_chat_queries(client: TestClient):
    """
    Executes a burst of 30 rapid interleaved profile updates and chat consultations
    to ensure database connection pooling and transaction commit reliability.
    """
    for i in range(15):
        # Update planner
        u_res = client.put("/api/agent-profiles/planner", json={
            "description": f"Rapid iteration {i} description",
            "task_depth": "Standard" if i % 2 == 0 else "Deep"
        })
        assert u_res.status_code == 200

        # Chat with planner
        c_res = client.post("/api/chat", json={
            "manager_id": "dr_sara",
            "message": f"طلب استشارة رقم {i}"
        })
        assert c_res.status_code == 200
        assert c_res.json()["suggested_mission"] is not None

