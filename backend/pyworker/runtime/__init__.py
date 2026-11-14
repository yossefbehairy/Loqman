"""Runtime package & factory (default runtime chosen by RUNTIME_MODE)."""

from backend.pyworker.appsettings import settings
from backend.pyworker.runtime.base import AgentRuntime

_runtime_instance = None
_engine_instances: dict = {}


def get_runtime(engine: str = None) -> AgentRuntime:
    """Returns the default runtime instance (created lazily).

    In real mode ``engine`` selects which engine's runtime is the default (cached per
    engine; OpenCode when None). Other modes ignore it: mock stays mock and the
    claude/codex/agy modes stay pinned to their adapter."""
    global _runtime_instance
    if engine not in (None, "opencode") and settings.RUNTIME_MODE == "real":
        if engine not in _engine_instances:
            from backend.pyworker.runtime.engine_registry import create_engine_runtime
            _engine_instances[engine] = create_engine_runtime(engine)
        return _engine_instances[engine]
    if _runtime_instance is None:
        mode = settings.RUNTIME_MODE
        if mode == "mock":
            from backend.pyworker.runtime.mock_runtime import MockRuntime
            _runtime_instance = MockRuntime()
        elif mode == "claude":
            from backend.pyworker.runtime.claude_runtime import ClaudeCodeRuntime
            _runtime_instance = ClaudeCodeRuntime()
        elif mode == "codex":
            from backend.pyworker.runtime.codex_runtime import CodexCliRuntime
            _runtime_instance = CodexCliRuntime()
        elif mode == "agy":
            from backend.pyworker.runtime.agy_runtime import AgyCliRuntime
            _runtime_instance = AgyCliRuntime()
        else:
            from backend.pyworker.runtime.opencode_runtime import RealOpenCodeRuntime
            _runtime_instance = RealOpenCodeRuntime()
    return _runtime_instance


def set_runtime(runtime: AgentRuntime) -> None:
    """Override the runtime instance (test isolation)."""
    global _runtime_instance
    _runtime_instance = runtime
