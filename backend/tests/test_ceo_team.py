"""CEO team building: propose a whole team -> approve/deny; invalid batches fail cleanly.

Approval semantics under test: a new_team action validates every member
(known role, name + instructions) BEFORE the first write, so an invalid
proposal fails with zero profiles created (no partial/zombie team). Approved
decisions return the really-created rows (`created_profiles`) plus the team
name so the UI can materialize the team without guessing.
"""
import json

from backend.app.db.models import AgentProfile, PendingAction
from backend.app.db.repository import AgentProfileRepository
from backend.app.orchestration import ceo_tools

CONFIRM = {"X-AgentOS-Confirm": "true"}
TEAM_QUESTION = "اعمل فريق مراجعة أمنية"


def _ask(client, question):
    r = client.post("/api/ceo/ask", json={"question": question})
    assert r.status_code == 200, r.text
    return r.json()


def _pending_team_action(client):
    data = _ask(client, TEAM_QUESTION)
    assert len(data["actions_proposed"]) == 1, data
    proposal = data["actions_proposed"][0]
    assert proposal["kind"] == "new_team"
    return proposal["id"]


def _insert_team_action(db_session, members, name="Risky Team"):
    """Build a pending new_team row directly (simulates a tampered/older payload)."""
    action = PendingAction(
        kind="new_team",
        payload_json=json.dumps({"name": name, "members": members}),
        summary=f"New team '{name}'",
        status="pending",
        created_by="test",
    )
    db_session.add(action)
    db_session.commit()
    db_session.refresh(action)
    return action.id


def _payload_of(db_session, action_id):
    action = db_session.query(PendingAction).filter(PendingAction.id == action_id).first()
    assert action is not None
    return json.loads(action.payload_json)


def test_ask_proposes_team_without_creating_profiles(client, db_session):
    before = db_session.query(AgentProfile).count()
    data = _ask(client, TEAM_QUESTION)
    assert data["iterations"] == 2
    assert len(data["actions_proposed"]) == 1
    proposal = data["actions_proposed"][0]
    assert proposal["kind"] == "new_team"
    assert "team" in proposal["summary"].lower() or "فريق" in proposal["summary"]
    # Recorded as a pending row only — no profiles exist yet.
    pending = client.get("/api/ceo/actions?status=pending").json()
    assert any(a["id"] == proposal["id"] and a["kind"] == "new_team" for a in pending)
    assert db_session.query(AgentProfile).count() == before
    assert (
        db_session.query(AgentProfile)
        .filter(AgentProfile.role_type == "security_auditor")
        .count() == 0
    )
    # The proposal really is a multi-profile team, not a single hire.
    payload = _payload_of(db_session, proposal["id"])
    assert len(payload["members"]) >= 2


def test_approve_creates_all_member_profiles(client, db_session):
    action_id = _pending_team_action(client)
    payload = _payload_of(db_session, action_id)
    r = client.post(f"/api/ceo/actions/{action_id}/approve", headers=CONFIRM)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "approved"
    repo = AgentProfileRepository(db_session)
    profiles = repo.list_all()
    for member in payload["members"]:
        prof = next(
            (p for p in profiles
             if p.name == member["name"] and p.role_type == member["role_type"]),
            None,
        )
        assert prof is not None, f"missing team member {member}"
        assert prof.id.startswith("custom-")
        assert prof.instructions == member["instructions"]
    assert any(p.role_type == "security_auditor" for p in profiles)
    # The approval response carries the executed team so the UI can materialize
    # it locally without a second round-trip: every member + the proposed name.
    assert body["team_name"] == payload["name"]
    created = body["created_profiles"]
    assert len(created) == len(payload["members"])
    assert {(c["name"], c["role_type"]) for c in created} == {
        (m["name"], m["role_type"]) for m in payload["members"]
    }
    # The returned ids are the real persisted profile rows.
    for row in created:
        persisted = repo.get_by_id(row["id"])
        assert persisted is not None
        assert persisted.name == row["name"]
        assert persisted.role_type == row["role_type"]


def test_approve_new_profile_returns_created_profile(client, db_session):
    data = _ask(client, "أضف موظف بحث جديد لفريق التحليل")
    assert len(data["actions_proposed"]) == 1
    proposal = data["actions_proposed"][0]
    assert proposal["kind"] == "new_profile"
    payload = _payload_of(db_session, proposal["id"])
    r = client.post(f"/api/ceo/actions/{proposal['id']}/approve", headers=CONFIRM)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "approved"
    assert body["team_name"] is None
    assert len(body["created_profiles"]) == 1
    created = body["created_profiles"][0]
    assert created["name"] == payload["name"]
    assert created["role_type"] == payload["role_type"]
    prof = AgentProfileRepository(db_session).get_by_id(created["id"])
    assert prof is not None
    assert prof.name == payload["name"]
    assert prof.role_type == payload["role_type"]


def test_deny_creates_nothing(client, db_session):
    before = db_session.query(AgentProfile).count()
    data = _ask(client, TEAM_QUESTION)
    action_id = data["actions_proposed"][0]["id"]
    payload = _payload_of(db_session, action_id)
    r = client.post(f"/api/ceo/actions/{action_id}/deny", headers=CONFIRM)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "denied"
    assert body["created_profiles"] == []
    assert body["team_name"] is None
    assert db_session.query(AgentProfile).count() == before
    names = {p.name for p in AgentProfileRepository(db_session).list_all()}
    assert not names & {m["name"] for m in payload["members"]}


def test_team_decision_requires_confirm_header(client):
    action_id = _pending_team_action(client)
    assert client.post(f"/api/ceo/actions/{action_id}/approve").status_code == 403
    assert client.post(f"/api/ceo/actions/{action_id}/deny").status_code == 403


def test_invalid_role_fails_cleanly_without_partial_profiles(client, db_session):
    before = db_session.query(AgentProfile).count()
    action_id = _insert_team_action(db_session, [
        {"name": "Zombie One", "role_type": "security_auditor", "instructions": "Valid member."},
        {"name": "Zombie Two", "role_type": "not_a_role", "instructions": "Invalid role member."},
    ])
    r = client.post(f"/api/ceo/actions/{action_id}/approve", headers=CONFIRM)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "failed"
    assert body["decision_note"] and "unknown role" in body["decision_note"]
    # A failed decision reports nothing materialized.
    assert body["created_profiles"] == []
    assert body["team_name"] is None
    # No partial zombie state: even the valid first member must NOT exist.
    assert db_session.query(AgentProfile).count() == before
    names = {p.name for p in AgentProfileRepository(db_session).list_all()}
    assert "Zombie One" not in names and "Zombie Two" not in names
    # Failed is terminal: the single-transition guard still applies.
    r2 = client.post(f"/api/ceo/actions/{action_id}/approve", headers=CONFIRM)
    assert r2.status_code == 409


def test_member_missing_instructions_fails_cleanly(client, db_session):
    before = db_session.query(AgentProfile).count()
    action_id = _insert_team_action(db_session, [
        {"name": "No Instructions", "role_type": "reviewer"},
    ])
    r = client.post(f"/api/ceo/actions/{action_id}/approve", headers=CONFIRM)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "failed"
    assert body["decision_note"] and "requires name and instructions" in body["decision_note"]
    assert db_session.query(AgentProfile).count() == before


def test_propose_new_team_schema_validation(db_session):
    pending_before = db_session.query(PendingAction).count()
    member = {"name": "M", "role_type": "security_auditor", "instructions": "Audit."}
    # Empty members list -> clean Pydantic rejection, nothing recorded.
    obs = ceo_tools.run_tool(db_session, "propose_new_team", {"name": "Empty Team", "members": []})
    assert obs.startswith("ERROR: invalid args")
    # More than 8 members -> same clean rejection.
    obs = ceo_tools.run_tool(db_session, "propose_new_team",
                             {"name": "Huge Team", "members": [member] * 9})
    assert obs.startswith("ERROR: invalid args")
    # Unknown role inside a member -> handler refuses, nothing recorded.
    obs = ceo_tools.run_tool(db_session, "propose_new_team", {
        "name": "Bad Team",
        "members": [{"name": "X", "role_type": "hacker", "instructions": "Nope."}],
    })
    assert obs.startswith("ERROR: unknown role")
    assert db_session.query(PendingAction).count() == pending_before
