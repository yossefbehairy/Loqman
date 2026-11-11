"""
ClaudeCodeRuntime: a deliberately BOUNDED AgentRuntime adapter over the local
Claude Code CLI (`claude`), using its official non-interactive print mode.

Selected only with RUNTIME_MODE=claude. The default (RUNTIME_MODE=real) stays
OpenCode; nothing here is imported unless Claude is selected.

What one task does
  claude -p --output-format json --tools "" --strict-mcp-config
         --no-session-persistence --model <alias|id>
  prompt is written to stdin (no argv length / quoting issues), cwd is a fresh
  empty temp directory, one process per task, one JSON result object parsed.

Explicit limitations (do not oversell this adapter)
  * NOT a sandbox. `--tools ""` disables Claude's BUILT-IN tools and
    `--strict-mcp-config` (with no --mcp-config) ignores configured MCP
    servers, but the user's own Claude settings/hooks/memory still load, and
    the empty cwd is a convenience, not an access boundary. No approval flow
    and no workspace restriction are offered.
  * Fail closed: before the first run the adapter reads `claude --help` and
    refuses to execute if any required flag is missing in the installed CLI.
  * Stateless: --no-session-persistence, so no resume/threads. Conversation
    context must be carried in the prompt by the caller. `session_id` in
    results is always None.
  * No streaming: the full reply is returned when the process exits.
  * Model catalog is NOT discoverable from the CLI. Only the documented
    aliases are listed, marked status="unverified"/is_free=False. Whether the
    account may use a model is only known when a task runs. `variant` is not
    supported and is rejected. Nothing here is ever reported as free.
  * Billing is unknown. ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN are removed
    from the child environment by default so the CLI cannot silently switch
    to paid API billing; `total_cost_usd` reported by the CLI is exposed only
    as tokens["reported_cost_usd"] (it is an estimate, not a bill) and
    AgentResult.cost stays unknown.
  * Timeout: wall-clock, enforced by this process; on expiry the child (and on
    Windows its process tree via taskkill) is killed. Cancel: `abort_task(key)`
    works only for an IN-FLIGHT task and `key` must be the task's
    `session_title` (no CLI session id exists before completion). Cancelling
    the awaiting asyncio task also kills the child. Kill is best effort; the
    CLI gives no confirmation that remote work stopped.
"""

import asyncio
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Any, Dict, List, Optional, Sequence, Union

from backend.app.runtime.base import (
    AgentResult,
    ExecuteAgentTask,
    ModelCatalogEntry,
    ModelCost,
    RuntimeHealth,
)
from backend.app.runtime.engine_discovery import (
    AUTHENTICATED,
    _extract_version,
    _parse_claude_auth,
)
from backend.app.runtime.errors import (
    RuntimeAuthError,
    RuntimeCancelled,
    RuntimeErrorBase,
    RuntimeModelUnavailable,
    RuntimeOffline,
    RuntimeRateLimit,
    RuntimeTimeout,
)

logger = logging.getLogger("agentos.runtime.claude")

MAX_PROMPT_CHARS = 24_000
MAX_OUTPUT_CHARS = 60_000
PROBE_TIMEOUT_SECONDS = 10.0
MIN_TASK_TIMEOUT = 5
MAX_TASK_TIMEOUT = 600

# Flags this adapter relies on; all must appear in `claude --help` or we refuse.
REQUIRED_FLAGS = ("--print", "--output-format", "--tools", "--strict-mcp-config",
                  "--no-session-persistence", "--model")

# Documented aliases only. Availability for the user's account is NOT verified.
KNOWN_MODEL_ALIASES = ("sonnet", "opus", "haiku")
_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-\[\]]{0,63}$")
_STRIPPED_ENV = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")

CmdSpec = Union[str, Sequence[str]]


class RuntimeUnsupported(RuntimeErrorBase):
    code = "unsupported_capability"
    retryable = False


def _classify_failure(text: str, returncode: Optional[int]) -> RuntimeErrorBase:
    """Map CLI failure text to a normalized error WITHOUT echoing raw output."""
    low = (text or "").lower()
    if any(k in low for k in ("not logged in", "/login", "invalid api key", "unauthorized",
                              "authentication", "401", "oauth token")):
        return RuntimeAuthError("Claude Code is not authenticated. Run `claude auth login` yourself.")
    if any(k in low for k in ("429", "rate limit", "usage limit", "quota", "overloaded")):
        return RuntimeRateLimit("Claude Code rate/usage limit reached. No fallback was used.")
    if "model" in low and any(k in low for k in ("not found", "unknown", "invalid", "not available",
                                                 "may not exist", "access")):
        return RuntimeModelUnavailable("Requested model is not available to this Claude account.")
    if any(k in low for k in ("econnrefused", "enotfound", "network", "connection", "timed out")):
        return RuntimeOffline("Claude Code could not reach its service.")
    return RuntimeErrorBase(f"Claude Code execution failed (exit {returncode}).")


def parse_result(stdout: str) -> Dict[str, Any]:
    """Extract the single `result` object from `--output-format json` stdout.

    Accepts either one object or an array of events (older/other shapes) and
    takes the last element with type == "result". Anything else is invalid.
    """
    try:
        data = json.loads((stdout or "").strip().lstrip("﻿"))
    except ValueError:
        raise RuntimeErrorBase("Claude Code returned unparseable output (invalid JSON).")
    if isinstance(data, list):
        data = next((d for d in reversed(data)
                     if isinstance(d, dict) and d.get("type") == "result"), None)
    if not isinstance(data, dict) or data.get("type", "result") != "result":
        raise RuntimeErrorBase("Claude Code returned no result object.")
    return data


class ClaudeCodeRuntime:
    def __init__(self, cli_cmd: Optional[CmdSpec] = None, allow_api_key_env: bool = False):
        """cli_cmd: executable path or argv prefix (tests pass [python, fake.py])."""
        if cli_cmd is None:
            cli_cmd = shutil.which("claude") or None
        if isinstance(cli_cmd, str):
            cli_cmd = [cli_cmd]
        self._cmd: Optional[List[str]] = list(cli_cmd) if cli_cmd else None
        self._allow_api_key_env = allow_api_key_env
        self._flags_ok: Optional[bool] = None
        self._inflight: Dict[str, asyncio.subprocess.Process] = {}
        self._cancelled: set = set()

    # -- helpers -----------------------------------------------------------
    def _env(self) -> Dict[str, str]:
        env = dict(os.environ)
        env.pop("AGENTOS_LOCAL_TOKEN", None)
        if not self._allow_api_key_env:
            for k in _STRIPPED_ENV:
                env.pop(k, None)
        return env

    def _probe_sync(self, args: Sequence[str]) -> Optional[subprocess.CompletedProcess]:
        if not self._cmd:
            return None
        kwargs: Dict[str, Any] = {}
        if sys.platform == "win32":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            return subprocess.run([*self._cmd, *args], stdin=subprocess.DEVNULL,
                                  capture_output=True, text=True, encoding="utf-8",
                                  errors="replace", timeout=PROBE_TIMEOUT_SECONDS,
                                  shell=False, env=self._env(), **kwargs)
        except (subprocess.TimeoutExpired, OSError, ValueError):
            return None

    async def _probe(self, args: Sequence[str]) -> Optional[subprocess.CompletedProcess]:
        return await asyncio.to_thread(self._probe_sync, args)

    async def _check_flags(self) -> bool:
        if self._flags_ok is None:
            res = await self._probe(["--help"])
            text = ((res.stdout or "") + (res.stderr or "")) if res else ""
            self._flags_ok = bool(text) and all(f in text for f in REQUIRED_FLAGS)
            if not self._flags_ok:
                logger.warning("Installed claude CLI does not advertise all required flags.")
        return self._flags_ok

    @staticmethod
    def _kill_tree_sync(pid: int) -> None:
        if sys.platform == "win32":
            try:
                subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True,
                               timeout=10, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            except Exception:
                pass

    async def _kill(self, proc: asyncio.subprocess.Process) -> None:
        if proc.returncode is not None:
            return
        if sys.platform == "win32":
            await asyncio.to_thread(self._kill_tree_sync, proc.pid)
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        try:
            await asyncio.wait_for(proc.wait(), timeout=5)
        except asyncio.TimeoutError:
            logger.warning("claude process did not confirm exit after kill.")

    # -- health / models ---------------------------------------------------
    async def check_health(self) -> RuntimeHealth:
        """Install + login status only. Never starts a model turn."""
        models = len(KNOWN_MODEL_ALIASES)
        if not self._cmd:
            return RuntimeHealth(status="offline", service_url=None,
                                 default_model_status="unavailable", models_count=0,
                                 free_models_count=0)
        ver = await self._probe(["--version"])
        if ver is None or (ver.returncode != 0 and not _extract_version(ver.stdout or "")):
            return RuntimeHealth(status="offline", default_model_status="unavailable",
                                 models_count=0, free_models_count=0)
        auth = await self._probe(["auth", "status"])
        state = _parse_claude_auth(auth.returncode, auth.stdout or "") if auth else "unknown"
        flags = await self._check_flags()
        # "ok" requires a confirmed login AND the required flags. Anything else
        # is degraded; model availability is never confirmed by health.
        status = "ok" if (state == AUTHENTICATED and flags) else "degraded"
        return RuntimeHealth(status=status, service_url=None, default_model_status="unverified",
                             models_count=models, free_models_count=0)

    async def list_available_models(self, free_only: bool = True) -> List[ModelCatalogEntry]:
        """Static documented aliases. The CLI cannot list models, so these are
        'unverified' and never free (free_only=True therefore returns [])."""
        if free_only:
            return []
        return [ModelCatalogEntry(id=a, name=f"Claude {a} (alias, unverified)",
                                  provider_id="anthropic", cost=ModelCost(), variants=[],
                                  is_free=False, status="unverified")
                for a in KNOWN_MODEL_ALIASES]

    # -- execution -----------------------------------------------------------
    async def execute_task(self, task: ExecuteAgentTask) -> AgentResult:
        if not task.prompt or not task.prompt.strip():
            raise RuntimeErrorBase("Empty prompt: refusing to invoke the model.")
        if not self._cmd:
            raise RuntimeOffline("Claude Code CLI not found on PATH.")
        if task.variant not in (None, "", "default"):
            raise RuntimeUnsupported("Claude Code adapter does not support model variants.")
        model = (task.model_id or "").strip()
        if not _MODEL_RE.match(model):
            raise RuntimeModelUnavailable("Invalid Claude model identifier.")
        if not await self._check_flags():
            raise RuntimeUnsupported(
                "Installed Claude CLI does not advertise the flags required for tool-less "
                "non-interactive mode; refusing to run.")

        timeout = max(MIN_TASK_TIMEOUT, min(int(task.timeout or 90), MAX_TASK_TIMEOUT))
        prompt = task.prompt[:MAX_PROMPT_CHARS]
        argv = [*self._cmd, "-p", "--output-format", "json", "--tools", "",
                "--strict-mcp-config", "--no-session-persistence", "--model", model]
        key = task.session_title or f"task-{time.time_ns()}"
        if key in self._inflight:
            raise RuntimeErrorBase("A Claude task with this session_title is already running.")

        t0 = time.time()
        with tempfile.TemporaryDirectory(prefix="agentos-claude-") as cwd:
            try:
                kwargs: Dict[str, Any] = {}
                if sys.platform == "win32":
                    kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
                proc = await asyncio.create_subprocess_exec(
                    *argv, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE, cwd=cwd, env=self._env(), **kwargs)
            except (FileNotFoundError, PermissionError, OSError):
                raise RuntimeOffline("Claude Code CLI could not be started.")
            self._inflight[key] = proc
            try:
                out_b, err_b = await asyncio.wait_for(
                    proc.communicate(prompt.encode("utf-8")), timeout=timeout)
            except asyncio.TimeoutError:
                await self._kill(proc)
                raise RuntimeTimeout(f"Claude Code did not finish within {timeout}s; process killed.")
            except asyncio.CancelledError:
                await self._kill(proc)
                raise
            finally:
                self._inflight.pop(key, None)
            was_cancelled = key in self._cancelled
            self._cancelled.discard(key)

        if was_cancelled:
            raise RuntimeCancelled("Claude Code task was cancelled.")
        stdout = out_b.decode("utf-8", errors="replace")
        stderr = err_b.decode("utf-8", errors="replace")

        if proc.returncode != 0:
            # Prefer a structured error in stdout JSON, else stderr, for classification only.
            raise _classify_failure(stderr + "\n" + stdout[:2000], proc.returncode)

        data = parse_result(stdout)
        if data.get("is_error"):
            raise _classify_failure(str(data.get("result") or "") + stderr, proc.returncode)
        text = data.get("result")
        if not isinstance(text, str) or not text.strip():
            raise RuntimeErrorBase("Claude Code returned no text content.")

        usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
        tokens: Dict[str, Any] = {k: usage[k] for k in
                                  ("input_tokens", "output_tokens",
                                   "cache_read_input_tokens", "cache_creation_input_tokens")
                                  if isinstance(usage.get(k), (int, float))}
        if isinstance(data.get("total_cost_usd"), (int, float)):
            tokens["reported_cost_usd"] = data["total_cost_usd"]  # estimate, not billing
        used_model = model
        mu = data.get("modelUsage")
        if isinstance(mu, dict) and len(mu) == 1:
            used_model = str(next(iter(mu)))
        return AgentResult(text=text[:MAX_OUTPUT_CHARS], reasoning=None, model_id=used_model,
                           variant_id=None, cost=None, tokens=tokens,
                           duration_ms=int((time.time() - t0) * 1000), session_id=None)

    async def abort_task(self, session_id: str) -> None:
        """Kill an in-flight task. `session_id` is the task's session_title.
        No-op (logged) if nothing with that key is running."""
        proc = self._inflight.get(session_id)
        if proc is None:
            logger.info("abort_task: no in-flight Claude task for the given key.")
            return
        self._cancelled.add(session_id)
        await self._kill(proc)
