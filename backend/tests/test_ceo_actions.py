"""CEO actions: proposals stay pending; approve/deny are gated and single-shot."""
from backend.app.db.repository import AgentProfileRepository


def _ask(client, question):
    r = client.post("/api/ceo/ask", json={"question": question})
    assert r.status_code == 200, r.text
    return r.json()


def test_propose_update_contract_stays_pending_until_approved(client, db_session):
    before = AgentProfileRepository(db_session).get_by_id("planner").default_variant_id
    data = _ask(client, "من فضلك سرع سارة")
    assert len(data["actions_proposed"]) == 1
    action = data["actions_proposed"][0]
    assert action["kind"] == "update_contract"
    # Proposal recorded, contract untouched.
    assert AgentProfileRepository(db_session).get_by_id("planner").default_variant_id == before
    pending = client.get("/api/ceo/actions?status=pending").json()
    assert any(a["id"] == action["id"] for a in pending)


def test_approve_requires_confirm_header(client):
    action_id = _ask(client, "سرع سارة")["actions_proposed"][0]["id"]
    r = client.post(f"/api/ceo/actions/{action_id}/approve")
    assert r.status_code == 403
    r = client.post(f"/api/ceo/actions/{action_id}/deny")
    assert r.status_code == 403


def test_approve_executes_once_then_conflicts(client, db_session):
    action_id = _ask(client, "سرع سارة")["actions_proposed"][0]["id"]
    h = {"X-AgentOS-Confirm": "true"}
    r = client.post(f"/api/ceo/actions/{action_id}/approve", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "approved"
    assert AgentProfileRepository(db_session).get_by_id("planner").default_variant_id == "low"
    # Second decision on the same action is a conflict, not a re-execution.
    r2 = client.post(f"/api/ceo/actions/{action_id}/approve", headers=h)
    assert r2.status_code == 409
    r3 = client.post(f"/api/ceo/actions/{action_id}/deny", headers=h)
    assert r3.status_code == 409


def test_deny_changes_nothing(client, db_session):
    before = AgentProfileRepository(db_session).get_by_id("planner").default_variant_id
    action_id = _ask(client, "سرع سارة")["actions_proposed"][0]["id"]
    r = client.post(f"/api/ceo/actions/{action_id}/deny",
                    headers={"X-AgentOS-Confirm": "true"})
    assert r.status_code == 200
    assert r.json()["status"] == "denied"
    assert AgentProfileRepository(db_session).get_by_id("planner").default_variant_id == before
    assert client.get("/api/ceo/actions?status=pending").json() == []


def test_stale_proposal_loses_to_newer_edit(client, db_session):
    repo = AgentProfileRepository(db_session)
    action_id = _ask(client, "سرع سارة")["actions_proposed"][0]["id"]
    # A newer human edit lands after the proposal was recorded.
    repo.update("planner", default_variant_id="max")
    r = client.post(f"/api/ceo/actions/{action_id}/approve",
                    headers={"X-AgentOS-Confirm": "true"})
    assert r.status_code == 200
    assert r.json()["status"] == "failed"
    assert repo.get_by_id("planner").default_variant_id == "max"


def test_propose_new_profile_and_unknown_action(client):
    data = _ask(client, "أضف موظف بحث جديد لفريق التحليل")
    assert len(data["actions_proposed"]) == 1
    assert data["actions_proposed"][0]["kind"] == "new_profile"
    r = client.post("/api/ceo/actions/does-not-exist/approve",
                    headers={"X-AgentOS-Confirm": "true"})
    assert r.status_code == 404
