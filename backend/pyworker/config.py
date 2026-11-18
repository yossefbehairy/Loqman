"""Worker settings: explicit, from argv only. No env files, no pydantic."""

from __future__ import annotations

import argparse
import logging
import os
from dataclasses import dataclass

from .protocol import (
    DEFAULT_MAX_MESSAGE_BYTES,
    MAX_MAX_MESSAGE_BYTES,
    MIN_MAX_MESSAGE_BYTES,
)


@dataclass(frozen=True)
class Settings:
    db_path: str
    data_dir: str | None = None
    runtime_mode: str | None = None
    max_message_bytes: int = DEFAULT_MAX_MESSAGE_BYTES
    max_inflight_requests: int = 64
    inbox_size: int = 256
    outbox_size: int = 1024
    shutdown_timeout_s: float = 5.0
    log_level: str = "INFO"


def _runtime_mode(value: str) -> str:
    from .appsettings import RUNTIME_MODES, runtime_mode_error

    if value not in RUNTIME_MODES:
        raise argparse.ArgumentTypeError(runtime_mode_error(value))
    return value


def parse_args(argv: list[str]) -> Settings:
    p = argparse.ArgumentParser(
        prog="python -m backend.pyworker",
        description="AgentOS Python worker (JSON Lines on stdin/stdout).",
    )
    p.add_argument("--db", default=None, help="Explicit sqlite file (created if missing).")
    p.add_argument("--data-dir", default=None,
                   help="Writable data directory; the database is <dir>/agentos.db (desktop mode).")
    p.add_argument("--runtime-mode", default=None, type=_runtime_mode,
                   help="Override RUNTIME_MODE: real (default) or mock. The default engine is chosen in Settings.")
    p.add_argument("--max-message-bytes", type=int, default=DEFAULT_MAX_MESSAGE_BYTES)
    p.add_argument("--shutdown-timeout", type=float, default=5.0)
    p.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    ns = p.parse_args(argv)
    if not (MIN_MAX_MESSAGE_BYTES <= ns.max_message_bytes <= MAX_MAX_MESSAGE_BYTES):
        p.error(f"--max-message-bytes must be in [{MIN_MAX_MESSAGE_BYTES}, {MAX_MAX_MESSAGE_BYTES}]")
    if not ns.db and not ns.data_dir:
        p.error("one of --db or --data-dir is required")
    if ns.shutdown_timeout <= 0:
        p.error("--shutdown-timeout must be > 0")
    if ns.db:
        db_path = os.path.abspath(ns.db)
    else:
        db_path = os.path.join(os.path.abspath(os.path.expanduser(ns.data_dir)), "agentos.db")
    return Settings(
        db_path=db_path,
        data_dir=os.path.abspath(os.path.expanduser(ns.data_dir)) if ns.data_dir else None,
        runtime_mode=ns.runtime_mode,
        max_message_bytes=ns.max_message_bytes,
        shutdown_timeout_s=ns.shutdown_timeout,
        log_level=ns.log_level,
    )


def setup_logging(level: str) -> None:
    # stdout is the protocol channel: logs go to stderr only.
    logging.basicConfig(
        level=getattr(logging, level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=__import__("sys").stderr,
    )
