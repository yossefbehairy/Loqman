"""Single source of truth for the four supported engines (CLI-backed providers).

Install/login commands are fixed constants: nothing here is ever built from
client input, and ``engines.open_setup`` spawns exactly these argv tuples.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple

_BASE_CAPS: Dict[str, Any] = {"conversation_runs": True, "streaming_text": False, "tool_approvals": False,
                              "cancellation": "best_effort_remote_unconfirmed", "workspace_restriction": False}


@dataclass(frozen=True)
class EngineProvider:
    engine_id: str
    title: str
    vendor: str
    install_command: Optional[Tuple[str, ...]]
    install_url: Optional[str]
    login_command: Optional[Tuple[str, ...]]
    docs_url: Optional[str]
    # usage-ledger provider ids that belong to this engine
    provider_ids: Tuple[str, ...]
    capabilities: Dict[str, Any] = field(compare=False)


PROVIDERS: Tuple[EngineProvider, ...] = (
    EngineProvider(
        "opencode", "OpenCode", "OpenCode", ("npm", "install", "-g", "opencode-ai"), None,
        ("opencode", "auth", "login"), "https://opencode.ai/docs", ("opencode",),
        {**_BASE_CAPS, "model_policy": "verified_free_only"}),
    EngineProvider(
        "claude-code", "Claude", "Anthropic", ("npm", "install", "-g", "@anthropic-ai/claude-code"), None,
        ("claude", "auth", "login"), "https://docs.claude.com/en/docs/claude-code/overview", ("anthropic",),
        {**_BASE_CAPS, "model_policy": "subscription_or_account", "tool_approvals": True,
         "workspace_restriction": "scoped_mcp_tools",
         "workspace_tools": ["workspace_read", "workspace_write"],
         "browser_tools": True, "computer_tools": "selected_windows_window",
         "write_policy": "new_workspace_root_reports_only"}),
    EngineProvider(
        "codex", "ChatGPT (Codex)", "OpenAI", ("npm", "install", "-g", "@openai/codex"), None,
        ("codex", "login"), "https://developers.openai.com/codex/cli", ("openai", "openai-codex"),
        {**_BASE_CAPS, "model_policy": "subscription_or_account"}),
    EngineProvider(
        "antigravity", "Gemini (Antigravity)", "Google", None, "https://antigravity.google/download",
        ("agy", "acp", "--login"), None, ("antigravity",),
        {**_BASE_CAPS, "model_policy": "subscription_or_account",
         "permission_policy": "plan_mode_and_deny_all_requests"}),
)

_BY_ID = {p.engine_id: p for p in PROVIDERS}


def get_provider(engine_id: str) -> Optional[EngineProvider]:
    return _BY_ID.get(engine_id)
