"""AgentOS Python worker (phase 1): stdlib-only, JSON Lines over stdin/stdout.

This package must never import ``backend.app`` (FastAPI/SQLAlchemy/Pydantic
land) or any third-party module. ``backend/tests/test_pyworker_imports.py``
enforces that by actually running the worker with those modules blocked.
"""

PROTOCOL = "agentos-worker/1"
WORKER_VERSION = "0.1.0"
