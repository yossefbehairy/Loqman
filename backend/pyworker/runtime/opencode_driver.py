"""Native OpenCode turn driver: ``opencode acp`` over the shared ACP transport (P7b). OFF by default
(``AGENTOS_OPENCODE_DRIVER=acp``); the legacy ``opencode api`` adapter (``opencode_runtime``) stays until a real-CLI check.

It subclasses ``AntigravityAcpDriver`` (same permission-deny policy, single terminal event, resume probing via
``session/resume`` only, ``runtime_sessions`` persistence, interrupt/kill, bounded output, env allowlist) and overrides only
the OpenCode dialect. The prompt travels in the JSON-RPC ``session/prompt`` frame, never on argv: this resolves S4.

What the legacy adapter enforced, and how each rule is kept (fail closed wherever ACP cannot prove it):
  * No tools / locked agent. Legacy only ran the app's own locked agents (``CHAT_AGENT_ID`` / ``WORK_AGENT_ID``, defined
    in the user's OpenCode config with edit/bash/browser/subagent denied). Here the ACP *session mode* is the OpenCode
    agent: the requested agent must be one of those two, must be OFFERED in ``session/new.modes.availableModes``, must be
    accepted by ``session/set_mode`` and a later ``current_mode_update`` to any other mode aborts the turn. If OpenCode
    does not list the locked agent (e.g. hidden agents are not exposed over ACP) the turn fails with
    ``unsupported_capability``: it never falls back to ``build``. On top of that every ``session/request_permission`` is
    answered ``cancelled``, no fs/terminal capability is advertised, no MCP server is passed, and a tool that finishes
    after a denial fails the turn.
  * Free-only model rule. ACP carries no pricing, so the model is resolved with the legacy catalog check
    (``RealOpenCodeRuntime.resolve_model``: explicit 0/0 cost from an allowlisted provider) before launch; the wire id is
    ``<providerID>/<modelID>`` from that catalog entry, it must be offered by ``session/new`` and accepted by
    ``session/set_model`` (no config-option fallback guesses for a model that was not offered), and model drift fails.
    Without a resolver the driver refuses to run.
  * Variants/effort, a separate system prompt and scoped MCP tools are refused (``unsupported_capability``).
  * Output capped at 60000 chars (as legacy). The prompt is NOT truncated at 24000 as the legacy adapter silently did
    (that limit existed because of argv size); over 200000 is refused.
  * Environment: ``child_env`` allowlist (never ``AGENTOS_LOCAL_TOKEN``) plus non-secret OpenCode/XDG location variables;
    provider API keys only with the opt-in flag. This is NARROWER than the legacy adapter, which forwarded the whole
    environment minus the local token.
"""

from __future__ import annotations

import os
import platform
import re
import shutil
from typing import Any, Callable, Optional

from .antigravity_driver import AntigravityAcpDriver, PROTOCOL, _Turn, _failure, conversation_resume_check  # noqa: F401
from .base import RuntimeHealth
from .child_env import child_env
from .errors import RuntimeErrorBase

ACP_ARGS = ("acp",)  # `opencode acp --help`: the only flag is --login (never passed); cwd travels in session/new
NAMESPACE = "opencode:default"
ALLOWED_AGENTS_DEFAULT = ("agentos-chat", "agentos-worker")
_PATH_ENV = ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME", "OPENCODE_CONFIG", "OPENCODE_CONFIG_DIR")
_KEY_ENV = ("NVIDIA_API_KEY", "OPENCODE_API_KEY")  # opt-in only
_BRAND = ((re.compile(r"\bagy acp\b"), "opencode acp"), (re.compile(r"\bagy\b"), "opencode"),
          (re.compile(r"Antigravity"), "OpenCode"), (re.compile(r"\bplan mode\b"), "the locked agent mode"))


def default_cli_cmd() -> Optional[list]:
    if platform.system() == "Windows":
        npm_exe = os.path.expandvars(r"%APPDATA%\npm\node_modules\opencode-ai\bin\opencode.exe")
        if os.path.exists(npm_exe):
            return [npm_exe]
    path = shutil.which("opencode.exe") or shutil.which("opencode")
    return [path] if path else None


def _rebrand(text: str) -> str:
    """The shared dialect code words its messages for agy; show OpenCode's name instead (text only, no behaviour)."""
    for pattern, replacement in _BRAND:
        text = pattern.sub(replacement, text)
    return text


class OpenCodeAcpDriver(AntigravityAcpDriver):
    engine = "opencode"
    namespace = NAMESPACE
    acp_args = ACP_ARGS
    sessions_dir = "opencode-sessions"

    def __init__(self, cli_cmd=None, *, resolve_model: Optional[Callable[[str], Any]] = None, legacy_runtime=None,
                 allowed_agents: Optional[tuple] = None, default_agent: Optional[str] = None, **kw):
        if isinstance(cli_cmd, str):
            cli_cmd = [cli_cmd]
        super().__init__(["unused"], **kw)
        self._cmd = list(cli_cmd) if cli_cmd else default_cli_cmd()  # never the base's agy fallback
        self._legacy = legacy_runtime
        self._resolve = resolve_model or getattr(legacy_runtime, "resolve_model", None)
        if allowed_agents is None or default_agent is None:
            from backend.pyworker.appsettings import settings
            allowed_agents = allowed_agents or (settings.CHAT_AGENT_ID, settings.WORK_AGENT_ID)
            default_agent = default_agent or settings.WORK_AGENT_ID
        self._allowed_agents, self._default_agent = tuple(allowed_agents), default_agent
        self._wire: dict = {}  # turn id -> "<providerID>/<modelID>"

    # -- environment --------------------------------------------------------------------------------------------------
    def _env(self) -> dict:
        names = _PATH_ENV + (_KEY_ENV if self._allow_api_key_env else ())
        env = child_env(names)
        env.pop("AGENTOS_LOCAL_TOKEN", None)
        env.update(self._extra_env or {})
        return env

    # -- dialect hooks ------------------------------------------------------------------------------------------------
    def _agent(self, s: _Turn) -> str:
        return (s.turn.agent_id or self._default_agent).strip()

    def _mode_for(self, s: _Turn) -> str:
        return self._agent(s)

    def _model_for(self, s: _Turn) -> str:
        return self._wire.get(s.turn.turn_id) or s.turn.model_id

    def _validate(self, s: _Turn):
        super()._validate(s)
        if self._agent(s) not in self._allowed_agents:
            raise _failure("model_unavailable", f"OpenCode agent is not allowed by AgentOS. Allowed agents: {sorted(self._allowed_agents)}.",
                           "launch", "not-submitted")

    async def _prepare(self, s: _Turn) -> None:
        if self._resolve is None:
            raise _failure("unsupported_capability", "OpenCode free-model verification is unavailable; refusing to run (fail closed).",
                           "launch", "not-submitted")
        try:
            entry = await self._resolve(s.turn.model_id)
        except RuntimeErrorBase as exc:
            raise _failure("model_unavailable", str(exc)[:300], "launch", "not-submitted")
        except Exception:
            raise _failure("model_unavailable", "OpenCode model could not be verified as free; refusing to run.", "launch", "not-submitted")
        provider = getattr(entry, "provider_id", None)
        if not isinstance(provider, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", provider):
            raise _failure("model_unavailable", "OpenCode catalog returned no usable provider for the model.", "launch", "not-submitted")
        self._wire[s.turn.turn_id] = f"{provider}/{s.turn.model_id}"

    async def _select_mode_and_model(self, s, conn, sid, info):
        # Never fall back to a model config option: the model must be offered and set explicitly (free-only proof).
        models, _ = self._offered(info)
        wire = self._model_for(s)
        if models is not None and wire not in models:
            raise _failure("model_unavailable", "Requested model is not offered by opencode acp.", "handshake", "not-submitted")
        await super()._select_mode_and_model(s, conn, sid, info)

    async def _send(self, s, type_, data, **kw):
        await super()._send(s, type_, _rebrand_event(data), **kw)

    async def _cleanup(self, s):
        self._wire.pop(s.turn.turn_id, None)
        await super()._cleanup(s)

    # -- health / catalog stay with the legacy CLI-catalog adapter (no prompt ever travels there) ---------------------
    def _runtime(self):
        if self._legacy is None:
            from .opencode_runtime import RealOpenCodeRuntime
            self._legacy = RealOpenCodeRuntime()
        return self._legacy

    async def check_health(self) -> RuntimeHealth:
        return await self._runtime().check_health()

    async def list_available_models(self, free_only=True, *, refresh=False) -> list:
        return await self._runtime().list_available_models(free_only)


def _rebrand_event(data: dict) -> dict:
    out = {}
    for key, value in data.items():
        if key in ("message", "stop_reason") and isinstance(value, str):
            out[key] = _rebrand(value)
        elif key == "failure" and isinstance(value, dict):
            out[key] = {k: _rebrand(v) if k == "message" else v for k, v in value.items()}
        else:
            out[key] = value
    return out


def build_opencode_driver(*, db=None, conversation_id=None, run_id=None, resume_prompt=None, runtime=None):
    sessions = resume_check = None
    if db is not None and conversation_id:
        from .sessions import SessionRepository
        sessions = SessionRepository(db)
        resume_check = conversation_resume_check(db, conversation_id, run_id)
    return OpenCodeAcpDriver(sessions=sessions, resume_prompt=resume_prompt, resume_check=resume_check,
                             legacy_runtime=runtime, allow_api_key_env=bool(getattr(runtime, "_allow_api_key_env", False)))
