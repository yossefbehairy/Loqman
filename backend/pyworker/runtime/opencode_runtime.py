"""
RealOpenCodeRuntime Implementation (repaired).

Transport: `opencode api <METHOD> <PATH>` CLI (the supported local transport
for this MVP — the installed opencode 2.0.29 exposes the model/session API
through it, verified against live `opencode api GET /api/model`).

Docs consulted:
- https://opencode.ai/docs/sdk/   (createOpencode / createOpencodeClient, session.* APIs)
- https://opencode.ai/docs/server/ (serve + HTTP API surface, auth via env)
- https://opencode.ai/docs/models/  (provider/model identifiers)

Notes:
- The JS SDK is TypeScript-only; the Python backend uses the documented
  HTTP-equivalent surface via the CLI transport instead of inventing endpoints.
- Model identifiers are never hard-coded: health + free-list come from the
  live catalog, and every task revalidates its model before execution.
- CLI calls use asyncio subprocesses so they can be killed on cancel/timeout.
- LIMITATIONS (verified against `opencode api --help`): the only body option is
  `--data/-d <string>` — no stdin or file input — so request bodies travel on argv
  (visible to same-user local processes; fail-closed above MAX_CMDLINE_CHARS).
  Remote interrupt is best effort; the CLI never confirms generation stopped.
"""

import asyncio
import json
import logging
import platform
import os
import re
import shutil
import subprocess
import sys
import time
import contextvars
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from backend.pyworker.appsettings import settings
from backend.pyworker.runtime.base import (
    ModelCatalogEntry,
    ModelCost,
    RuntimeHealth,
    ExecuteAgentTask,
    AgentResult,
)
from backend.pyworker.runtime.errors import (
    RuntimeErrorBase,
    RuntimeOffline,
    RuntimeTimeout,
    RuntimeCancelled,
    RuntimeModelUnavailable,
    normalize_cli_error,
)

logger = logging.getLogger("agentos.runtime.opencode")

MAX_PROMPT_CHARS = 24_000
MAX_OUTPUT_CHARS = 60_000
# Model catalog changes rarely; every fetch spawns a CLI subprocess, so reuse
# fresh results briefly across the validation calls of a single user request.
MODEL_CATALOG_TTL_SEC = 60.0
# Windows CreateProcess caps the whole command line at 32,767 chars. `opencode api`
# has no stdin/file body option (only `-d <string>`), so bodies ride on argv; leave
# headroom and refuse larger requests rather than truncating or leaking via error.
# LIMITATION: argv is visible to other local processes of the same user while the
# request runs. There is no supported way around this with the current CLI.
MAX_CMDLINE_CHARS = 30_000


@dataclass(eq=False)
class _ActiveRun:
    """One in-flight execute_task: maps AgentOS' key (session_title) to the real
    OpenCode session id and the CLI processes currently serving it."""
    key: str
    session_id: Optional[str] = None
    cancelled: bool = False
    procs: set = field(default_factory=set)


_CURRENT_RUN: "contextvars.ContextVar[Optional[_ActiveRun]]" = contextvars.ContextVar(
    "opencode_current_run", default=None)


def _redact(text: str) -> str:
    # Never log credentials / tokens.
    lowered = text or ""
    for key in ("apikey", "api_key", "sk-", "bearer "):
        if key in lowered.lower():
            return "[redacted runtime error]"
    return text[:500]


# Conversation threads (PR6): session titles with these prefixes are REUSED
# across calls, so the OpenCode session's own memory carries the conversation.
# Worker/mission one-shots stay stateless by design (fresh context each run).
THREAD_TITLE_PREFIXES = ("consult-", "ceo-ask-", "ceo-fast-")


class RealOpenCodeRuntime:
    def __init__(self, base_url: Optional[str] = None):
        self.base_url = (base_url or settings.OPENCODE_BASE_URL).rstrip("/")
        if platform.system() == "Windows":
            npm_exe = os.path.expandvars(r"%APPDATA%\npm\node_modules\opencode-ai\bin\opencode.exe")
            if os.path.exists(npm_exe):
                self.cli_cmd = npm_exe
            else:
                self.cli_cmd = shutil.which("opencode.exe") or shutil.which("opencode") or "opencode.cmd"
        else:
            self.cli_cmd = shutil.which("opencode") or "opencode"
        self._cached_models: List[ModelCatalogEntry] = []
        self._last_refresh: float = 0.0
        # Thread survival (PR6): stable-title sessions are REUSED so conversations
        # keep their OpenCode memory across calls and app restarts.
        self._thread_sessions: Dict[str, str] = {}
        # Short-lived catalog cache per free_only flag: the catalog barely changes
        # minute-to-minute, and every fetch spawns a CLI subprocess.
        self._catalog_cache: Dict[bool, tuple] = {}
        # In-flight runs by AgentOS key (task.session_title) -> real session/process.
        self._active: Dict[str, List[_ActiveRun]] = {}

    # -- transport ---------------------------------------------------------
    @staticmethod
    def _kill_tree_sync(pid: int) -> None:
        if sys.platform == "win32":
            try:
                subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True,
                               timeout=10, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            except Exception:
                pass

    async def _kill_proc(self, proc: "asyncio.subprocess.Process") -> None:
        """Terminate a CLI process (and its tree on Windows). Only the LOCAL
        process is guaranteed gone; this says nothing about the remote session."""
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
            logger.warning("opencode CLI process did not confirm exit after kill.")

    @staticmethod
    def _check_argv_size(args: List[str]) -> None:
        """Fail closed instead of letting CreateProcess/exec reject (or truncate) argv.

        `opencode api` only documents `--data/-d <string>`: there is NO stdin or
        data-file option, so the request body must travel on the command line.
        """
        # CreateProcess measures UTF-16 code units after Windows quoting.
        total = len(subprocess.list2cmdline(args).encode("utf-16-le")) // 2
        if total > MAX_CMDLINE_CHARS:
            raise RuntimeErrorBase(
                f"OpenCode request body too large for the command-line transport "
                f"({total} > {MAX_CMDLINE_CHARS} chars); refusing to send. "
                "Shorten the prompt."
            )

    async def _run_cli_api(
        self, method: str, path: str, data: Optional[Dict[str, Any]] = None, timeout: int = 90
    ) -> Dict[str, Any]:
        args = [self.cli_cmd, "api", method, path]
        if data is not None:
            # ensure_ascii=False keeps non-Latin prompts ~6x smaller than \uXXXX escapes.
            args.extend(["-d", json.dumps(data, ensure_ascii=False)])
        self._check_argv_size(args)
        env = {k: v for k, v in os.environ.items() if k != "AGENTOS_LOCAL_TOKEN"}
        kwargs: Dict[str, Any] = {}
        if sys.platform == "win32":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        run = _CURRENT_RUN.get()
        if run is not None and run.cancelled:
            raise RuntimeCancelled("OpenCode run was cancelled.")
        try:
            proc = await asyncio.create_subprocess_exec(
                *args, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE, env=env, **kwargs)
        except (FileNotFoundError, PermissionError):
            raise RuntimeOffline("OpenCode CLI not found. Install opencode v2 and ensure it is on PATH.")
        except NotImplementedError:
            raise RuntimeOffline("Event loop cannot spawn subprocesses (Windows needs the Proactor loop).")
        if run is not None:
            run.procs.add(proc)
        try:
            out_b, err_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            await self._kill_proc(proc)
            raise RuntimeTimeout(f"OpenCode request timed out after {timeout}s ({method} {path}).")
        except asyncio.CancelledError:
            await asyncio.shield(self._kill_proc(proc))
            raise RuntimeCancelled("OpenCode request was cancelled.")
        finally:
            if run is not None:
                run.procs.discard(proc)
        if run is not None and run.cancelled:
            raise RuntimeCancelled("OpenCode run was cancelled.")
        stdout = out_b.decode("utf-8", errors="replace")
        stderr = err_b.decode("utf-8", errors="replace")
        if proc.returncode != 0:
            err = (stderr or stdout or "").strip()
            logger.error("opencode api failed (exit %s)", proc.returncode)
            raise normalize_cli_error(err, proc.returncode)
        out = stdout.strip()
        if not out:
            return {}
        try:
            # CLI prints UTF-16 on Windows in some setups; handle BOM gracefully.
            if b"\x00" in out_b[:4]:
                out = out_b.decode("utf-16", errors="ignore").strip()
            return json.loads(out) if out else {}
        except Exception as e:
            raise RuntimeErrorBase(f"Unparseable OpenCode response for {method} {path}: {e}")

    async def request_api(self, method: str, path: str, data: Optional[Dict[str, Any]] = None,
                          timeout: int = 180) -> Any:
        """Forward a narrowly allowlisted SDK request through the local OpenCode CLI.

        The renderer SDK speaks the legacy ``/session/{id}/message`` prompt surface,
        while opencode v2 exposes ``/api/session`` + ``/api/session/{id}/prompt``.
        We translate the prompt call here (POST message -> prompt + wait) and return
        a message payload the SDK/front-end can read directly.
        """
        method = method.upper()
        # Validate the complete path, including query strings, before forwarding.
        # A prefix check lets POST /api/session?x bypass session creation checks.
        allowed = (
            method == "POST" and (path == "/api/session" or re.fullmatch(r"/api/session/[A-Za-z0-9_-]+/message", path))
        ) or (
            method == "GET" and re.fullmatch(r"/api/session(?:/[A-Za-z0-9_-]+(?:/message)?)?", path)
        )
        if not allowed:
            raise RuntimeModelUnavailable("OpenCode SDK request is not allowed by the local proxy.")

        allowed_agents = {settings.CHAT_AGENT_ID, settings.WORK_AGENT_ID}
        if method == "POST" and path == "/api/session":
            # The renderer may only open sessions with AgentOS' locked agents.
            # "build" (unrestricted tools) is rejected here, not just hidden in the UI.
            data = dict(data or {})
            agent_id = str(data.get("agent") or settings.CHAT_AGENT_ID)
            if agent_id not in allowed_agents:
                raise RuntimeModelUnavailable(
                    f"OpenCode agent '{agent_id}' is not allowed by AgentOS. "
                    f"Allowed agents: {sorted(allowed_agents)}."
                )
            data["agent"] = agent_id

        prompt_match = re.fullmatch(r"/api/session/([A-Za-z0-9_-]+)/message", path)
        if method == "POST" and prompt_match:
            session_id = prompt_match.group(1)
            text = ""
            parts = (data or {}).get("parts")
            if isinstance(parts, list):
                for part in parts:
                    if isinstance(part, dict) and part.get("type") == "text":
                        text += part.get("text") or ""
            if not text.strip():
                text = str((data or {}).get("text") or "")
            if not text.strip():
                raise RuntimeErrorBase("Empty prompt: refusing to invoke the model.")
            model_ref = (data or {}).get("model") or {}
            assistant = await self._prompt_and_wait(
                session_id=session_id,
                text=text[:MAX_PROMPT_CHARS],
                model_ref=model_ref,
                timeout=timeout,
            )
            reply = self._assistant_text(assistant)
            if not reply.strip():
                raise RuntimeErrorBase(f"No text content returned from session {session_id}.")
            return {
                "data": {
                    "info": {
                        "id": assistant.get("id"),
                        "role": "assistant",
                        "model": assistant.get("model"),
                    },
                    # `parts` (SDK prompt shape) and `content` (v2 message shape) both
                    # carry the text so either reader finds it.
                    "parts": [{"type": "text", "text": reply[:MAX_OUTPUT_CHARS]}],
                    "content": assistant.get("content") or [{"type": "text", "text": reply[:MAX_OUTPUT_CHARS]}],
                    "cost": assistant.get("cost", 0.0),
                    "tokens": assistant.get("tokens", {}),
                }
            }
        return await self._run_cli_api(method, path, data, timeout=timeout)

    # -- session helpers ---------------------------------------------------
    @staticmethod
    def _assistant_text(message: Optional[Dict[str, Any]]) -> str:
        """Extract concatenated assistant text from a v2 or legacy message payload."""
        if not isinstance(message, dict):
            return ""
        chunks: List[str] = []
        content = message.get("content")
        if isinstance(content, list):
            for c in content:
                if isinstance(c, dict) and c.get("type") == "text":
                    chunks.append(c.get("text") or "")
        parts = message.get("parts")
        if isinstance(parts, list):
            for p in parts:
                if isinstance(p, dict) and p.get("type") == "text":
                    chunks.append(p.get("text") or "")
        if not chunks and isinstance(message.get("text"), str):
            chunks.append(message["text"])
        return "".join(chunks)

    @staticmethod
    def _created_at(message: Dict[str, Any]) -> int:
        try:
            return int((message.get("time") or {}).get("created") or 0)
        except Exception:
            return 0

    def _latest_assistant(self, messages: List[Any], after_ts: int = 0) -> Optional[Dict[str, Any]]:
        best: Optional[Dict[str, Any]] = None
        best_ts = -1
        for m in messages or []:
            if not isinstance(m, dict) or m.get("type") != "assistant":
                continue
            ts = self._created_at(m)
            if after_ts and ts and ts + 5 < after_ts:
                continue
            if ts >= best_ts:
                best, best_ts = m, ts
        return best

    def _idle_marker(self, messages: List[Any], after_ts: int = 0) -> Optional[Dict[str, Any]]:
        for m in messages or []:
            if isinstance(m, dict) and m.get("type") == "idle":
                if not after_ts or self._created_at(m) + 5 >= after_ts:
                    return m
        return None

    async def _wait_for_assistant(self, session_id: str, after_ts: int, timeout: int) -> Dict[str, Any]:
        """Poll a session until its agent loop finishes and an assistant reply exists."""
        deadline = time.time() + max(timeout, 15)
        last_assistant: Optional[Dict[str, Any]] = None
        while True:
            msg_res = await self._run_cli_api("GET", f"/api/session/{session_id}/message", timeout=30)
            messages = msg_res.get("data", []) if isinstance(msg_res, dict) else []
            if not isinstance(messages, list):
                messages = []
            assistant = self._latest_assistant(messages, after_ts)
            if assistant is not None:
                error = assistant.get("error")
                if error:
                    raise RuntimeErrorBase(f"OpenCode model error: {str(error)[:300]}")
                last_assistant = assistant
                completed = bool((assistant.get("time") or {}).get("completed"))
                if completed and self._assistant_text(assistant).strip():
                    return assistant
                if self._assistant_text(assistant).strip() and self._idle_marker(messages, after_ts):
                    return assistant
            else:
                idle = self._idle_marker(messages, after_ts)
                if idle is not None and idle.get("outcome") not in (None, "succeeded"):
                    raise RuntimeErrorBase(
                        f"OpenCode session ended without output (outcome={idle.get('outcome')})."
                    )
            if time.time() > deadline:
                # Partial / still-streaming text is NOT a success: only a completed
                # message (or idle marker) returns above.
                state = "partial text only" if last_assistant is not None else "no reply"
                raise RuntimeTimeout(f"OpenCode session {session_id} did not finish within {timeout}s ({state}).")
            await asyncio.sleep(0.5)

    async def _prompt_and_wait(self, session_id: str, text: str, model_ref: Optional[Dict[str, Any]] = None,
                               timeout: int = 180) -> Dict[str, Any]:
        """Send one prompt to an existing session and wait for the assistant reply."""
        model_ref = model_ref or {}
        model_id = model_ref.get("modelID") or model_ref.get("id")
        if model_id:
            switch = {"model": {"id": model_id, "providerID": model_ref.get("providerID") or "opencode"}}
            if model_ref.get("variant"):
                switch["model"]["variant"] = model_ref["variant"]
            await self._run_cli_api("POST", f"/api/session/{session_id}/model", switch, timeout=30)
        # Baseline: ignore assistant replies that already existed before this prompt
        # (a reused chat session must not return the previous turn's answer).
        baseline = 0
        try:
            pre = await self._run_cli_api("GET", f"/api/session/{session_id}/message", timeout=30)
            for m in (pre.get("data") or []) if isinstance(pre, dict) else []:
                if isinstance(m, dict):
                    baseline = max(baseline, self._created_at(m))
        except Exception:
            baseline = 0
        prompt_res = await self._run_cli_api(
            "POST", f"/api/session/{session_id}/prompt", {"text": text}, timeout=30
        )
        prompt_ts = self._created_at((prompt_res or {}).get("data") or {}) if isinstance(prompt_res, dict) else 0
        after_ts = max(prompt_ts, baseline)
        return await self._wait_for_assistant(session_id, after_ts, timeout)

    # -- health / models ---------------------------------------------------
    async def check_health(self) -> RuntimeHealth:
        try:
            models_data = await self._run_cli_api("GET", "/api/model", timeout=15)
            all_models = models_data.get("data", [])
            free_models = await self.list_available_models(free_only=True)
            has_default = any(m.id == settings.DEFAULT_MODEL_ID for m in free_models)
            return RuntimeHealth(
                status="ok",
                service_url=self.base_url,
                default_model_status="available" if has_default else "unavailable",
                models_count=len(all_models),
                free_models_count=len(free_models),
            )
        except RuntimeErrorBase as e:
            logger.error("Health check failed: %s", _redact(str(e)))
            return RuntimeHealth(
                status="offline",
                service_url=self.base_url,
                default_model_status="unavailable",
                models_count=0,
                free_models_count=0,
            )
        except Exception as e:
            logger.error("Health check failed: %s", _redact(str(e)))
            return RuntimeHealth(
                status="offline",
                service_url=self.base_url,
                default_model_status="unavailable",
                models_count=0,
                free_models_count=0,
            )

    @staticmethod
    def _entry_from_raw(m: Dict[str, Any]) -> ModelCatalogEntry:
        """Normalize one catalog entry with FAIL-CLOSED free eligibility.

        A model counts as free only when BOTH hold:
          1. its provider is explicitly allowlisted, and
          2. it declares explicit 0/0 pricing in the catalog.

        Custom providers (a keyed gateway configured outside the free catalog)
        report no pricing at all. Treating "no cost" as "0.0" would silently
        route paid traffic, so missing/unknown pricing is NOT free.
        """
        provider_id = m.get("providerID", "opencode")

        cost_arr = m.get("cost")
        declared_cost = False
        inp_cost, out_cost = 0.0, 0.0
        if isinstance(cost_arr, list) and cost_arr:
            first = cost_arr[0] or {}
            if isinstance(first, dict) and "input" in first and "output" in first:
                try:
                    inp_cost = float(first.get("input"))
                    out_cost = float(first.get("output"))
                    declared_cost = True
                except (TypeError, ValueError):
                    declared_cost = False

        zero_cost = declared_cost and inp_cost == 0.0 and out_cost == 0.0
        provider_allowed = provider_id in set(settings.FREE_PROVIDER_ALLOWLIST)
        is_free = zero_cost and provider_allowed

        if not declared_cost:
            logger.debug("Model %s excluded: no declared pricing.", m.get("id"))
        elif not zero_cost:
            logger.debug("Model %s excluded: non-zero pricing.", m.get("id"))
        elif not provider_allowed:
            logger.debug(
                "Model %s excluded: provider '%s' is not in FREE_PROVIDER_ALLOWLIST.",
                m.get("id"), provider_id,
            )

        variants = [v.get("id") for v in (m.get("variants") or []) if isinstance(v, dict) and "id" in v]
        is_active = m.get("status") == "active" and m.get("enabled", True)
        return ModelCatalogEntry(
            id=m.get("id") or m.get("modelID", "unknown"),
            name=m.get("name", m.get("id", "Unknown")),
            provider_id=provider_id,
            cost=ModelCost(input=inp_cost, output=out_cost),
            variants=variants,
            is_free=is_free,
            status="active" if is_active else "disabled",
        )

    async def list_available_models(self, free_only: bool = True) -> List[ModelCatalogEntry]:
        try:
            cached = self._catalog_cache.get(free_only)
            if cached and (time.time() - cached[0]) < MODEL_CATALOG_TTL_SEC:
                return list(cached[1])
            models_data = await self._run_cli_api("GET", "/api/model", timeout=20)
            raw_models = models_data.get("data", [])
            entries: List[ModelCatalogEntry] = []
            for m in raw_models:
                try:
                    entry = self._entry_from_raw(m)
                except Exception:
                    continue
                if free_only:
                    if entry.is_free and entry.status == "active":
                        entries.append(entry)
                else:
                    entries.append(entry)
            self._cached_models = entries
            self._last_refresh = time.time()
            self._catalog_cache[free_only] = (self._last_refresh, list(entries))
            return entries
        except RuntimeErrorBase as e:
            logger.error("Failed to fetch model catalog: %s", _redact(str(e)))
            return list(self._cached_models)
        except Exception as e:
            logger.error("Failed to fetch model catalog: %s", _redact(str(e)))
            return list(self._cached_models)

    async def resolve_model(self, model_id: str) -> ModelCatalogEntry:
        """Revalidate a model against the live free catalog before execution."""
        free_models = await self.list_available_models(free_only=True)
        for m in free_models:
            if m.id == model_id:
                return m
        raise RuntimeModelUnavailable(
            f"Model '{model_id}' is not confirmed free: it must declare explicit 0/0 pricing "
            f"from an allowlisted provider {sorted(settings.FREE_PROVIDER_ALLOWLIST)}. "
            "No paid fallback used."
        )

    # -- execution ----------------------------------------------------------
    async def _create_session(self, title: str, agent_id: str, model_ref: Dict[str, Any]) -> str:
        sess_res = await self._run_cli_api(
            "POST",
            "/api/session",
            {"title": title, "agent": agent_id, "model": model_ref},
            timeout=30,
        )
        session_id = (sess_res.get("data") or {}).get("id")
        if not session_id:
            raise RuntimeErrorBase("OpenCode did not return a session id.")
        return str(session_id)

    async def _find_thread_by_title(self, title: str) -> Optional[str]:
        """Find an OpenCode session previously opened with this exact title.

        Makes conversation threads durable across app restarts without a local
        session table. Shape-tolerant: different OpenCode versions have returned
        a bare list and {"data": [...]}; an unexpected shape fails OPEN (returns
        None) so the caller simply opens a fresh session — never a regression.
        """
        try:
            res = await self._run_cli_api("GET", "/api/session", None, timeout=30)
        except (RuntimeTimeout, RuntimeErrorBase):
            return None
        sessions = res.get("data") if isinstance(res, dict) else res
        if isinstance(sessions, dict) and isinstance(sessions.get("data"), list):
            sessions = sessions["data"]
        if not isinstance(sessions, list):
            return None
        for s in sessions:
            if isinstance(s, dict) and s.get("title") == title and s.get("id"):
                return str(s["id"])
        return None

    async def _get_or_create_session(self, title: str, agent_id: str, model_ref: Dict[str, Any]) -> tuple:
        """Reuse a conversation thread by its stable title, or open one fresh.

        Returns (session_id, reused). Titles outside THREAD_TITLE_PREFIXES are
        worker one-shots and always get a fresh stateless session. A cached id
        is re-validated against the durable title lookup each turn, so a
        externally-deleted session repairs itself instead of failing one turn.
        """
        if not title or not title.startswith(THREAD_TITLE_PREFIXES):
            return await self._create_session(title, agent_id, model_ref), False
        cached = self._thread_sessions.get(title)
        if cached:
            found = await self._find_thread_by_title(title)
            if found:
                self._thread_sessions[title] = found
                return found, True
            self._thread_sessions.pop(title, None)  # stale: open a replacement
        else:
            found = await self._find_thread_by_title(title)
            if found:
                self._thread_sessions[title] = found
                return found, True
        session_id = await self._create_session(title, agent_id, model_ref)
        self._thread_sessions[title] = session_id
        return session_id, False

    async def execute_task(self, task: ExecuteAgentTask) -> AgentResult:
        if not task.prompt or not task.prompt.strip():
            raise RuntimeErrorBase("Empty prompt: refusing to invoke the model.")

        # Agent lockdown: the app only runs its own locked agents. "build" can
        # shell out and edit files, so it is rejected before any I/O happens.
        agent_id = (task.agent_id or settings.WORK_AGENT_ID).strip()
        allowed_agents = {settings.CHAT_AGENT_ID, settings.WORK_AGENT_ID}
        if agent_id not in allowed_agents:
            raise RuntimeModelUnavailable(
                f"OpenCode agent '{agent_id}' is not allowed by AgentOS. "
                f"Allowed agents: {sorted(allowed_agents)}."
            )

        prompt = task.prompt[:MAX_PROMPT_CHARS]
        if len(task.prompt) > MAX_PROMPT_CHARS:
            logger.warning("Prompt truncated from %d to %d chars", len(task.prompt), MAX_PROMPT_CHARS)

        # Revalidate model right before execution (free-only).
        catalog_entry = await self.resolve_model(task.model_id)

        t0 = time.time()
        # The OpenCode catalog is authoritative for the provider. A caller's
        # default provider value must not misroute models from other providers.
        model_ref: Dict[str, Any] = {"providerID": catalog_entry.provider_id, "id": task.model_id}
        if task.variant:
            model_ref["variant"] = task.variant

        title = task.session_title or ""
        run = _ActiveRun(key=title or f"task-{time.time_ns()}")
        self._active.setdefault(run.key, []).append(run)
        token = _CURRENT_RUN.set(run)
        try:
            try:
                session_id, reused = await self._get_or_create_session(
                    title=title, agent_id=agent_id, model_ref=model_ref
                )
                run.session_id = session_id
                if run.cancelled:  # abort arrived before the session id was known
                    raise RuntimeCancelled("OpenCode run was cancelled.")
                try:
                    assistant = await self._prompt_and_wait(
                        session_id=session_id,
                        text=prompt,
                        model_ref=model_ref,
                        timeout=task.timeout,
                    )
                except BaseException:
                    # Evict so the NEXT turn re-resolves by title: the durable
                    # lookup re-finds a live thread or opens a clean replacement.
                    # (Safe to duplicate work — the title lookup is authoritative.)
                    if reused:
                        self._thread_sessions.pop(title, None)
                    raise
                if run.cancelled:  # a late reply after abort must not count as success
                    raise RuntimeCancelled("OpenCode run was cancelled.")
            except (RuntimeTimeout, RuntimeCancelled) as exc:
                # Timed out or cancelled: the remote session may still be working.
                # Ask it to stop (best effort, unconfirmed).
                if run.session_id:
                    await asyncio.shield(self._interrupt_session(run.session_id))
                raise
            except RuntimeErrorBase:
                raise
            except Exception as e:
                raise RuntimeOffline(f"OpenCode transport failure: {_redact(str(e))}")
        finally:
            _CURRENT_RUN.reset(token)
            runs = self._active.get(run.key, [])
            if run in runs:
                runs.remove(run)
            if not runs:
                self._active.pop(run.key, None)

        assistant_text = self._assistant_text(assistant)
        if not assistant_text.strip():
            raise RuntimeErrorBase(f"No text content returned from session {session_id}.")

        reasoning_text: Optional[str] = None
        content = assistant.get("content")
        if isinstance(content, list):
            for c in content:
                if isinstance(c, dict) and c.get("type") == "reasoning":
                    reasoning_text = c.get("text", "") or reasoning_text

        assistant_model = assistant.get("model") or {}
        duration_ms = int((time.time() - t0) * 1000)
        recorded_model = task.model_id
        if isinstance(assistant_model, dict):
            recorded_model = assistant_model.get("id") or assistant_model.get("modelID") or recorded_model
        recorded_variant = task.variant
        if isinstance(assistant_model, dict) and assistant_model.get("variant"):
            recorded_variant = assistant_model.get("variant")

        assistant_text = assistant_text[:MAX_OUTPUT_CHARS]
        return AgentResult(
            text=assistant_text,
            reasoning=reasoning_text,
            model_id=recorded_model,
            variant_id=recorded_variant or "default",
            cost=float(assistant["cost"]) if isinstance(assistant.get("cost"), (int, float)) else None,
            tokens=assistant.get("tokens", {}) or {},
            duration_ms=duration_ms,
            session_id=session_id,
        )

    async def _interrupt_session(self, session_id: str) -> bool:
        """Best-effort remote interrupt. True only means the CLI call succeeded;
        it does NOT prove the model stopped generating."""
        token = _CURRENT_RUN.set(None)  # the run may already be flagged cancelled
        try:
            await self._run_cli_api("POST", f"/api/session/{session_id}/interrupt", timeout=15)
            return True
        except Exception as e:  # incl. RuntimeErrorBase
            logger.warning("Could not interrupt session %s: %s", session_id, _redact(str(e)))
            return False
        finally:
            _CURRENT_RUN.reset(token)

    async def abort_task(self, session_id: str) -> None:
        """Cancel by AgentOS key (task.session_title) or by raw OpenCode session id.

        1. flag the matching in-flight run(s) cancelled (execute_task then raises
           RuntimeCancelled and discards any late reply);
        2. kill the local `opencode api` process(es) serving it;
        3. send a best-effort remote interrupt to the REAL session id (mapped from
           the key). The remote stop is unconfirmed: the CLI gives no ack that
           generation ended.
        """
        runs = list(self._active.get(session_id, []))
        if not runs:
            runs = [r for rs in self._active.values() for r in rs if r.session_id == session_id]
        target_ids = {r.session_id for r in runs if r.session_id}
        if not runs and re.fullmatch(r"[A-Za-z0-9_-]+", session_id or ""):
            target_ids.add(session_id)  # direct caller with a raw OpenCode session id
        for r in runs:
            r.cancelled = True
            for proc in list(r.procs):
                await self._kill_proc(proc)
        for sid in target_ids:
            await self._interrupt_session(sid)
