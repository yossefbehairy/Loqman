"""AgentOS Python worker: the whole backend, stdlib-only, JSON Lines over stdin/stdout.

This package must never import a web framework, an ORM, Pydantic or any other
third-party module. ``backend/tests/test_pyworker_imports.py`` enforces that by
running the real worker with those modules blocked. Docs: docs/PYWORKER.md.
"""

PROTOCOL = "agentos-worker/1"
WORKER_VERSION = "0.1.0"
