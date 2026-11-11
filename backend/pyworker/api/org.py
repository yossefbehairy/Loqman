"""Agent profiles, workflows and workflow executions."""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any, Dict

from backend.pyworker.appsettings import settings
from backend.pyworker.core import executions as ex
from backend.pyworker.core import profiles as profile_repo
from backend.pyworker.engine.events import event_hub
from backend.pyworker.engine.executor import (
    cancel_execution_rows,
    cancel_running_execution,
    execute_workflow,
)
from backend.pyworker.engine.state_machine import NodeState
from backend.pyworker.engine.validator import validate_workflow_graph
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


# -- workflows ----------------------------------------------------------------

def _wf(ctx, workflow_id: str) -> Dict[str, Any]:
    wf = ex.workflow_get(ctx.db, workflow_id)
    if not wf:
        raise api_error(404, "Workflow not found")
    return wf


@method("workflows.list")
async def workflows_list(ctx, params):
    check_keys(params, optional=("skip", "limit"))
    skip, limit = pagination(params, 100, 1000)
    return {"workflows": [ex.workflow_out(w) for w in ex.workflow_list(ctx.db, skip, limit)]}


@method("workflows.create")
async def workflows_create(ctx, params):
    check_keys(params, {"name"}, ("description", "definition_json"))
    wf = ex.workflow_create(ctx.db, req_str(params, "name", 1, 255), opt_str(params, "description"),
                            opt_str(params, "definition_json", default="{}"))
    return ex.workflow_out(wf)


@method("workflows.get")
async def workflows_get(ctx, params):
    check_keys(params, {"workflow_id"})
    return ex.workflow_out(_wf(ctx, req_str(params, "workflow_id", 1, 64)))


@method("workflows.update")
async def workflows_update(ctx, params):
    check_keys(params, {"workflow_id"}, ("name", "description", "definition_json"))
    wf = ex.workflow_update(ctx.db, req_str(params, "workflow_id", 1, 64), opt_str(params, "name"),
                            opt_str(params, "description"), opt_str(params, "definition_json"))
    if not wf:
        raise api_error(404, "Workflow not found")
    return ex.workflow_out(wf)


@method("workflows.delete")
async def workflows_delete(ctx, params):
    check_keys(params, {"workflow_id"})
    if not ex.workflow_delete(ctx.db, req_str(params, "workflow_id", 1, 64)):
        raise api_error(404, "Workflow not found")
    return {"deleted": True}


def _definition(wf: Dict[str, Any]):
    return json.loads(wf["definition_json"]) if isinstance(wf["definition_json"], str) else wf["definition_json"]


@method("workflows.validate")
async def workflows_validate(ctx, params):
    check_keys(params, {"workflow_id"})
    wf = _wf(ctx, req_str(params, "workflow_id", 1, 64))
    try:
        definition = _definition(wf)
    except Exception as e:
        return {"valid": False, "errors": [f"Malformed workflow definition JSON: {e}"],
                "warnings": [], "topological_order": []}
    return validate_workflow_graph(definition).to_dict()


async def _run_execution_task(ctx, execution_id, wf_def, initial_input, runtime):
    try:
        await execute_workflow(execution_id=execution_id, db=ctx.db, runtime=runtime,
                               workflow_definition=wf_def, initial_input=initial_input)
    except Exception:
        import logging
        logging.getLogger("agentos.executor").exception("background execution %s crashed", execution_id)


@method("workflows.run")
async def workflows_run(ctx, params):
    check_keys(params, {"workflow_id"}, ("input", "sync"))
    wf = _wf(ctx, req_str(params, "workflow_id", 1, 64))
    try:
        wf_def = _definition(wf)
    except Exception as e:
        raise api_error(400, f"Invalid definition JSON: {e}")
    validation = validate_workflow_graph(wf_def)
    if not validation.valid:
        raise api_error(400, {"message": "Workflow graph validation failed", "errors": validation.errors})
    input_data = params.get("input", "Start workflow execution")
    input_json = json.dumps(input_data, ensure_ascii=False) if not isinstance(input_data, str) else input_data
    wf_exec = ex.create_execution(ctx.db, wf["id"], input_json, wf["definition_json"])
    runtime = ctx.get_runtime()
    result, status_out = None, "pending"
    if opt_bool(params, "sync", False):
        result = await execute_workflow(execution_id=wf_exec["id"], db=ctx.db, runtime=runtime,
                                        workflow_definition=wf_def, initial_input=input_data)
        status_out = result.get("status", "completed")
    else:
        ctx.spawn(_run_execution_task(ctx, wf_exec["id"], wf_def, input_data, runtime), "workflow-run")
    return {"execution_id": wf_exec["id"], "workflow_id": wf["id"], "status": status_out,
            "topological_order": validation.topological_order, "result": result}


# -- executions ---------------------------------------------------------------

@method("executions.list")
async def executions_list(ctx, params):
    check_keys(params, optional=("skip", "limit"))
    skip, limit = pagination(params, 50, 1000)
    return {"executions": [ex.execution_out(ctx.db, r) for r in ex.list_executions(ctx.db, skip, limit)]}


def _exec(ctx, execution_id: str) -> Dict[str, Any]:
    row = ex.execution_get(ctx.db, execution_id)
    if not row:
        raise api_error(404, "Execution not found")
    return row


@method("executions.get")
async def executions_get(ctx, params):
    check_keys(params, {"execution_id"})
    return ex.execution_out(ctx.db, _exec(ctx, req_str(params, "execution_id", 1, 64)))


@method("executions.cancel")
async def executions_cancel(ctx, params):
    check_keys(params, {"execution_id"})
    execution_id = req_str(params, "execution_id", 1, 64)
    row = _exec(ctx, execution_id)
    running_nodes = [n["node_id"] for n in ex.execution_nodes(ctx.db, execution_id)
                     if n["status"] == NodeState.RUNNING.value]
    won = cancel_execution_rows(ctx.db, execution_id, event_json='{"reason": "User requested cancellation"}')
    if not won:
        current = ex.execution_get(ctx.db, execution_id)
        return {"status": current["status"],
                "message": f"Execution already in terminal state '{current['status']}'"}
    cancel_running_execution(execution_id)  # flag only after the cancel is committed
    runtime = ctx.get_runtime()

    async def abort_running():
        await asyncio.gather(*(runtime.abort_task(f"workflow-{execution_id}-{node_id}")
                               for node_id in running_nodes), return_exceptions=True)

    try:
        await asyncio.wait_for(abort_running(), timeout=5)
    except asyncio.TimeoutError:
        pass
    await event_hub.broadcast(execution_id, "execution.cancelled", {"reason": "User cancelled"})
    return {"status": "cancelled", "execution_id": execution_id, "message": "Execution marked as cancelled"}


@method("executions.messages")
async def executions_messages(ctx, params):
    check_keys(params, {"execution_id"})
    execution_id = req_str(params, "execution_id", 1, 64)
    _exec(ctx, execution_id)
    messages = ex.team_messages(ctx.db, execution_id)
    return {"execution_id": execution_id, "count": len(messages), "messages": messages}
