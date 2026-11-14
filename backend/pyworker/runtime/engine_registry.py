"""Canonical engine lookup shared by setup/catalog APIs."""
from backend.pyworker.runtime.base import AgentRuntime
from backend.pyworker.runtime.providers import PROVIDERS


ENGINE_IDS = tuple(p.engine_id for p in PROVIDERS)


def create_engine_runtime(engine_id: str) -> AgentRuntime:
    if engine_id == "opencode":
        from backend.pyworker.runtime.opencode_runtime import RealOpenCodeRuntime
        return RealOpenCodeRuntime()
    if engine_id == "claude-code":
        from backend.pyworker.runtime.claude_runtime import ClaudeCodeRuntime
        return ClaudeCodeRuntime()
    if engine_id == "codex":
        from backend.pyworker.runtime.codex_runtime import CodexCliRuntime
        return CodexCliRuntime()
    if engine_id == "antigravity":
        from backend.pyworker.runtime.agy_acp_runtime import AgyAcpRuntime
        return AgyAcpRuntime()
    raise ValueError("Unknown engine")
