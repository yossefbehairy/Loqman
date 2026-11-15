"""
CodexCliRuntime: a deliberately BOUNDED AgentRuntime adapter over the local
OpenAI Codex CLI (`codex exec`), its documented non-interactive mode.

Created through ``engine_registry.create_engine_runtime("codex")``; nothing
here is imported unless Codex is selected.

What one task does (argv inspected from `codex exec --help`, v0.167.0)
  codex exec --json --ephemeral --sandbox read-only --skip-git-repo-check
             --ignore-user-config --cd <empty temp dir>
             -c approval_policy="never" [-c model_reasoning_effort="<v>"]
             --model <id> -
  The prompt is written to stdin ("-"), one process per task, JSONL events
  are parsed from stdout.

Explicit limitations (do not oversell this adapter)
  * NOT a tool-less mode. Codex has no flag that disables its shell tool.
    `--sandbox read-only` prevents workspace WRITES by the sandboxed shell, but
    the model can still READ files the sandbox exposes (read-only is not
    "no access"), and network behaviour is whatever the sandbox/CLI defaults
    are. The empty temp cwd is a convenience, not an access boundary.
    `approval_policy="never"` means nothing is escalated out of the sandbox.
    `--ignore-user-config` prevents loading config.toml (including MCP servers
    and profiles); exec-policy rules remain enabled and auth still
    comes from CODEX_HOME. Never uses --dangerously-bypass-*, --full-auto,
    --add-dir, --worktree, --oss.
  * Fail closed: before the first run the adapter reads `codex exec --help`
    and refuses to execute if any required flag is missing.
  * Event schema UNVERIFIED LIVE. `codex exec --json` events are not
    documented by the local CLI help or the app-server schema, and no live
    model call was made while building this. The parser follows the upstream
    event names (thread.started, item.completed/agent_message, turn.completed,
    turn.failed, error) and fails closed (raises) if no agent message is found.
    Verify against a real run before relying on it.
  * Stateless: --ephemeral, so no resume. `session_id` is always None.
  * No streaming: the full reply is returned when the process exits.
  * Model catalog comes from `codex debug models` (local, no model call),
    visible entries only, falling back to nothing if unavailable. Entries are
    status="unverified" and is_free=False: whether the account may use a model
    is only known when a task runs. Variants are the reasoning efforts the
    catalog lists, EXCLUDING "ultra" (it enables automatic task delegation,
    i.e. sub-agents, which this bounded adapter does not allow).
  * Billing is unknown. A ChatGPT login is subscription-metered with plan
    usage limits; an API key would be pay-per-token. OPENAI_API_KEY and
    CODEX_API_KEY are removed from the child environment by default so the
    CLI cannot silently switch to paid API billing. Token counts are exposed
    in AgentResult.tokens when reported; AgentResult.cost is unknown.
  * Timeout: wall-clock, enforced here; on expiry the child (and on Windows
    its process tree via taskkill) is killed. Cancel: `abort_task(key)` works
    only for an IN-FLIGHT task and `key` must be the task's `session_title`.
    Cancelling the awaiting asyncio task also kills the child. Kill is best
    effort; the CLI gives no confirmation that remote work stopped.
"""

import asyncio
from .process_io import communicate_bounded, RuntimeOutputLimit
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

from backend.pyworker.runtime.base import (
    AgentResult,
    ExecuteAgentTask,
    ModelCatalogEntry,
    ModelCost,
    RuntimeHealth,
)
from backend.pyworker.runtime.engine_discovery import (
    AUTHENTICATED,
    _extract_version,
    _parse_codex_auth,
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

logger = logging.getLogger("agentos.runtime.codex")

MAX_PROMPT_CHARS = 24_000
MAX_OUTPUT_CHARS = 60_000
PROBE_TIMEOUT_SECONDS = 15.0
MIN_TASK_TIMEOUT = 5
MAX_TASK_TIMEOUT = 600

# Flags this adapter relies on; all must appear in `codex exec --help` or we refuse.
REQUIRED_FLAGS = ("--json", "--ephemeral", "--sandbox", "--skip-git-repo-check",
                  "--ignore-user-config", "--cd", "--model")

# "ultra" = automatic task delegation (sub-agents): not allowed here.
FORBIDDEN_EFFORTS = frozenset({"ultra"})

_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-]{0,63}$")
_EFFORT_RE = re.compile(r"^[a-z]{2,12}$")
_STRIPPED_ENV = ("OPENAI_API_KEY", "CODEX_API_KEY")

CmdSpec = Union[str, Sequence[str]]


class RuntimeUnsupported(RuntimeErrorBase):
    code = "unsupported_capability"
    retryable = False


def _classify_failure(text: str, returncode: Optional[int]) -> RuntimeErrorBase:
    """Map CLI failure text to a normalized error WITHOUT echoing raw output."""
    low = (text or "").lower()
    if any(k in low for k in ("not logged in", "codex login", "unauthorized", "401",
                              "invalid api key", "authentication", "sign in")):
        return RuntimeAuthError("Codex is not authenticated. Run `codex login` yourself.")
    if any(k in low for k in ("429", "rate limit", "usage limit", "quota", "too many requests")):
        return RuntimeRateLimit("Codex rate/usage limit reached. No fallback was used.")
    if "model" in low and any(k in low for k in ("not found", "unknown", "invalid", "not supported",
                                                 "not available", "does not exist", "access")):
        return RuntimeModelUnavailable("Requested model is not available to this Codex account.")
    if any(k in low for k in ("econnrefused", "enotfound", "network", "connection", "timed out",
                              "dns")):
        return RuntimeOffline("Codex could not reach its service.")
    return RuntimeErrorBase(f"Codex execution failed (exit {returncode}).")


def parse_events(stdout: str) -> Dict[str, Any]:
    """Parse `codex exec --json` JSONL into {text, usage, error}.

    Non-JSON lines are ignored (the CLI may print banners). The reply text is
    the LAST item.completed whose item.type is agent_message. Fails closed by
    returning text=None when nothing usable is found.
    """
    text: Optional[str] = None
    usage: Dict[str, Any] = {}
    error: Optional[str] = None
    for raw in (stdout or "").splitlines():
        line = raw.strip().lstrip("﻿")
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if not isinstance(ev, dict):
            continue
        etype = ev.get("type")
        if etype == "item.completed" and isinstance(ev.get("item"), dict):
            item = ev["item"]
            if item.get("type") == "agent_message" and isinstance(item.get("text"), str):
                text = item["text"]
        elif etype == "turn.completed" and isinstance(ev.get("usage"), dict):
            usage = ev["usage"]
        elif etype in ("turn.failed", "error"):
            err = ev.get("error")
            msg = err.get("message") if isinstance(err, dict) else ev.get("message")
            error = str(msg or "unknown error")[:2000]
    return {"text": text, "usage": usage, "error": error}


class CodexCliRuntime:
    def __init__(self, cli_cmd: Optional[CmdSpec] = None, allow_api_key_env: bool = False):
        """cli_cmd: executable path or argv prefix (tests pass [python, fake.py])."""
        if cli_cmd is None:
            cli_cmd = shutil.which("codex") or None
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
            res = await self._probe(["exec", "--help"])
            text = ((res.stdout or "") + (res.stderr or "")) if res else ""
            self._flags_ok = bool(text) and all(f in text for f in REQUIRED_FLAGS)
            if not self._flags_ok:
                logger.warning("Installed codex CLI does not advertise all required flags.")
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
            logger.warning("codex process did not confirm exit after kill.")

    async def _catalog(self) -> List[Tuple[str, str, List[str]]]:
        """(slug, display_name, efforts) for user-visible models from the local
        catalog. `codex debug models` makes no model call. [] if unavailable."""
        res = await self._probe(["debug", "models"])
        if res is None or res.returncode != 0:
            return []
        try:
            data = json.loads(res.stdout or "")
        except ValueError:
            return []
        out: List[Tuple[str, str, List[str]]] = []
        models = data.get("models") if isinstance(data, dict) else None
        for m in models if isinstance(models, list) else []:
            if not isinstance(m, dict) or m.get("visibility") != "list":
                continue
            slug = m.get("slug")
            if not isinstance(slug, str) or not _MODEL_RE.match(slug):
                continue
            levels = m.get("supported_reasoning_levels")
            efforts = [lv["effort"] for lv in levels if isinstance(lv, dict)
                       and isinstance(lv.get("effort"), str)
                       and lv["effort"] not in FORBIDDEN_EFFORTS
                       and _EFFORT_RE.match(lv["effort"])] if isinstance(levels, list) else []
            out.append((slug, str(m.get("display_name") or slug)[:80], efforts))
        return out

    # -- health / models ---------------------------------------------------
    async def check_health(self) -> RuntimeHealth:
        """Install + login status only. Never starts a model turn."""
        if not self._cmd:
            return RuntimeHealth(status="offline", service_url=None,
                                 default_model_status="unavailable", models_count=0,
                                 free_models_count=0)
        ver = await self._probe(["--version"])
        if ver is None or (ver.returncode != 0 and not _extract_version(ver.stdout or "")):
            return RuntimeHealth(status="offline", default_model_status="unavailable",
                                 models_count=0, free_models_count=0)
        auth = await self._probe(["login", "status"])
        state = (_parse_codex_auth(auth.returncode, (auth.stdout or "") + (auth.stderr or ""))
                 if auth else "unknown")
        flags = await self._check_flags()
        count = len(await self._catalog())
        status = "ok" if (state == AUTHENTICATED and flags) else "degraded"
        return RuntimeHealth(status=status, service_url=None, default_model_status="unverified",
                             models_count=count, free_models_count=0)

    async def list_available_models(self, free_only: bool) -> List[ModelCatalogEntry]:
        """Visible models from the local Codex catalog, 'unverified' and never
        free (free_only=True therefore returns [])."""
        if free_only:
            return []
        return [ModelCatalogEntry(id=slug, name=name,
                                  provider_id="openai-codex", cost=ModelCost(),
                                  variants=efforts, is_free=False, status="unverified")
                for slug, name, efforts in await self._catalog()]

    # -- execution -----------------------------------------------------------
    async def execute_task(self, task: ExecuteAgentTask) -> AgentResult:
        if task.mcp_servers:
            raise RuntimeUnsupported("Scoped MCP tools are currently supported by the Claude adapter.")
        if not task.prompt or not task.prompt.strip():
            raise RuntimeErrorBase("Empty prompt: refusing to invoke the model.")
        if not self._cmd:
            raise RuntimeOffline("Codex CLI not found on PATH.")
        model = (task.model_id or "").strip()
        if not _MODEL_RE.match(model):
            raise RuntimeModelUnavailable("Invalid Codex model identifier.")
        effort: Optional[str] = None
        if task.variant not in (None, "", "default"):
            effort = str(task.variant).strip().lower()
            if effort in FORBIDDEN_EFFORTS:
                raise RuntimeUnsupported("Codex 'ultra' (automatic delegation) is not allowed.")
            if not _EFFORT_RE.match(effort):
                raise RuntimeUnsupported("Invalid Codex reasoning-effort variant.")
        if not await self._check_flags():
            raise RuntimeUnsupported(
                "Installed Codex CLI does not advertise the flags required for sandboxed "
                "non-interactive mode; refusing to run.")

        timeout = max(MIN_TASK_TIMEOUT, min(int(task.timeout or 90), MAX_TASK_TIMEOUT))
        prompt = task.prompt[:MAX_PROMPT_CHARS]
        key = task.session_title or f"task-{time.time_ns()}"
        if key in self._inflight:
            raise RuntimeErrorBase("A Codex task with this session_title is already running.")

        t0 = time.time()
        with tempfile.TemporaryDirectory(prefix="agentos-codex-") as cwd:
            argv = [*self._cmd, "exec", "--json", "--ephemeral", "--sandbox", "read-only",
                    "--skip-git-repo-check", "--ignore-user-config",
                    "--cd", cwd, "-c", 'approval_policy="never"']
            if effort:
                argv += ["-c", f'model_reasoning_effort="{effort}"']
            argv += ["--model", model, "-"]
            try:
                kwargs: Dict[str, Any] = {}
                if sys.platform == "win32":
                    kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
                proc = await asyncio.create_subprocess_exec(
                    *argv, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE, cwd=cwd, env=self._env(), **kwargs)
            except (FileNotFoundError, PermissionError, OSError):
                raise RuntimeOffline("Codex CLI could not be started.")
            self._inflight[key] = proc
            try:
                out_b, err_b = await asyncio.wait_for(
                    communicate_bounded(proc, prompt.encode("utf-8"), kill=self._kill), timeout=timeout)
            except RuntimeOutputLimit:
                await self._kill(proc)
                raise
            except asyncio.TimeoutError:
                await self._kill(proc)
                raise RuntimeTimeout(f"Codex did not finish within {timeout}s; process killed.")
            except asyncio.CancelledError:
                await self._kill(proc)
                raise
            finally:
                self._inflight.pop(key, None)
            was_cancelled = key in self._cancelled
            self._cancelled.discard(key)

        if was_cancelled:
            raise RuntimeCancelled("Codex task was cancelled.")
        stdout = out_b.decode("utf-8", errors="replace")
        stderr = err_b.decode("utf-8", errors="replace")
        parsed = parse_events(stdout)

        if proc.returncode != 0:
            raise _classify_failure((parsed["error"] or "") + "\n" + stderr[:2000],
                                    proc.returncode)
        if parsed["error"]:
            raise _classify_failure(parsed["error"], proc.returncode)
        text = parsed["text"]
        if not isinstance(text, str) or not text.strip():
            raise RuntimeErrorBase("Codex returned no agent message.")

        usage = parsed["usage"]
        tokens: Dict[str, Any] = {k: usage[k] for k in
                                  ("input_tokens", "cached_input_tokens", "output_tokens",
                                   "reasoning_output_tokens")
                                  if isinstance(usage.get(k), (int, float))}
        return AgentResult(text=text[:MAX_OUTPUT_CHARS], reasoning=None, model_id=model,
                           variant_id=effort, cost=None, tokens=tokens,
                           duration_ms=int((time.time() - t0) * 1000), session_id=None)

    async def abort_task(self, session_id: str) -> None:
        """Kill an in-flight task. `session_id` is the task's session_title.
        No-op (logged) if nothing with that key is running."""
        proc = self._inflight.get(session_id)
        if proc is None:
            logger.info("abort_task: no in-flight Codex task for the given key.")
            return
        self._cancelled.add(session_id)
        await self._kill(proc)
