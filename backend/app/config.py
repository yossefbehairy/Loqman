"""
AgentOS Configuration Module
Loads application configuration with environment variable support.
"""

from pathlib import Path
from typing import List, Literal, Optional

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    APP_NAME: str = "Loqman"
    APP_VERSION: str = "0.2.2"
    DEBUG: bool = False

    # Server binding (Strictly 127.0.0.1 for local security)
    HOST: str = "127.0.0.1"
    PORT: int = 8000

    # Database
    SQLITE_DB_URL: str = "sqlite:///./agentos.db"

    # Desktop mode (Electron shell passes its userData folder here):
    # when set, agentos.db lives inside this single writable directory.
    AGENTOS_DATA_DIR: Optional[str] = None

    # AI Runtime Configuration
    OPENCODE_BASE_URL: str = "http://127.0.0.1:49100"
    # Must exist in the live free catalog; revalidated before every execution.
    DEFAULT_MODEL_ID: str = "deepseek-v4.1-flash-free"
    # "real" = OpenCode (default). "claude" = opt-in bounded Claude Code adapter.
    # "codex" = opt-in bounded Codex CLI adapter (read-only sandbox, see codex_runtime.py).
    # "agy" = opt-in bounded Antigravity CLI adapter (print mode, see agy_runtime.py).
    RUNTIME_MODE: Literal["real", "mock", "claude", "codex", "agy"] = "real"

    # Free-only enforcement (fail-closed):
    # A model is executable only when its provider is in this allowlist AND it
    # declares explicit 0/0 pricing. Custom providers (e.g. a keyed gateway)
    # with no declared cost must NEVER be treated as free.
    #
    # "opencode" + "nvidia" are free catalog providers that need no user key.
    # "agentrouter" is deliberately excluded: it is a keyed external gateway
    # configured by the user, so treating its models as free would violate the
    # "no external APIs" invariant. OpenAI is paid/image-only.
    FREE_PROVIDER_ALLOWLIST: List[str] = ["opencode", "nvidia"]

    # Locked OpenCode agents (defined in ~/.config/opencode/agents/*.md).
    # Conversational surfaces use CHAT_AGENT_ID (no tools). Work tasks use
    # WORK_AGENT_ID. "build" is deliberately NOT allowed by the app.
    CHAT_AGENT_ID: str = "agentos-chat"
    WORK_AGENT_ID: str = "agentos-worker"

    # CEO mission orchestration limits (local MVP, no Redis)
    MISSION_MAX_CONCURRENCY: int = 3
    MISSION_TASK_TIMEOUT: int = 150
    MISSION_MAX_RETRIES: int = 2
    MISSION_MAX_PROMPT_CHARS: int = 12000
    MISSION_MAX_OUTPUT_CHARS: int = 20000

    # CORS configuration for Frontend (Vite) and the Electron desktop shell.
    # "null" is the Origin header sent by pages loaded from file:// inside Electron.
    CORS_ORIGINS: List[str] = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "null",
    ]

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    @model_validator(mode="after")
    def _apply_desktop_data_dir(self) -> "Settings":
        """
        Desktop mode: redirect the SQLite database into AGENTOS_DATA_DIR
        (the Electron userData folder) and make sure the directory exists.
        """
        if self.AGENTOS_DATA_DIR:
            data_dir = Path(self.AGENTOS_DATA_DIR).expanduser()
            data_dir.mkdir(parents=True, exist_ok=True)
            self.SQLITE_DB_URL = f"sqlite:///{(data_dir / 'agentos.db').as_posix()}"
        return self


settings = Settings()
