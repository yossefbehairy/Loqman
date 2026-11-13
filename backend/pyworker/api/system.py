"""health, runtime/model catalog, engines, usage ledger and backup methods."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Dict

from backend.pyworker.appsettings import settings
from backend.pyworker.core import backup as backup_mod
from backend.pyworker.db.core import iso
from backend.pyworker.errors import api_error
from backend.pyworker.runtime import engine_discovery
from backend.pyworker.runtime.engine_registry import ENGINE_IDS
from backend.pyworker.runtime.errors import RuntimeErrorBase
from backend.pyworker.validate import check_keys, opt_bool, opt_int, opt_str, req_str

from .registry import method


def _model_out(m) -> Dict[str, Any]:
    return {"id": m.id, "name": m.name, "provider_id": m.provider_id, "input_cost": m.cost.input,
            "output_cost": m.cost.output, "variants": m.variants, "is_free": m.is_free, "status": m.status}


@method("app.info")
async def app_info(ctx, params):
    check_keys(params)
    return {"app": settings.APP_NAME, "version": settings.APP_VERSION, "status": "online"}


@method("health")
async def health(ctx, params):
    check_keys(params)
    ctx.db.scalar("SELECT 1")
    return {"status": "ok", "app_name": settings.APP_NAME, "version": settings.APP_VERSION}


@method("runtime.health")
async def runtime_health(ctx, params):
    check_keys(params)
    h = await ctx.get_runtime().check_health()
    return h.to_dict()


@method("models.free")
async def models_free(ctx, params):
    check_keys(params)
    models = await ctx.get_runtime().list_available_models(free_only=True)
    return {"models": [_model_out(m) for m in models]}


@method("models.refresh")
async def models_refresh(ctx, params):
    check_keys(params)
    models = await ctx.get_runtime().list_available_models(free_only=True)
    return {"models": [_model_out(m) for m in models]}


# -- engines ---------------------------------------------------------------

@method("engines.capabilities")
async def engines_capabilities(ctx, params):
    check_keys(params)
    base = {"conversation_runs": True, "streaming_text": False, "tool_approvals": False,
            "cancellation": "best_effort_remote_unconfirmed", "workspace_restriction": False}
    return {
        "opencode": {**base, "model_policy": "verified_free_only"},
        "claude-code": {**base, "model_policy": "subscription_or_account", "tool_approvals": True,
                        "workspace_restriction": "scoped_mcp_tools",
                        "workspace_tools": ["workspace_read", "workspace_write"],
                        "browser_tools": True, "computer_tools": "selected_windows_window",
                        "write_policy": "new_workspace_root_reports_only"},
        "codex": {**base, "model_policy": "subscription_or_account"},
        "antigravity": {**base, "model_policy": "subscription_or_account",
                        "permission_policy": "plan_mode_and_deny_all_requests"},
        "mock": {"conversation_runs": settings.RUNTIME_MODE == "mock", "streaming_text": False,
                 "tool_approvals": False, "cancellation": "local", "workspace_restriction": False,
                 "model_policy": "simulated"},
    }


@method("engines.status")
async def engines_status(ctx, params):
    check_keys(params)
    statuses = await asyncio.to_thread(engine_discovery.discover_engines)
    return {"engines": [vars(s) for s in statuses]}


@method("computer.windows")
async def computer_windows(ctx, params):
    check_keys(params)
    from backend.pyworker.core.computer import windows
    return {"windows": await asyncio.to_thread(windows)}


def _engine_id(params: dict) -> str:
    engine_id = req_str(params, "engine_id", 1, 64)
    if engine_id not in ENGINE_IDS:
        raise api_error(404, "Unknown engine")
    return engine_id


@method("engines.setup")
async def engines_setup(ctx, params):
    check_keys(params, {"engine_id"})
    engine_id = _engine_id(params)
    commands = {"opencode": ["opencode", "auth", "login"], "claude-code": ["claude", "auth", "login"],
                "codex": ["codex", "login"], "antigravity": ["agy", "acp", "--login"]}
    command = commands.get(engine_id)
    return {
        "engine_id": engine_id, "login_method": "external_cli" if command else "not_available",
        "command": command,
        "instructions": ("Run this command in your terminal and complete the official sign-in, then check the connection."
                         if command else "Sign in through the installed Antigravity application. A separate CLI login command has not been verified."),
        "credentials_handled_by": "official_cli",
    }


@method("engines.check")
async def engines_check(ctx, params):
    check_keys(params, {"engine_id"})
    engine_id = _engine_id(params)
    runtime = ctx.engine_factory(engine_id)
    spec = next(s for s in engine_discovery.ENGINE_SPECS if s.engine_id == engine_id)
    discovery = await asyncio.to_thread(engine_discovery.probe_engine, spec)
    health_obj, error_code = None, None
    if discovery.install_state == "installed":
        try:
            health_obj = await asyncio.wait_for(runtime.check_health(), timeout=45)
        except asyncio.TimeoutError:
            error_code = "timeout"
        except RuntimeErrorBase as exc:
            error_code = exc.code
        except Exception:
            error_code = "health_check_failed"
    return {"engine_id": engine_id, "checked_at": datetime.now(timezone.utc).isoformat(),
            "discovery": vars(discovery), "runtime": health_obj.to_dict() if health_obj else None,
            "error_code": error_code, "generation_verified": False, "billing_state": "unknown"}


@method("engines.models")
async def engines_models(ctx, params):
    check_keys(params, {"engine_id"})
    runtime = ctx.engine_factory(_engine_id(params))
    try:
        models = await runtime.list_available_models(free_only=False)
    except RuntimeErrorBase as exc:
        raise api_error(502, f"Engine catalog failed ({exc.code}).")
    return {"models": [{"id": m.id, "name": m.name, "provider_id": m.provider_id, "status": m.status,
                        "is_free": m.is_free, "variants": m.variants} for m in models]}


# -- usage ledger ----------------------------------------------------------

def _usage_event_out(r: Dict[str, Any]) -> Dict[str, Any]:
    return {"id": r["id"], "run_id": r["run_id"], "created_at": iso(r["created_at"]), "kind": r["kind"],
            "agent_id": r["agent_id"], "model_id": r["model_id"], "provider_id": r["provider_id"],
            "variant_id": r["variant_id"], "session_id": r["session_id"], "duration_ms": r["duration_ms"] or 0,
            "tokens_in": r["tokens_in"], "tokens_out": r["tokens_out"],
            "tokens_cache_read": r["tokens_cache_read"], "success": bool(r["success"]),
            "error_kind": r["error_kind"], "error_detail": r["error_detail"]}


_AGG = ("COUNT(id) AS events, COALESCE(SUM(duration_ms),0) AS duration, COALESCE(SUM(tokens_in),0) AS tin, "
        "COALESCE(SUM(tokens_out),0) AS tout, COALESCE(SUM(success),0) AS succeeded, "
        "COALESCE(SUM(tokens_cache_read),0) AS tcache, "
        "COALESCE(SUM(CASE WHEN tokens_in IS NULL OR tokens_out IS NULL THEN 1 ELSE 0 END),0) AS unknown")


def _agg(r: Dict[str, Any]) -> Dict[str, Any]:
    events, duration, succeeded = int(r["events"] or 0), int(r["duration"] or 0), int(r["succeeded"] or 0)
    return {"events": events, "succeeded": succeeded, "failed": events - succeeded,
            "total_duration_ms": duration, "avg_duration_ms": (duration // events) if events else 0,
            "tokens_in": int(r["tin"] or 0), "tokens_out": int(r["tout"] or 0),
            "tokens_cache_read": int(r["tcache"] or 0), "token_usage_unknown_events": int(r["unknown"] or 0)}


@method("usage.events")
async def usage_events(ctx, params):
    check_keys(params, optional=("limit", "kind", "model_id", "success"))
    limit = opt_int(params, "limit", 100, 1, 500)
    kind, model_id = opt_str(params, "kind"), opt_str(params, "model_id")
    success = opt_bool(params, "success")
    where, args = [], []
    if kind:
        where.append("kind=?"); args.append(kind)
    if model_id:
        where.append("model_id=?"); args.append(model_id)
    if success is not None:
        where.append("success=?"); args.append(1 if success else 0)
    sql = "SELECT * FROM usage_events" + (" WHERE " + " AND ".join(where) if where else "")
    rows = ctx.db.all(sql + " ORDER BY created_at DESC, id DESC LIMIT ?", (*args, limit))
    return {"events": [_usage_event_out(r) for r in rows]}


@method("usage.summary")
async def usage_summary(ctx, params):
    check_keys(params)

    def groups(col: str):
        rows = ctx.db.all(f"SELECT {col} AS key, {_AGG} FROM usage_events GROUP BY {col} "
                          "ORDER BY COUNT(id) DESC LIMIT 25")
        return [{"key": r["key"] or "unknown", **_agg(r)} for r in rows]

    totals = ctx.db.one(f"SELECT {_AGG} FROM usage_events")
    return {"totals": _agg(totals), "by_model": groups("model_id"), "by_kind": groups("kind")}


# -- backup ----------------------------------------------------------------

@method("backup.create")
async def backup_create(ctx, params):
    """Create a consistent online backup in a private temp dir; the caller
    (Electron) moves/streams it and then calls ``backup.cleanup``."""
    check_keys(params)
    try:
        path, name = backup_mod.create_backup(ctx.db)
    except backup_mod.BackupBusy:
        raise api_error(409, "A backup is already in progress")
    except backup_mod.BackupTooLarge:
        raise api_error(413, "Database is too large to export")
    except backup_mod.BackupError:
        raise api_error(500, "Backup failed")
    return {"path": str(path), "name": name, "size": path.stat().st_size,
            "media_type": "application/vnd.sqlite3"}


@method("backup.cleanup")
async def backup_cleanup(ctx, params):
    check_keys(params, {"path"})
    from pathlib import Path
    backup_mod.cleanup_backup(Path(req_str(params, "path", 1, 4096)))
    return {"cleaned": True}
