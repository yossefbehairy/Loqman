"""Runtime package & factory (real or mock; the default engine is a saved preference)."""

from backend.pyworker.appsettings import settings
from backend.pyworker.runtime.base import AgentRuntime

_runtime_instance = None
_engine_instances: dict = {}


def get_runtime(engine: str = None) -> AgentRuntime:
    """Returns the default runtime instance (created lazily).

    In real mode ``engine`` selects which engine's runtime is the default (cached per
    engine; OpenCode when None). Mock mode ignores it and stays mock."""
    global _runtime_instance
    if engine not in (None, "opencode") and settings.RUNTIME_MODE == "real":
        if engine not in _engine_instances:
            from backend.pyworker.runtime.engine_registry import create_engine_runtime
            _engine_instances[engine] = create_engine_runtime(engine)
        return _engine_instances[engine]
    if _runtime_instance is None:
        if settings.RUNTIME_MODE == "mock":
            from backend.pyworker.runtime.mock_runtime import MockRuntime
            _runtime_instance = MockRuntime()
        else:
            from backend.pyworker.runtime.opencode_runtime import RealOpenCodeRuntime
            _runtime_instance = RealOpenCodeRuntime()
    return _runtime_instance


def set_runtime(runtime: AgentRuntime) -> None:
    """Override the runtime instance (test isolation)."""
    global _runtime_instance
    _runtime_instance = runtime
