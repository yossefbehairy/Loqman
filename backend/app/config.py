"""
AgentOS Configuration Module
Loads application configuration with environment variable support.
"""

from pathlib import Path
from typing import List, Literal, Optional

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    APP_NAME: str = "AgentOS"
    APP_VERSION: str = "0.2.1"
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
    DEFAULT_MODEL_ID: str = "muse-spark-1.3-contributor-free"
    RUNTIME_MODE: Literal["real", "mock"] = "real"

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
