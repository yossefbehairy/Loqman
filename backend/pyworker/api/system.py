"""health, model catalog, engines, usage ledger and backup methods."""

from __future__ import annotations

import asyncio
import subprocess
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict

from backend.pyworker.appsettings import settings
from backend.pyworker.core import backup as backup_mod
from backend.pyworker.db.core import iso
from backend.pyworker.errors import api_error
from backend.pyworker.runtime import engine_discovery
from backend.pyworker.runtime.engine_registry import ENGINE_IDS
from backend.pyworker.runtime.base import ExecuteAgentTask
from backend.pyworker.runtime.errors import RuntimeErrorBase
from backend.pyworker.runtime.providers import PROVIDERS, get_provider
from backend.pyworker.validate import check_keys, opt_bool, opt_int, opt_str, req_enum, req_str

from .registry import method


def _model_out(m) -> Dict[str, Any]:
    return {"id": m.id, "name": m.name, "provider_id": m.provider_id, "input_cost": m.cost.input,
            "output_cost": m.cost.output, "variants": m.variants, "is_free": m.is_free, "status": m.status}


@method("health")
async def health(ctx, params):
    check_keys(params)
    ctx.db.scalar("SELECT 1")
    return {"status": "ok", "app_name": settings.APP_NAME, "version": settings.APP_VERSION}


@method("models.free")
async def models_free(ctx, params):
    check_keys(params)
    models = await ctx.get_runtime().list_available_models(free_only=True)
    return {"models": [_model_out(m) for m in models]}


# -- engines ---------------------------------------------------------------

@method("engines.capabilities")
async def engines_capabilities(ctx, params):
    check_keys(params)
    return {
        **{p.engine_id: dict(p.capabilities) for p in PROVIDERS},
        "mock": {"conversation_runs": settings.RUNTIME_MODE == "mock", "streaming_text": False,
                 "tool_approvals": False, "cancellation": "local", "workspace_restriction": False,
                 "model_policy": "simulated"},
    }


ENGINE_STATUS_TTL_SECONDS = 30
ENGINE_TEST_PROMPT = "Reply with exactly: OK"
ENGINE_TEST_TIMEOUT_SECONDS = 60


async def _probe_engines(cache: Dict[str, Any], previous, seq: int):
    """One discovery run. It waits for the run before it, so runs are serialised and a slower, older
    probe can never land after (and replace) a newer one; ``seq`` additionally guards the cache write."""
    if previous is not None:
        await asyncio.gather(previous, return_exceptions=True)
    statuses = await asyncio.to_thread(engine_discovery.discover_engines)
    if seq > cache.get("seq", 0):
        cache.update(seq=seq, at=time.monotonic(), statuses=statuses, checked_at=datetime.now(timezone.utc).isoformat())
    return cache["statuses"], cache["checked_at"]


async def _discovery(ctx, force: bool = False):
    """Cached discovery (per app instance): ``(statuses, checked_at_iso)``.

    Concurrent callers share one probe: any caller joins a probe already running, except that a
    forced call does not join one that was started without ``force`` (it queues its own, run after it).
    """
    cache = ctx.engine_cache
    if not force and "at" in cache and time.monotonic() - cache["at"] < ENGINE_STATUS_TTL_SECONDS:
        return cache["statuses"], cache["checked_at"]
    running = cache.get("running")
    if running is None or (force and not running["forced"]):
        cache["next_seq"] = seq = cache.get("next_seq", 0) + 1
        previous = running["task"] if running else None
        task = asyncio.ensure_future(_probe_engines(cache, previous, seq))
        running = cache["running"] = {"task": task, "forced": force}

        def _done(_t, entry=running):
            if cache.get("running") is entry:
                cache.pop("running", None)

        task.add_done_callback(_done)
    return await asyncio.shield(running["task"])


def _engine_out(s, checked_at: str) -> Dict[str, Any]:
    p = get_provider(s.engine_id)
    return {**vars(s), "state": engine_discovery.engine_state(s.install_state, s.auth_state),
            "title": p.title, "vendor": p.vendor, "checked_at": checked_at}


@method("engines.status")
async def engines_status(ctx, params):
    check_keys(params, optional=("force",))
    statuses, checked_at = await _discovery(ctx, bool(opt_bool(params, "force", False)))
    return {"engines": [_engine_out(s, checked_at) for s in statuses]}


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


_SETUP_TEXT = {
    "not_installed": "Install the {title} CLI first, then sign in with the official flow and check the connection.",
    "needs_login": "Run the sign-in command and complete the official sign-in, then check the connection.",
    "unverified": "{title} is installed but sign-in could not be verified. Run the sign-in command if needed, then check the connection.",
    "error": "The {title} CLI could not be checked. Make sure it is installed correctly, then check again.",
    "ready": "No setup is needed. {title} is installed and signed in.",
}


@method("engines.setup")
async def engines_setup(ctx, params):
    check_keys(params, {"engine_id"})
    engine_id = _engine_id(params)
    p = get_provider(engine_id)
    statuses, _ = await _discovery(ctx)
    found = next((s for s in statuses if s.engine_id == engine_id), None)
    state = engine_discovery.engine_state(found.install_state, found.auth_state) if found else "error"
    command = list(p.login_command)
    return {
        "engine_id": engine_id, "state": state, "login_method": "external_cli",
        "command": command,
        "install": {"command": list(p.install_command) if p.install_command else None, "url": p.install_url},
        "login": {"command": command}, "docs_url": p.docs_url,
        "instructions": _SETUP_TEXT[state].format(title=p.title),
        "credentials_handled_by": "official_cli",
    }


def _is_windows() -> bool:
    return sys.platform == "win32"


def _spawn_terminal(argv: list) -> None:
    """Open a visible console window running ``argv`` (kept open afterwards)."""
    subprocess.Popen(["cmd.exe", "/k", *argv], creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0))


@method("engines.open_setup")
async def engines_open_setup(ctx, params):
    check_keys(params, {"engine_id", "step"})
    engine_id = _engine_id(params)
    step = req_enum(params, "step", ("install", "login"))
    p = get_provider(engine_id)
    command = p.install_command if step == "install" else p.login_command
    if settings.RUNTIME_MODE == "mock":
        return {"launched": False, "reason": "Not available in mock mode."}
    if not _is_windows():
        return {"launched": False, "reason": "Opening a terminal is only supported on Windows."}
    if not command:
        return {"launched": False, "reason": f"{p.title} has no {step} command; use the download page instead."}
    try:
        await asyncio.to_thread(_spawn_terminal, list(command))
    except OSError:
        return {"launched": False, "reason": "The terminal window could not be opened."}
    return {"launched": True, "reason": None}


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


def _free_only(engine_id: str) -> bool:
    """True for engines whose runtime executes only verified-free models (capability model_policy)."""
    p = get_provider(engine_id)
    return bool(p) and p.capabilities.get("model_policy") == "verified_free_only"


@method("engines.models")
async def engines_models(ctx, params):
    check_keys(params, {"engine_id"})
    engine_id = _engine_id(params)
    runtime = ctx.engine_factory(engine_id)
    try:
        models = await runtime.list_available_models(free_only=_free_only(engine_id))
    except RuntimeErrorBase as exc:
        raise api_error(502, f"Engine catalog failed ({exc.code}).")
    return {"models": [{"id": m.id, "name": m.name, "provider_id": m.provider_id, "status": m.status,
                        "is_free": m.is_free, "variants": m.variants} for m in models]}


_TEST_ERRORS = {
    "offline": "The engine could not be reached.", "auth": "Sign-in is required or has expired.",
    "rate_limited": "Rate limit or quota reached.", "model_unavailable": "This model is not available.",
    "timeout": "No reply arrived in time.", "cancelled": "The test was cancelled.",
}


@method("engines.test")
async def engines_test(ctx, params):
    """One real generation with a fixed prompt; the client never supplies a prompt."""
    check_keys(params, {"engine_id"}, ("model_id",))
    engine_id = _engine_id(params)
    model_id = opt_str(params, "model_id", 1, 200)
    started = time.monotonic()
    out = {"engine_id": engine_id, "model_id": model_id, "ok": False, "duration_ms": 0,
           "reply_preview": "", "error_code": None, "error_message": None}

    def fail(code: str, message: str):
        out.update(ok=False, error_code=code, error_message=message,
                   duration_ms=int((time.monotonic() - started) * 1000))
        return out

    provider_id = engine_id
    if settings.RUNTIME_MODE == "mock":
        return fail("mock_mode", "Engine generation tests are unavailable in mock mode.")
    try:
        runtime = ctx.engine_factory(engine_id)
        free_only = _free_only(engine_id)
        models = await asyncio.wait_for(runtime.list_available_models(free_only=free_only), ENGINE_TEST_TIMEOUT_SECONDS)
        chosen = next((m for m in models if m.id == model_id), None) if model_id else None
        if not model_id and models:
            patterns = get_provider(engine_id).test_model_patterns
            chosen = next((m for pattern in patterns for m in models if pattern in m.id.lower()), models[-1])
        if free_only and model_id and chosen is None:
            return fail("model_unavailable", _TEST_ERRORS["model_unavailable"])
        if chosen is not None:
            model_id, provider_id = chosen.id, chosen.provider_id
        elif not model_id:
            return fail("no_models", "The engine reported no models to test.")
        out["model_id"] = model_id
        task = ExecuteAgentTask(prompt=ENGINE_TEST_PROMPT, model_id=model_id, provider_id=provider_id,
                                session_title="agentos-engine-test",
                                timeout=max(1, int(ENGINE_TEST_TIMEOUT_SECONDS)))
        result = await asyncio.wait_for(runtime.execute_task(task), ENGINE_TEST_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        return fail("timeout", _TEST_ERRORS["timeout"])
    except RuntimeErrorBase as exc:
        return fail(exc.code, _TEST_ERRORS.get(exc.code, "The engine returned an error."))
    except Exception:
        return fail("test_failed", "The test could not be completed.")
    preview = " ".join((result.text or "").split())[:80]
    if not preview:
        return fail("empty_reply", "The engine returned an empty reply.")
    out.update(ok=True, reply_preview=preview, duration_ms=int((time.monotonic() - started) * 1000))
    return out


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

    def groups(col: str, source: str = "usage_events", args: tuple = ()):
        rows = ctx.db.all(f"SELECT {col} AS key, {_AGG} FROM {source} GROUP BY {col} "
                          "ORDER BY COUNT(id) DESC LIMIT 25", args)
        return [{"key": r["key"] or "unknown", **_agg(r)} for r in rows]

    # provider_id -> engine_id via the catalog; anything else is "other"
    pairs = [(pid, p.engine_id) for p in PROVIDERS for pid in p.provider_ids]
    case = "CASE provider_id " + "".join("WHEN ? THEN ? " for _ in pairs) + "ELSE 'other' END"
    by_engine = groups("engine_key", f"(SELECT *, {case} AS engine_key FROM usage_events)",
                       tuple(v for pair in pairs for v in pair))

    totals = ctx.db.one(f"SELECT {_AGG} FROM usage_events")
    return {"totals": _agg(totals), "by_model": groups("model_id"), "by_kind": groups("kind"),
            "by_engine": by_engine}


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
