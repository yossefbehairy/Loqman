"""
AgyCliRuntime: a deliberately BOUNDED AgentRuntime adapter over the local
Antigravity CLI (`agy`), using its headless print mode (`agy -p`).

Selected only with RUNTIME_MODE=agy. The default (RUNTIME_MODE=real) stays
OpenCode; nothing here is imported unless agy is selected. This is NOT ACP and
NOT Gemini CLI: it only uses flags observed in `agy --help` (v1.4.0).

What one task does
  agy --print <prompt> --disable-slash-commands --sandbox
      --model <id> [--effort <low|medium|high|xhigh|max>]
  stdin is /dev/null, cwd is a fresh empty temp directory, one process per
  task, stdout (plain text, the documented default --output-format) is the
  reply.

Why plain text, not --output-format json/stream-json
  The JSON / stream-json schemas are not documented by `--help` and no live
  model call was made while building this, so they are NOT parsed (no invented
  protocol). Plain text stdout is the only output contract relied on.

Explicit limitations (do not oversell this adapter)
  * NOT a sandbox and NOT workspace-isolated. `--sandbox` is passed (the CLI
    describes it only as "terminal restrictions"); its real strength is
    unverified. There is no flag to disable tools, MCP servers, plugins,
    hooks or the user's agy settings/rules, so those still load. The empty
    cwd is a convenience, not an access boundary. `--add-dir`, `--project`,
    `--new-project`, `--agent`, `--continue`, `--conversation`, `--mode`
    and `--dangerously-skip-permissions` are NEVER passed.
  * No automatic approvals. Nothing is passed that grants permissions and
    stdin is closed, so a permission prompt cannot be answered by us. How
    headless agy resolves an unanswered prompt (deny vs. wait) is not stated
    by --help; if it waits, the wall-clock timeout kills it (fail closed).
    --mode accept-edits/auto are deliberately unused (they auto-approve).
  * Fail closed: before the first run the adapter reads `agy --help` and
    refuses to execute if any required flag is missing.
  * Stateless as far as THIS adapter goes (session_id is always None, no
    resume), but agy has no "no persistence" flag, so the CLI may still store
    the conversation in its own history. Carry context in the prompt.
  * The prompt travels in argv (the only documented input path for -p), so it
    is visible in the OS process list and limited in size; it is truncated to
    MAX_PROMPT_CHARS. Slash-command expansion is disabled so prompt text such
    as "/foo" is not interpreted as a CLI command.
  * No streaming: the full reply is returned when the process exits.
  * Model catalog comes from `agy models` (tab separated "id<TAB>name", no
    model call). Entries are status="unverified", is_free=False: plan/credit
    billing is unknown, and the list mixes vendors. `variant` maps to
    --effort and only the values documented in --help are accepted.
  * Billing is unknown. GEMINI_API_KEY / GOOGLE_API_KEY are removed from the
    child environment by default so the CLI cannot silently switch to paid
    API-key billing. AgentResult.cost and tokens are unknown (empty).
  * Health never starts a model turn and cannot confirm login (there is no
    documented auth-status command): "ok" means installed + required flags +
    `agy models` returned entries; login and model availability are never confirmed.
  * Errors: exit 3 = model/agent failure with an `AGY_ERROR: {...}` stderr
    line, 2 = usage error, 1 = other. Only keyword classification is used and
    raw CLI output is never echoed into exceptions.
  * Timeout: wall-clock, enforced here; on expiry the child (and on Windows
    its process tree via taskkill) is killed. Cancel: `abort_task(key)` works
    only for an IN-FLIGHT task and `key` must be the task's `session_title`.
    Kill is best effort.
"""

import asyncio
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Any, Dict, List, Optional, Sequence, Union

from backend.pyworker.runtime.base import (
    AgentResult,
    ExecuteAgentTask,
    ModelCatalogEntry,
    ModelCost,
    RuntimeHealth,
)
from backend.pyworker.runtime.errors import (
    RuntimeAuthError,
    RuntimeCancelled,
    RuntimeErrorBase,
    RuntimeModelUnavailable,
    RuntimeOffline,
    RuntimeRateLimit,
    RuntimeTimeout,
)

logger = logging.getLogger("agentos.runtime.agy")

MAX_PROMPT_CHARS = 24_000      # argv on Windows is capped near 32k chars
MAX_OUTPUT_CHARS = 60_000
PROBE_TIMEOUT_SECONDS = 20.0
MIN_TASK_TIMEOUT = 5
MAX_TASK_TIMEOUT = 600

# Flags this adapter relies on; all must appear in `agy --help` or we refuse.
REQUIRED_FLAGS = ("--print", "--sandbox", "--model", "--effort", "--disable-slash-commands")
# Documented in `agy --help` for --effort.
EFFORT_VALUES = ("low", "medium", "high", "xhigh", "max")

_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-]{0,79}$")
_STRIPPED_ENV = ("GEMINI_API_KEY", "GOOGLE_API_KEY")

CmdSpec = Union[str, Sequence[str]]


class RuntimeUnsupported(RuntimeErrorBase):
    code = "unsupported_capability"
    retryable = False


def _classify_failure(text: str, returncode: Optional[int]) -> RuntimeErrorBase:
    """Map CLI failure text to a normalized error WITHOUT echoing raw output."""
    low = (text or "").lower()
    if any(k in low for k in ("not logged in", "login", "unauthenticated", "unauthorized",
                              "permission_denied", "401", "authenticat")):
        return RuntimeAuthError("agy is not authenticated. Sign in with the agy CLI yourself.")
    if any(k in low for k in ("429", "rate limit", "quota", "credits balance", "resource_exhausted")):
        return RuntimeRateLimit("agy rate/quota/credit limit reached. No fallback was used.")
    if "model" in low and any(k in low for k in ("not found", "unknown", "invalid", "not available",
                                                 "unsupported", "effort")):
        return RuntimeModelUnavailable("Requested model is not available to this agy account.")
    if any(k in low for k in ("econnrefused", "enotfound", "network", "connection", "unavailable")):
        return RuntimeOffline("agy could not reach its service.")
    return RuntimeErrorBase(f"agy execution failed (exit {returncode}).")


def parse_models(stdout: str) -> List[Dict[str, str]]:
    """Parse `agy models` output: lines of '<id>\\t<display name>'.

    Lines without a tab or with an invalid id (e.g. the 'Fetching ...' banner)
    are ignored. Duplicates are dropped.
    """
    out: List[Dict[str, str]] = []
    seen = set()
    for line in (stdout or "").splitlines():
        if "\t" not in line:
            continue
        mid, name = (p.strip() for p in line.split("\t", 1))
        if not _MODEL_RE.match(mid) or mid in seen:
            continue
        seen.add(mid)
        out.append({"id": mid, "name": name or mid})
    return out


class AgyCliRuntime:
    def __init__(self, cli_cmd: Optional[CmdSpec] = None, allow_api_key_env: bool = False):
        """cli_cmd: executable path or argv prefix (tests pass [python, fake.py])."""
        if cli_cmd is None:
            cli_cmd = shutil.which("agy") or None
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
                logger.warning("Installed agy CLI does not advertise all required flags.")
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
            logger.warning("agy process did not confirm exit after kill.")

    async def _models_raw(self) -> List[Dict[str, str]]:
        res = await self._probe(["models"])
        if res is None or res.returncode != 0:
            return []
        return parse_models(res.stdout or "")

    # -- health / models ---------------------------------------------------
    async def check_health(self) -> RuntimeHealth:
        """Install + flags + model listing. Never starts a model turn."""
        if not self._cmd:
            return RuntimeHealth(status="offline", service_url=None,
                                 default_model_status="unavailable", models_count=0,
                                 free_models_count=0)
        ver = await self._probe(["--version"])
        if ver is None or ver.returncode != 0 or not (ver.stdout or "").strip():
            return RuntimeHealth(status="offline", default_model_status="unavailable",
                                 models_count=0, free_models_count=0)
        await self._check_flags()
        models = await self._models_raw()
        # The CLI has no verified non-interactive authentication status probe.
        status = "degraded"
        return RuntimeHealth(status=status, service_url=None, default_model_status="unverified",
                             models_count=len(models), free_models_count=0)

    async def list_available_models(self, free_only: bool = True) -> List[ModelCatalogEntry]:
        """Entries from `agy models`; never free (billing unknown), so
        free_only=True returns []."""
        if free_only or not self._cmd:
            return []
        return [ModelCatalogEntry(id=m["id"], name=m["name"], provider_id="antigravity",
                                  cost=ModelCost(), variants=list(EFFORT_VALUES),
                                  is_free=False, status="unverified")
                for m in await self._models_raw()]

    # -- execution -----------------------------------------------------------
    async def execute_task(self, task: ExecuteAgentTask) -> AgentResult:
        if not task.prompt or not task.prompt.strip():
            raise RuntimeErrorBase("Empty prompt: refusing to invoke the model.")
        if not self._cmd:
            raise RuntimeOffline("agy CLI not found on PATH.")
        variant = task.variant if task.variant not in (None, "", "default") else None
        if variant is not None and variant not in EFFORT_VALUES:
            raise RuntimeUnsupported("agy adapter only supports variants: " + ", ".join(EFFORT_VALUES))
        model = (task.model_id or "").strip()
        if not _MODEL_RE.match(model):
            raise RuntimeModelUnavailable("Invalid agy model identifier.")
        if not await self._check_flags():
            raise RuntimeUnsupported(
                "Installed agy CLI does not advertise the flags required for headless "
                "print mode; refusing to run.")

        timeout = max(MIN_TASK_TIMEOUT, min(int(task.timeout or 90), MAX_TASK_TIMEOUT))
        prompt = task.prompt[:MAX_PROMPT_CHARS]
        argv = [*self._cmd, "--print", prompt, "--disable-slash-commands", "--sandbox",
                "--model", model]
        if variant:
            argv += ["--effort", variant]
        key = task.session_title or f"task-{time.time_ns()}"
        if key in self._inflight:
            raise RuntimeErrorBase("An agy task with this session_title is already running.")

        t0 = time.time()
        with tempfile.TemporaryDirectory(prefix="agentos-agy-") as cwd:
            try:
                kwargs: Dict[str, Any] = {}
                if sys.platform == "win32":
                    kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
                proc = await asyncio.create_subprocess_exec(
                    *argv, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE, cwd=cwd, env=self._env(), **kwargs)
            except (FileNotFoundError, PermissionError, OSError):
                raise RuntimeOffline("agy CLI could not be started.")
            self._inflight[key] = proc
            try:
                out_b, err_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            except asyncio.TimeoutError:
                await self._kill(proc)
                raise RuntimeTimeout(f"agy did not finish within {timeout}s; process killed.")
            except asyncio.CancelledError:
                await self._kill(proc)
                raise
            finally:
                self._inflight.pop(key, None)
            was_cancelled = key in self._cancelled
            self._cancelled.discard(key)

        if was_cancelled:
            raise RuntimeCancelled("agy task was cancelled.")
        stdout = out_b.decode("utf-8", errors="replace")
        stderr = err_b.decode("utf-8", errors="replace")

        if proc.returncode != 0:
            raise _classify_failure(stderr + "\n" + stdout[:2000], proc.returncode)
        # A structured error line can accompany exit 0 only by contract
        # violation; treat it as failure rather than returning it as a reply.
        if "AGY_ERROR:" in stderr:
            raise _classify_failure(stderr, proc.returncode)

        text = stdout.strip().lstrip("﻿").strip()
        if not text:
            raise RuntimeErrorBase("agy returned no text content.")
        return AgentResult(text=text[:MAX_OUTPUT_CHARS], reasoning=None, model_id=model,
                           variant_id=variant, cost=None, tokens={},
                           duration_ms=int((time.time() - t0) * 1000), session_id=None)

    async def abort_task(self, session_id: str) -> None:
        """Kill an in-flight task. `session_id` is the task's session_title.
        No-op (logged) if nothing with that key is running."""
        proc = self._inflight.get(session_id)
        if proc is None:
            logger.info("abort_task: no in-flight agy task for the given key.")
            return
        self._cancelled.add(session_id)
        await self._kill(proc)
