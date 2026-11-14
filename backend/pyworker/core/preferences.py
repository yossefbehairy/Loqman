"""Worker-side preferences: the default engine and model.

Stored as a small JSON file next to agentos.db (``preferences.json``): no database
schema is involved. The file is written atomically and a missing or corrupt file just
means "nothing chosen yet" (OpenCode keeps being the default engine, as before).

The saved model is NOT verified against the engine's catalog here: saving never needs
a CLI call. Whoever uses the model checks it against the catalog (``orchestration.routing``).

``bind`` makes one Preferences object the process-wide source for the helpers below
(``default_engine_id``, ``default_model_id``, ...), which the orchestration code uses
without a request context, exactly like ``ledger.bind``.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
from typing import Any, Dict, Optional, Tuple

from backend.pyworker.appsettings import settings
from backend.pyworker.runtime.providers import PROVIDERS, get_provider

FILE_NAME = "preferences.json"
ENGINE_IDS = tuple(p.engine_id for p in PROVIDERS)
# Ids such as "deepseek-v4.1-flash-free", "claude-opus-4-5", "gpt-5.1-codex", "opus[1m]", "org/model:tag".
MODEL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+\[\]-]{0,127}$")

# RUNTIME_MODE values other than real/mock pin the default runtime to one adapter.
_MODE_ENGINE = {"claude": "claude-code", "codex": "codex", "agy": "agy-legacy-cli"}
_MODE_PROVIDER = {"claude": "anthropic", "codex": "openai", "agy": "antigravity"}


def valid_model_id(model_id: Any) -> bool:
    return isinstance(model_id, str) and MODEL_ID_RE.match(model_id) is not None


class Preferences:
    """The saved ``{engine, model_id}`` default. ``path=None`` keeps it in memory only."""

    def __init__(self, path: Optional[str]):
        self.path = path
        self._lock = threading.Lock()
        self._memory: Dict[str, Any] = {}

    def _read(self) -> Dict[str, Any]:
        if self.path is None:
            return dict(self._memory)
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def get_default(self) -> Tuple[Optional[str], Optional[str]]:
        """The saved (engine, model_id), or (None, None) when nothing valid is saved."""
        data = self._read()
        engine, model_id = data.get("engine"), data.get("model_id")
        if engine in ENGINE_IDS and valid_model_id(model_id):
            return engine, model_id
        return None, None

    def set_default(self, engine: str, model_id: str) -> None:
        if engine not in ENGINE_IDS:
            raise ValueError("Unknown engine.")
        if not valid_model_id(model_id):
            raise ValueError("Invalid model id.")
        payload = {"engine": engine, "model_id": model_id}
        with self._lock:
            if self.path is None:
                self._memory = payload
                return
            directory = os.path.dirname(os.path.abspath(self.path))
            os.makedirs(directory, exist_ok=True)
            fd, tmp = tempfile.mkstemp(prefix=".preferences-", suffix=".tmp", dir=directory)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    json.dump(payload, fh)
                    fh.flush()
                    os.fsync(fh.fileno())
                os.replace(tmp, self.path)
            except BaseException:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise


def path_for_db(db_path: str) -> Optional[str]:
    """``preferences.json`` in the data directory that holds the database (None for in-memory databases)."""
    if not db_path or db_path == ":memory:" or db_path.startswith("file:"):
        return None
    return os.path.join(os.path.dirname(os.path.abspath(db_path)), FILE_NAME)


_bound: Optional[Preferences] = None


def bind(prefs: Optional[Preferences]) -> None:
    global _bound
    _bound = prefs


def current() -> Optional[Preferences]:
    return _bound


def preferred() -> Tuple[Optional[str], Optional[str]]:
    """The saved default as (engine, model_id); (None, None) when unset or when the mode pins the runtime."""
    if settings.RUNTIME_MODE != "real" or _bound is None:
        return None, None
    return _bound.get_default()


def default_engine_id() -> str:
    """The engine the default runtime belongs to: mock in mock mode, the pinned adapter in claude/codex/agy
    mode, otherwise the saved preference (OpenCode when nothing is saved)."""
    mode = settings.RUNTIME_MODE
    if mode == "mock":
        return "mock"
    if mode in _MODE_ENGINE:
        return _MODE_ENGINE[mode]
    return preferred()[0] or "opencode"


def default_model_id() -> str:
    """The model to use when nothing more specific is chosen: the saved preference, else the setting."""
    return preferred()[1] or settings.DEFAULT_MODEL_ID


def provider_label(engine: str) -> str:
    """Usage-ledger provider id for an engine."""
    if engine == "mock":
        return "mock"
    p = get_provider(engine)
    if p is not None:
        return p.provider_ids[0]
    return next((v for k, v in _MODE_PROVIDER.items() if _MODE_ENGINE[k] == engine), "opencode")


def default_identity() -> Tuple[str, str]:
    """(engine label, ledger provider) of the default runtime."""
    engine = default_engine_id()
    return engine, provider_label(engine)


def engine_title(engine: str) -> str:
    p = get_provider(engine)
    return p.title if p is not None else engine


def free_only(engine: str) -> bool:
    """True when the engine runs only verified-free models (provider catalog policy). The mock simulates the
    OpenCode free list, so it is treated the same way."""
    if engine == "mock":
        return True
    p = get_provider(engine)
    return bool(p) and p.capabilities.get("model_policy") == "verified_free_only"
