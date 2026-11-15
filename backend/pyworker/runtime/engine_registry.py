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


CLAUDE_DRIVER_ENV = "AGENTOS_CLAUDE_DRIVER"


def claude_driver_enabled(env=None) -> bool:
    """Feature flag for the native Claude stream-json driver. Default OFF; ``AGENTOS_CLAUDE_DRIVER=stream`` enables it."""
    import os
    value = (os.environ if env is None else env).get(CLAUDE_DRIVER_ENV, "")
    return value.strip().lower() in ("stream", "1", "on", "true")


def create_turn_driver(engine_id: str, runtime: AgentRuntime, *, legacy_task=None, db=None, conversation_id=None,
                       run_id=None, resume_prompt=None, enabled=None):
    """The ONE place that chooses a turn driver. Claude streaming only when enabled; otherwise the whole-result wrapper."""
    if engine_id == "claude-code" and (claude_driver_enabled() if enabled is None else enabled):
        from backend.pyworker.runtime.claude_driver import ClaudeStreamDriver, conversation_resume_check
        sessions = resume_check = None
        if db is not None and conversation_id:
            from backend.pyworker.runtime.sessions import SessionRepository
            sessions = SessionRepository(db)
            resume_check = conversation_resume_check(db, conversation_id, run_id)
        return ClaudeStreamDriver(sessions=sessions, resume_prompt=resume_prompt, resume_check=resume_check)
    from backend.pyworker.runtime.execute_driver import ExecuteTaskDriver
    return ExecuteTaskDriver(runtime, engine_id, legacy_task=legacy_task)
