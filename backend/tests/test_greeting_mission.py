"""Greeting short-circuit: greetings get one natural reply, never the pipeline."""
from backend.app.api.routes.missions import is_greeting_objective


def test_greeting_matcher_exact_only():
    assert is_greeting_objective("عامل ايه")
    assert is_greeting_objective("عامل اي")
    assert is_greeting_objective("عامل إيه؟")
    assert is_greeting_objective("  ازيك؟  ")
    assert is_greeting_objective("Hi")
    assert is_greeting_objective("hello!")
    # Real task descriptions must NOT match.
    assert not is_greeting_objective("Analyze sample requirements and return plan")
    assert not is_greeting_objective("X")
    assert not is_greeting_objective("Cancel me please")
    assert not is_greeting_objective("List probe")
    assert not is_greeting_objective("عامل ايه في الشغل بكرة؟")


def test_greeting_mission_returns_single_reply(client):
    r = client.post("/api/missions", json={"objective": "عامل ايه"})
    assert r.status_code == 201, r.text
    m = r.json()
    assert m["status"] == "succeeded"
    assert len(m["tasks"]) == 1
    assert m["tasks"][0]["status"] == "succeeded"
    assert m["tasks"][0]["task_key"] == "greeting_reply"
    reply = m["final_summary"]
    assert isinstance(reply, str) and len(reply.strip()) > 0
    # Same reply visible through the mission detail endpoint.
    g = client.get(f"/api/missions/{m['id']}")
    assert g.json()["status"] == "succeeded"


def test_non_greeting_still_launches_pipeline(client):
    r = client.post("/api/missions", json={"objective": "Analyze sample requirements and return plan"})
    assert r.status_code == 201
    assert r.json()["status"] in ("queued", "planning", "running")
    assert r.json()["tasks"] == []
