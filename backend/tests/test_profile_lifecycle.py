"""Profile deletion is permanent: the seeder never resurrects removed profiles."""
from backend.app.db.models import AgentProfile
from backend.app.db.repository import AgentProfileRepository, _LEGACY_GM_INSTRUCTIONS


def test_delete_profile_and_stays_deleted(client, db_session):
    r = client.delete("/api/agent-profiles/planner")
    assert r.status_code == 200, r.text
    assert r.json()["deleted"] == "planner"
    assert client.get("/api/agent-profiles/planner").status_code == 404
    assert client.delete("/api/agent-profiles/planner").status_code == 404
    assert client.delete("/api/agent-profiles/ghost-xyz").status_code == 404
    # Reseed must NOT bring it back.
    assert AgentProfileRepository(db_session).seed_defaults() == 0
    assert client.get("/api/agent-profiles/planner").status_code == 404
    assert db_session.query(AgentProfile).count() == 9


def test_fresh_db_still_seeds_defaults(db_session):
    assert db_session.query(AgentProfile).count() == 0
    assert AgentProfileRepository(db_session).seed_defaults() == 10
    assert db_session.query(AgentProfile).count() == 10


def test_gm_upgrade_still_applies_on_nonempty_db(db_session):
    repo = AgentProfileRepository(db_session)
    repo.seed_defaults()
    gm = repo.get_by_id("custom")
    gm.instructions = _LEGACY_GM_INSTRUCTIONS
    db_session.commit()
    assert repo.seed_defaults() == 0
    assert repo.get_by_id("custom").instructions != _LEGACY_GM_INSTRUCTIONS
