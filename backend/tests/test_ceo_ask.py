"""CEO ask: read-only reasoning loop over live DB data (MockRuntime)."""
from backend.app.db.models import Mission, MissionTask
from backend.app.db.models import PendingAction


def _counts(client):
    return (
        len(client.get("/api/missions").json()),
        len(client.get("/api/ceo/actions?status=pending").json()),
    )


def test_ask_about_missions_returns_real_ids(client):
    created = client.post("/api/missions", json={"objective": "Probe mission for ask test"}).json()
    mid = created["id"]
    r = client.post("/api/ceo/ask", json={"question": "ما هي آخر المهام وحالاتها؟"})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["iterations"] == 2
    assert mid in data["reply"]
    assert data["actions_proposed"] == []


def test_ask_about_team_returns_profiles(client):
    r = client.post("/api/ceo/ask", json={"question": "Who is on the team?"})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["iterations"] == 2
    assert "planner" in data["reply"] or "Sara" in data["reply"]


def test_ask_direct_final_first_iteration(client):
    r = client.post("/api/ceo/ask", json={"question": "صباح الخير"})
    assert r.status_code == 200, r.text
    assert r.json()["iterations"] == 1
    assert len(r.json()["reply"]) > 0


def test_smalltalk_takes_fast_single_call_path(client):
    """Small talk bypasses the tool loop entirely: 1 call, plain text, no actions."""
    r = client.post("/api/ceo/ask", json={"question": "أهلاً يا مدير، عامله ايه؟"})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["fast"] is True
    assert data["iterations"] == 1
    assert data["actions_proposed"] == []
    assert "أهلاً بيك" in data["reply"]  # the deterministic mock fast reply (plain text)


def test_fast_reply_is_plain_text_not_json(client):
    r = client.post("/api/ceo/ask", json={"question": "شكراً ليك"})
    assert r.status_code == 200, r.text
    reply = r.json()["reply"]
    assert not reply.lstrip().startswith("{")


def test_data_question_never_takes_fast_path(client):
    r = client.post("/api/ceo/ask", json={"question": "شكرا، وبالمناسبة ما هي آخر المهام؟"})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["fast"] is False


def test_fast_path_records_usage_kind(client, db_session):
    from backend.app.db.models import UsageEvent
    from backend.app.runtime.ledger import KIND_CEO_ASK_FAST

    before = db_session.query(UsageEvent).count()
    r = client.post("/api/ceo/ask", json={"question": "مساء الخير"})
    assert r.status_code == 200, r.text
    rows = db_session.query(UsageEvent).all()
    assert len(rows) == before + 1
    assert rows[-1].kind == KIND_CEO_ASK_FAST


def test_ask_empty_question_400(client):
    r = client.post("/api/ceo/ask", json={"question": "   "})
    assert r.status_code == 400


def test_stubborn_loop_caps_at_three_iterations(client):
    r = client.post("/api/ceo/ask", json={"question": "stubborn_loop probe please"})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["iterations"] == 3
    assert len(data["reply"]) > 0
    assert data["fast"] is False


def test_self_correction_on_unknown_tool(client):
    r = client.post("/api/ceo/ask", json={"question": "glitch_probe recovery check"})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["iterations"] == 2
    assert len(data["reply"]) > 0


def test_ask_progress_reports_done_with_request_id(client):
    r = client.post("/api/ceo/ask", json={
        "question": "صباح الخير",
        "request_id": "prog-done-1",
    })
    assert r.status_code == 200, r.text
    p = client.get("/api/ceo/ask-progress/prog-done-1")
    assert p.status_code == 200, p.text
    data = p.json()
    assert data["done"] is True
    assert data["stage"] == "done"
    assert data["iteration"] >= 1
    assert data["started_at"] and data["updated_at"]


def test_ask_progress_unknown_id_404(client):
    r = client.get("/api/ceo/ask-progress/does-not-exist")
    assert r.status_code == 404


def test_reads_create_no_mission_or_profile_rows(client, db_session):
    missions_before = db_session.query(Mission).count()
    tasks_before = db_session.query(MissionTask).count()
    pending_before = db_session.query(PendingAction).count()
    r = client.post("/api/ceo/ask", json={"question": "ما هي آخر المهام؟"})
    assert r.status_code == 200
    assert db_session.query(Mission).count() == missions_before
    assert db_session.query(MissionTask).count() == tasks_before
    assert db_session.query(PendingAction).count() == pending_before
