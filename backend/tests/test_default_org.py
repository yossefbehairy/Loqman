"""Default Loqman organization: CEO + Compass (planning) + Forge (execution)."""

import json

from backend.app.db.repository import AgentProfileRepository


def _settings(profile):
    return json.loads(profile.settings_json or "{}")


def test_default_org_has_loqman_ceo_and_two_teams(db_session):
    repo = AgentProfileRepository(db_session)
    assert repo.seed_defaults() == 10

    profiles = {p.id: p for p in repo.list_all()}

    ceo = profiles["custom"]
    assert ceo.name == "Loqman"
    ceo_settings = _settings(ceo)
    assert ceo_settings["title"] == "الرئيس التنفيذي · CEO"
    assert ceo_settings["team"] == "executive"

    by_team = {}
    for pid, profile in profiles.items():
        team = _settings(profile).get("team")
        by_team.setdefault(team, set()).add(pid)

    assert by_team["planning"] == {"planner", "researcher", "data_extractor", "report_writer"}
    assert by_team["execution"] == {"analyst", "coder", "reviewer", "summarizer", "translator"}

    for profile in profiles.values():
        contract = _settings(profile)["contract"]
        assert contract["mission"]
        assert contract["responsibilities"]
        assert contract["deliverables"]
        assert contract["handoff"]

    # The conversational alias used by the UI/tools resolves to the CEO.
    assert repo.get_by_id("loqman").id == "custom"


def test_legacy_ceo_is_upgraded_and_user_customizations_survive(db_session):
    repo = AgentProfileRepository(db_session)
    repo.seed_defaults()

    # Simulate a pre-Loqman catalog: legacy CEO identity + a planner profile
    # whose settings predate the team metadata and carry a custom timeout.
    ceo = repo.get_by_id("custom")
    ceo.name = "General Manager (المدير العام)"
    ceo.instructions = (
        "You are the General Manager (المدير العام) — a warm, natural conversationalist, "
        "not a bureaucrat."
    )
    planner = repo.get_by_id("planner")
    legacy_planner_settings = _settings(planner)
    for key in ("team", "team_name", "team_name_en", "reports_to", "contract"):
        legacy_planner_settings.pop(key, None)
    legacy_planner_settings["timeout"] = 999
    planner.settings_json = json.dumps(legacy_planner_settings, ensure_ascii=False)
    db_session.commit()

    # Upgrade runs against the non-empty catalog without reseeding.
    assert repo.seed_defaults() == 0

    ceo = repo.get_by_id("custom")
    assert "Loqman" in ceo.name
    assert ceo.instructions.startswith("You are Loqman")
    assert _settings(ceo)["arabic_name"] == "لقمان"

    planner = repo.get_by_id("planner")
    planner_settings = _settings(planner)
    assert planner_settings["timeout"] == 999  # user customization preserved
    assert planner_settings["team"] == "planning"
    assert planner_settings["team_name"] == "Compass"
    assert planner_settings["contract"]["responsibilities"]


def test_bilingual_v1_org_is_renamed_in_place(db_session):
    """A catalog seeded by the first Loqman release moves to English names."""
    repo = AgentProfileRepository(db_session)
    repo.seed_defaults()

    legacy_names = {
        "custom": "Loqman (لقمان)",
        "planner": "Dr. Sara (د. سارة)",
        "researcher": "Khaled (خالد)",
        "data_extractor": "Layla (ليلى)",
        "report_writer": "Nour (نور)",
        "analyst": "Eng. Tarek (م. طارق)",
        "coder": "Ahmed (أحمد)",
        "reviewer": "Prof. Yasmin (أ. ياسمين)",
        "summarizer": "Omar (عمر)",
        "translator": "Mona (منى)",
    }
    team_names = {
        "custom": "القيادة التنفيذية",
        "planner": "فريق البوصلة",
        "researcher": "فريق البوصلة",
        "data_extractor": "فريق البوصلة",
        "report_writer": "فريق البوصلة",
        "analyst": "فريق الإنجاز",
        "coder": "فريق الإنجاز",
        "reviewer": "فريق الإنجاز",
        "summarizer": "فريق الإنجاز",
        "translator": "فريق الإنجاز",
    }
    for pid, legacy_name in legacy_names.items():
        profile = repo.get_by_id(pid)
        profile.name = legacy_name
        settings = _settings(profile)
        settings["team_name"] = team_names[pid]
        settings["contract"]["handoff"] = (
            settings["contract"]["handoff"]
            .replace("Compass", "فريق البوصلة")
            .replace("Forge", "فريق الإنجاز")
        )
        profile.settings_json = json.dumps(settings, ensure_ascii=False)

    ceo = repo.get_by_id("custom")
    ceo.instructions = ceo.instructions.replace("Compass", "فريق البوصلة").replace(
        "Forge", "فريق الإنجاز"
    )
    db_session.commit()

    assert repo.seed_defaults() == 0

    assert repo.get_by_id("custom").name == "Loqman"
    assert repo.get_by_id("planner").name == "Dr. Sara"
    assert repo.get_by_id("analyst").name == "Eng. Tarek"
    assert repo.get_by_id("reviewer").name == "Prof. Yasmin"
    assert _settings(repo.get_by_id("planner"))["team_name"] == "Compass"
    assert _settings(repo.get_by_id("analyst"))["team_name"] == "Forge"
    assert "فريق" not in _settings(repo.get_by_id("planner"))["contract"]["handoff"]
    assert _settings(repo.get_by_id("custom"))["arabic_name"] == "لقمان"
    assert "Compass" in repo.get_by_id("custom").instructions


def test_existing_names_are_not_renamed(db_session):
    repo = AgentProfileRepository(db_session)
    repo.seed_defaults()

    planner = repo.get_by_id("planner")
    planner.name = "Sara — Chief of Staff"
    db_session.commit()

    repo.seed_defaults()
    assert repo.get_by_id("planner").name == "Sara — Chief of Staff"
