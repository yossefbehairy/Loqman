"""The one engine-id -> runtime factory (``create_engine_runtime``). Ids, titles and ledger provider ids live in
``providers.py``."""
from backend.pyworker.runtime.base import AgentRuntime
from backend.pyworker.runtime.providers import MOCK_ENGINE_ID, PROVIDERS


ENGINE_IDS = tuple(p.engine_id for p in PROVIDERS)


def create_engine_runtime(engine_id: str) -> AgentRuntime:
    if engine_id == MOCK_ENGINE_ID:
        from backend.pyworker.runtime.mock_runtime import MockRuntime
        return MockRuntime()
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
