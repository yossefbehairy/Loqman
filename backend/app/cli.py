"""
AgentOS Backend CLI & Desktop Entry Point

Parses launcher arguments (from the Electron shell or the PyInstaller exe),
applies them as environment configuration BEFORE importing the app module,
then serves the FastAPI app with uvicorn.

This ordering matters: backend.app.config instantiates the Settings singleton
(and backend.app.db.session creates the SQLAlchemy engine) at import time,
so AGENTOS_DATA_DIR / HOST / PORT must be in os.environ first.

Usage:
    python -m backend.app.cli --port 8123 --data-dir "%APPDATA%/AgentOS"
"""

import argparse
import os


def _parse_args(argv=None):
    parser = argparse.ArgumentParser(
        prog="agentos-backend",
        description="AgentOS backend server (dev CLI & desktop sidecar entry point)",
    )
    parser.add_argument("--host", default=None, help="Bind host (default: settings.HOST = 127.0.0.1)")
    parser.add_argument("--port", type=int, default=None, help="Bind port (default: settings.PORT = 8000)")
    parser.add_argument(
        "--data-dir",
        default=None,
        help="Writable data directory for agentos.db (desktop mode; overrides SQLITE_DB_URL)",
    )
    parser.add_argument("--log-level", default=None, help="uvicorn log level (default: info)")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = _parse_args(argv)

    # Must be set before backend.app.config / backend.app.main are imported.
    if args.data_dir:
        os.environ["AGENTOS_DATA_DIR"] = args.data_dir
    if args.host:
        os.environ["HOST"] = args.host
    if args.port:
        os.environ["PORT"] = str(args.port)

    # Imported late on purpose (after env is applied). Passing the app object
    # (not an import string) keeps uvicorn working inside a frozen PyInstaller exe.
    import uvicorn

    from backend.app.main import app
    from backend.app.config import settings

    uvicorn.run(
        app,
        host=settings.HOST,
        port=settings.PORT,
        log_level=args.log_level or ("debug" if settings.DEBUG else "info"),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
