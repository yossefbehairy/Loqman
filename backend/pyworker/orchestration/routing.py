"""Engine/model routing rules shared by ceo.ask, missions, delegation and planning.

The default engine and model come from ``core.preferences``. "Free only" is a policy of
the OpenCode engine (provider catalog ``model_policy``), not a global rule. Catalog
checks list models only; they never run a model.
"""

from __future__ import annotations

from typing import Any, List, Optional

from backend.pyworker.core import preferences
from backend.pyworker.errors import api_error

OPENCODE_STYLE = ("opencode", "mock")  # engines that take the locked agent id and a reusable session title


def is_strict(engine: str) -> bool:
    """Account engines expose no variants unless the catalog lists them."""
    return engine not in OPENCODE_STYLE


def variant_ok(entry, variant: Optional[str], strict: bool) -> bool:
    if not variant or (strict and variant == "default"):
        return True
    if entry.variants:
        return variant in entry.variants
    return not strict  # strict (account engines): no exposed variants -> none supported


async def load_catalog(runtime: Any, engine: str) -> List[Any]:
    """The engine's model list under its own policy. A failure is a 502 naming the engine."""
    try:
        return await runtime.list_available_models(free_only=preferences.free_only(engine))
    except Exception:
        raise api_error(502, f"{preferences.engine_title(engine)} runtime unreachable.")


def check_model(models: List[Any], engine: str, model_id: str, variant: Optional[str] = None):
    """The catalog entry for ``model_id`` or a 400 that names the engine."""
    title = preferences.engine_title(engine)
    entry = next((m for m in models if m.id == model_id), None)
    if entry is None:
        shown = [m.id for m in models[:20]]
        if preferences.free_only(engine):
            raise api_error(400, f"Model '{model_id}' is not verified as free and active on {title}. "
                                 f"Available free models include: {shown}")
        raise api_error(400, f"Model '{model_id}' is not offered by {title}; "
                             f"available models include: {shown}")
    if not variant_ok(entry, variant, is_strict(engine)):
        raise api_error(400, f"Variant '{variant}' is not supported by model '{model_id}' on {title}. "
                             f"Supported: {entry.variants}")
    return entry


async def usable_model(runtime: Any, engine: str, candidate: Optional[str], fallback: str) -> str:
    """``candidate`` when this engine offers it, else ``fallback`` (the default model). The mock offers anything;
    a failing catalog keeps the candidate so the real error surfaces where the model runs."""
    if not candidate or candidate == fallback:
        return fallback
    if engine == "mock":
        return candidate
    try:
        models = await runtime.list_available_models(free_only=preferences.free_only(engine))
    except Exception:
        return candidate
    return candidate if any(m.id == candidate for m in models) else fallback
