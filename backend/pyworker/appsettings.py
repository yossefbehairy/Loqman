"""Application settings for the stdlib-only worker.

Same names/defaults as the legacy pydantic ``Settings`` so the copied runtime
adapters and orchestration keep working. Values come from (lowest to highest
priority): defaults, a ``.env`` file in the working directory, the process
environment, then explicit overrides from the command line.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, fields
from typing import List, Optional


def _parse_dotenv(path: str) -> dict:
    out: dict = {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for raw in fh:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                value = value.strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                    value = value[1:-1]
                out[key.strip()] = value
    except OSError:
        pass
    return out


def _bool(value: str) -> bool:
    return value.strip().lower() in ("1", "true", "yes", "on")


def _list(value: str) -> List[str]:
    import json

    value = value.strip()
    if value.startswith("["):
        try:
            data = json.loads(value)
            return [str(x) for x in data]
        except ValueError:
            pass
    return [p.strip() for p in value.split(",") if p.strip()]


RUNTIME_MODES = ("real", "mock")
# Modes that used to pin the whole worker to one CLI. The default engine is now a saved preference.
RETIRED_RUNTIME_MODES = ("claude", "codex", "agy")


def runtime_mode_error(mode: str) -> str:
    """The message for an unusable RUNTIME_MODE value (clear text, never a stack trace)."""
    if mode in RETIRED_RUNTIME_MODES:
        return (f"RUNTIME_MODE={mode} is no longer supported. Remove it (or use RUNTIME_MODE=real) and set the "
                "default provider in Settings; RUNTIME_MODE now accepts only 'real' or 'mock'.")
    return f"RUNTIME_MODE must be one of {RUNTIME_MODES}, got {mode!r}."


@dataclass
class Settings:
    APP_NAME: str = "Loqman"
    APP_VERSION: str = "0.2.2"
    DEBUG: bool = False
    # Desktop mode: Electron passes its userData folder; agentos.db lives there.
    AGENTOS_DATA_DIR: Optional[str] = None
    SQLITE_DB_PATH: str = "./agentos.db"
    OPENCODE_BASE_URL: str = "http://127.0.0.1:49100"
    DEFAULT_MODEL_ID: str = "deepseek-v4.1-flash-free"
    RUNTIME_MODE: str = "real"
    FREE_PROVIDER_ALLOWLIST: List[str] = field(default_factory=lambda: ["opencode", "nvidia"])
    CHAT_AGENT_ID: str = "agentos-chat"
    WORK_AGENT_ID: str = "agentos-worker"
    MISSION_MAX_CONCURRENCY: int = 3
    MISSION_TASK_TIMEOUT: int = 150
    MISSION_MAX_RETRIES: int = 2
    MISSION_MAX_PROMPT_CHARS: int = 12000
    MISSION_MAX_OUTPUT_CHARS: int = 20000

    def apply_data_dir(self) -> None:
        if self.AGENTOS_DATA_DIR:
            data_dir = os.path.expanduser(self.AGENTOS_DATA_DIR)
            os.makedirs(data_dir, exist_ok=True)
            self.SQLITE_DB_PATH = os.path.join(data_dir, "agentos.db")


def load_settings(env: Optional[dict] = None, dotenv_path: Optional[str] = ".env", **overrides) -> Settings:
    merged: dict = {}
    if dotenv_path:
        merged.update(_parse_dotenv(dotenv_path))
    merged.update(os.environ if env is None else env)
    s = Settings()
    for f in fields(Settings):
        if f.name in merged:
            raw = merged[f.name]
            cur = getattr(s, f.name)
            if isinstance(cur, bool):
                setattr(s, f.name, _bool(raw))
            elif isinstance(cur, int):
                try:
                    setattr(s, f.name, int(raw))
                except ValueError:
                    pass
            elif isinstance(cur, list):
                setattr(s, f.name, _list(raw))
            else:
                setattr(s, f.name, raw)
    for key, value in overrides.items():
        if value is not None:
            setattr(s, key, value)
    if s.RUNTIME_MODE not in RUNTIME_MODES:
        raise ValueError(runtime_mode_error(s.RUNTIME_MODE))
    s.apply_data_dir()
    return s


# Process-wide object read by the copied runtime adapters. The entry point
# (``__main__``) replaces its fields via ``configure`` before serving.
try:
    settings = load_settings(dotenv_path=None)
except ValueError:
    # A retired/invalid RUNTIME_MODE in the environment must not crash the import with a trace: the entry point
    # re-validates the mode and reports it (see __main__).
    settings = load_settings(dotenv_path=None, env={})


def configure(new: Settings) -> Settings:
    """Mutate the shared settings object in place so every importer sees it."""
    for f in fields(Settings):
        setattr(settings, f.name, getattr(new, f.name))
    return settings
