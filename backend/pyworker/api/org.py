"""Agent profiles."""

from __future__ import annotations

import json
import uuid
from typing import Any, Dict

from backend.pyworker.appsettings import settings
from backend.pyworker.core import profiles as profile_repo
from backend.pyworker.errors import api_error
from backend.pyworker.validate import (
    bad, check_keys, opt_bool, opt_int, opt_str, pagination, req_str,
)

from .registry import method

# -- profiles -----------------------------------------------------------------

_PROFILE_FIELDS = ("name", "role_type", "description", "instructions", "input_schema_json",
                   "output_schema_json", "default_model_id", "default_variant_id", "task_depth",
                   "settings_json", "engine")


def validate_profile_engine(value):
    """None/"" = server default. "mock" is accepted only in explicit mock mode."""
    if value is None or value == "":
        return None
    allowed = profile_repo.PROFILE_ENGINES + (("mock",) if settings.RUNTIME_MODE == "mock" else ())
    if not isinstance(value, str) or value not in allowed:
        raise bad(f"engine must be one of {list(allowed)} or null")
    return value


def _settings_json(params: dict):
    if "settings_json" not in params or params["settings_json"] is None:
        return params.get("settings_json")
    v = params["settings_json"]
    if not isinstance(v, str):
        raise bad("'settings_json' must be a string.")
    if not v.strip():
        return "{}"
    try:
        json.loads(v)
    except Exception as exc:
        raise bad(f"settings_json must be valid JSON: {exc}")
    return v


@method("profiles.list")
async def profiles_list(ctx, params):
    check_keys(params)
    return {"profiles": [profile_repo.profile_out(p) for p in profile_repo.list_all(ctx.db)]}


@method("profiles.get")
async def profiles_get(ctx, params):
    check_keys(params, {"profile_id"})
    pid = req_str(params, "profile_id", 1, 128)
    p = profile_repo.get_by_id(ctx.db, pid)
    if not p:
        raise api_error(404, f"Agent profile '{pid}' not found")
    return profile_repo.profile_out(p)


def _profile_data(params: dict, creating: bool) -> Dict[str, Any]:
    data: Dict[str, Any] = {}
    if "name" in params or creating:
        data["name"] = req_str(params, "name", 1, 255) if (creating or params.get("name") is not None) else None
    if "role_type" in params or creating:
        data["role_type"] = req_str(params, "role_type", 1, 64) if (creating or params.get("role_type") is not None) else None
    if "instructions" in params or creating:
        data["instructions"] = req_str(params, "instructions", 1) if (creating or params.get("instructions") is not None) else None
    for key in ("description", "input_schema_json", "output_schema_json", "default_model_id",
                "default_variant_id", "task_depth"):
        if key in params:
            data[key] = opt_str(params, key)
    if "settings_json" in params:
        data["settings_json"] = _settings_json(params)
    if "engine" in params:
        data["engine"] = validate_profile_engine(params["engine"])
    return data


@method("profiles.create")
async def profiles_create(ctx, params):
    check_keys(params, {"name", "role_type", "instructions"},
               ("id", *[f for f in _PROFILE_FIELDS if f not in ("name", "role_type", "instructions")]))
    data = _profile_data(params, creating=True)
    data["id"] = opt_str(params, "id", 1, 64) or f"custom-{uuid.uuid4().hex[:8]}"
    if profile_repo.get_by_id(ctx.db, data["id"]):
        raise api_error(409, f"Agent profile with id '{data['id']}' already exists")
    return profile_repo.profile_out(profile_repo.create(ctx.db, data))


@method("profiles.update")
async def profiles_update(ctx, params):
    check_keys(params, {"profile_id"}, _PROFILE_FIELDS)
    pid = req_str(params, "profile_id", 1, 128)
    existing = profile_repo.get_by_id(ctx.db, pid)
    if not existing:
        raise api_error(404, f"Agent profile '{pid}' not found")
    changes = _profile_data({k: v for k, v in params.items() if k != "profile_id"}, creating=False)
    return profile_repo.profile_out(profile_repo.update(ctx.db, existing["id"], changes))


@method("profiles.delete")
async def profiles_delete(ctx, params):
    check_keys(params, {"profile_id"})
    pid = req_str(params, "profile_id", 1, 128)
    existing = profile_repo.get_by_id(ctx.db, pid)
    if not existing:
        raise api_error(404, f"Agent profile '{pid}' not found")
    profile_repo.delete(ctx.db, existing["id"])
    return {"deleted": existing["id"]}
