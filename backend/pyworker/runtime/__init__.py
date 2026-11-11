"""Runtime package & factory (default runtime chosen by RUNTIME_MODE)."""

from backend.pyworker.appsettings import settings
from backend.pyworker.runtime.base import AgentRuntime

_runtime_instance = None


def get_runtime() -> AgentRuntime:
    """Returns the configured default runtime instance (created lazily)."""
    global _runtime_instance
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
