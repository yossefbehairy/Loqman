"""preferences.get / preferences.set_default: the worker-side default engine and model.

The default drives everything that has no explicit engine: Loqman (``ceo.ask`` without
``engine``), the agents and teams Loqman creates, missions, delegation and planning.
The model is checked against the engine's catalog when it is used, never when saved.
"""

from __future__ import annotations

from backend.pyworker.core import preferences as prefs_mod
from backend.pyworker.errors import api_error
from backend.pyworker.validate import check_keys, req_str

from .registry import method


def _out(ctx) -> dict:
    engine, model_id = ctx.preferences.get_default() if ctx.preferences is not None else (None, None)
    return {"engine": engine, "model_id": model_id,
            "effective_engine": prefs_mod.default_engine_id(),
            "effective_model_id": prefs_mod.default_model_id()}


@method("preferences.get")
async def preferences_get(ctx, params):
    check_keys(params)
    return _out(ctx)


@method("preferences.set_default")
async def preferences_set_default(ctx, params):
    check_keys(params, {"engine", "model_id"})
    engine = req_str(params, "engine", 1, 64)
    model_id = req_str(params, "model_id", 1, 128)
    if engine not in prefs_mod.ENGINE_IDS:
        raise api_error(400, f"Unknown engine '{engine[:64]}'. Known engines: {list(prefs_mod.ENGINE_IDS)}.")
    if not prefs_mod.valid_model_id(model_id):
        raise api_error(400, "Invalid model id.")
    if ctx.preferences is None:
        raise api_error(503, "Preferences are not available.")
    try:
        ctx.preferences.set_default(engine, model_id)
    except OSError:
        raise api_error(500, "The default could not be saved.")
    return _out(ctx)
