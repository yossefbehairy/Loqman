"""Agent profiles: CRUD, aliases, default org, upgrades, engine validation."""

import json

import pytest

from backend.pyworker.core import profiles as repo
from backend.pyworker.appsettings import settings


@pytest.mark.asyncio
async def test_seeded_org_and_alias_lookup(api):
    profiles = (await api("profiles.list"))["profiles"]
    assert {p["id"] for p in profiles} >= {"custom", "planner", "researcher", "analyst", "coder", "reviewer"}
    assert len(profiles) == 10
    assert [p["name"] for p in profiles] == sorted(p["name"] for p in profiles)  # name ASC

    by_id = await api("profiles.get", profile_id="planner")
    by_alias = await api("profiles.get", profile_id="dr_sara")
    assert by_id["id"] == by_alias["id"] == "planner" and "Sara" in by_id["name"]
    assert (await api("profiles.get", profile_id="loqman"))["id"] == "custom"
    err = await api.fail("profiles.get", profile_id="nope_999")
    assert err.status == 404 and "not found" in err.detail.lower()


@pytest.mark.asyncio
async def test_update_contract_persists_and_ignores_null(api):
    contract = {"department": "planning", "timeout": 240, "max_retries": 4}
    out = await api("profiles.update", profile_id="planner", name="Dr. Sara X", default_variant_id="max",
                    task_depth="Deep", settings_json=json.dumps(contract), description=None)
    assert out["name"] == "Dr. Sara X" and out["default_variant_id"] == "max" and out["task_depth"] == "Deep"
    got = await api("profiles.get", profile_id="planner")
    assert json.loads(got["settings_json"])["timeout"] == 240
    assert got["description"]  # null ignored: description untouched
    assert got["updated_at"] >= got["created_at"]


@pytest.mark.asyncio
async def test_update_validation(api):
    assert (await api.fail("profiles.update", profile_id="planner", settings_json="{not json")).status == 422
    assert (await api.fail("profiles.update", profile_id="planner", engine="gemini")).status == 422
    assert (await api.fail("profiles.update", profile_id="planner", bogus=1)).status == 422
    assert (await api.fail("profiles.update", profile_id="missing", name="x")).status == 404
    blank = await api("profiles.update", profile_id="planner", settings_json="  ")
    assert blank["settings_json"] == "{}"


@pytest.mark.asyncio
async def test_engine_set_and_clear(api):
    out = await api("profiles.update", profile_id="coder", engine="codex")
    assert out["engine"] == "codex"
    out = await api("profiles.update", profile_id="coder", engine=None)  # explicit null clears
    assert out["engine"] is None
    # "mock" is only a valid profile engine in explicit mock mode
    assert (await api("profiles.update", profile_id="coder", engine="mock"))["engine"] == "mock"


@pytest.mark.asyncio
async def test_mock_engine_rejected_outside_mock_mode(make_app):
    from .helpers import Api
    real = Api(make_app(mode="real"))
    assert (await real.fail("profiles.update", profile_id="coder", engine="mock")).status == 422


@pytest.mark.asyncio
async def test_create_delete_and_no_resurrection(api, tmp_path):
    created = await api("profiles.create", name="Zed", role_type="coder", instructions="Do things.")
    assert created["id"].startswith("custom-") and created["default_model_id"]
    assert created["task_depth"] == "Standard"
    dup = await api.fail("profiles.create", id=created["id"], name="Z2", role_type="coder", instructions="x")
    assert dup.status == 409
    assert (await api("profiles.delete", profile_id=created["id"]))["deleted"] == created["id"]
    assert (await api.fail("profiles.get", profile_id=created["id"])).status == 404
    # deleting a seeded profile is permanent: re-seeding a non-empty table never revives it
    await api("profiles.delete", profile_id="translator")
    assert repo.seed_defaults(api.db) == 0
    assert (await api.fail("profiles.get", profile_id="translator")).status == 404


@pytest.mark.asyncio
async def test_create_requires_fields(api):
    err = await api.fail("profiles.create", name="x")
    assert err.status == 422 and "role_type" in err.message


def test_default_org_structure(make_app):
    app = make_app(specialists=False)
    profiles = {p["id"]: p for p in repo.list_all(app.ctx.db)}
    assert set(profiles) == {"custom"}
    ceo = profiles["custom"]
    assert ceo["name"] == "Loqman"
    s = json.loads(ceo["settings_json"])
    assert s["team"] == "executive"
    contract = s["contract"]
    assert contract["mission"] and contract["responsibilities"] and contract["deliverables"] and contract["handoff"]


def test_legacy_ceo_upgraded_and_user_customizations_survive(make_app):
    app = make_app(specialists=False)
    db = app.ctx.db
    db.update("agent_profiles", {"name": "General Manager (المدير العام)",
                                 "instructions": "You are the General Manager (المدير العام) — warm."},
              "id='custom'")
    assert repo.seed_defaults(db) == 0  # non-empty: upgrade only
    ceo = repo.get_by_id(db, "custom")
    assert ceo["name"] == "Loqman" and ceo["instructions"].startswith("You are Loqman")


def test_only_the_ceo_ships_and_old_specialists_are_pruned(make_app):
    app = make_app(specialists=False)
    db = app.ctx.db
    assert [p["id"] for p in repo.list_all(db)] == ["custom"]
    from .legacy_org import seed_legacy_specialists
    seed_legacy_specialists(db)
    mine = repo.create(db, {"id": "custom-mine", "name": "Mine", "role_type": "custom", "instructions": "x"})
    assert len(repo.list_all(db)) == 11
    assert repo.seed_defaults(db) == 0  # an older database: the shipped specialists are dropped
    assert {p["id"] for p in repo.list_all(db)} == {"custom", mine["id"]}


def test_user_renamed_ceo_is_left_alone(make_app):
    app = make_app()
    db = app.ctx.db
    custom_instr = "You are my very own CEO persona."
    settings_json = json.dumps({"arabic_name": "رئيسي", "title": "Boss"}, ensure_ascii=False)
    db.update("agent_profiles", {"name": "Boss", "instructions": custom_instr, "settings_json": settings_json},
              "id='custom'")
    repo.seed_defaults(db)
    ceo = repo.get_by_id(db, "custom")
    assert ceo["name"] == "Boss" and ceo["instructions"] == custom_instr
