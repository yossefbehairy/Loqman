"""
Runtime Package & Factory for AgentOS
"""

from backend.app.config import settings
from backend.app.runtime.base import AgentRuntime
from backend.app.runtime.mock_runtime import MockRuntime
from backend.app.runtime.opencode_runtime import RealOpenCodeRuntime

_runtime_instance = None


def get_runtime() -> AgentRuntime:
    """
    Returns the configured runtime instance (MockRuntime or RealOpenCodeRuntime).
    """
    global _runtime_instance
    if _runtime_instance is None:
        if settings.RUNTIME_MODE == "mock":
            _runtime_instance = MockRuntime()
        elif settings.RUNTIME_MODE == "claude":
            from backend.app.runtime.claude_runtime import ClaudeCodeRuntime
            _runtime_instance = ClaudeCodeRuntime()
        elif settings.RUNTIME_MODE == "codex":
            from backend.app.runtime.codex_runtime import CodexCliRuntime
            _runtime_instance = CodexCliRuntime()
        elif settings.RUNTIME_MODE == "agy":
            from backend.app.runtime.agy_runtime import AgyCliRuntime
            _runtime_instance = AgyCliRuntime()
        else:
            _runtime_instance = RealOpenCodeRuntime()
    return _runtime_instance


def set_runtime(runtime: AgentRuntime):
    """
    Override runtime instance (useful for test isolation).
    """
    global _runtime_instance
    _runtime_instance = runtime
