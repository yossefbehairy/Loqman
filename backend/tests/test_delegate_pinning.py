"""Delegate by profile id: precise even when role_type is shared."""
from backend.app.db.repository import AgentProfileRepository


def test_delegate_pins_profile_by_id_not_role(client, db_session):
    from backend.app.db.models import MissionTask
    repo = AgentProfileRepository(db_session)
    clone = repo.create({
        "name": "Sara Clone (clone)",
        "role_type": "planner",
        "description": "company member clone",
        "instructions": "You are a clone.",
    })
    r = client.post("/api/missions/delegate", json={
        "conversation_id": "chat-a", "employee_role": "planner",
        "agent_profile_id": clone.id,
        "title": "Pinned task", "instructions": "Do pinned work.",
        "model_id": "muse-spark-1.3-contributor-free", "variant": "minimal",
    })
    assert r.status_code == 201, r.text
    mid = r.json()["id"]
    import time
    final = None
    for _ in range(60):
        g = client.get(f"/api/missions/{mid}").json()
        if g["status"] in ("succeeded", "partially_succeeded", "failed", "cancelled"):
            final = g
            break
        time.sleep(1)
    assert final is not None
    assert final["status"] in ("succeeded", "partially_succeeded")
    assert final["tasks"][0]["status"] == "succeeded"
    assert final["tasks"][0]["output"] is not None
    # Execution pinned to the clone, not the seeded planner sharing its role.
    row = db_session.query(MissionTask).filter_by(mission_id=mid).first()
    assert row.agent_profile_id == clone.id
    # Display keeps the role readable, execution used the pinned profile.
    assert final["tasks"][0]["employee_role"] == "planner"


def test_delegate_unknown_profile_id_404(client):
    r = client.post("/api/missions/delegate", json={
        "conversation_id": "chat-a", "employee_role": "planner",
        "agent_profile_id": "no-such-profile",
        "title": "T", "instructions": "I",
        "model_id": "muse-spark-1.3-contributor-free",
    })
    assert r.status_code == 404
