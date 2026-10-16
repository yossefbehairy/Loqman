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
- Blocking subprocess calls run in a worker thread so the event loop stays free.
"""

import asyncio
import json
import logging
import platform
import os
import re
import shutil
import subprocess
import time
from typing import Any, Dict, List, Optional

from backend.app.config import settings
from backend.app.runtime.base import (
    ModelCatalogEntry,
    ModelCost,
    RuntimeHealth,
    ExecuteAgentTask,
    AgentResult,
)
from backend.app.runtime.errors import (
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


def _redact(text: str) -> str:
    # Never log credentials / tokens.
    lowered = text or ""
    for key in ("apikey", "api_key", "sk-", "bearer "):
        if key in lowered.lower():
            return "[redacted runtime error]"
    return text[:500]


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
        # Short-lived catalog cache per free_only flag: the catalog barely changes
        # minute-to-minute, and every fetch spawns a CLI subprocess.
        self._catalog_cache: Dict[bool, tuple] = {}

    # -- transport ---------------------------------------------------------
    def _run_cli_api_sync(
        self, method: str, path: str, data: Optional[Dict[str, Any]] = None, timeout: int = 90
    ) -> Dict[str, Any]:
        args = [self.cli_cmd, "api", method, path]
        if data is not None:
            args.extend(["-d", json.dumps(data)])
        try:
            res = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", timeout=timeout)
        except FileNotFoundError as e:
            raise RuntimeOffline("OpenCode CLI not found. Install opencode v2 and ensure it is on PATH.")
        except subprocess.TimeoutExpired:
            raise RuntimeTimeout(f"OpenCode request timed out after {timeout}s ({method} {path}).")
        if res.returncode != 0:
            err = (res.stderr or res.stdout or "").strip()
            logger.error("opencode api failed: %s", _redact(err))
            raise normalize_cli_error(err, res.returncode)
        out = (res.stdout or "").strip()
        if not out:
            return {}
        try:
            # CLI prints UTF-16 on Windows in some setups; handle BOM gracefully.
            if out and out[0] == "\x00":
                out = res.stdout.encode().decode("utf-16", errors="ignore").strip()
            return json.loads(out) if out else {}
        except Exception as e:
            raise RuntimeErrorBase(f"Unparseable OpenCode response for {method} {path}: {e}")

    async def _run_cli_api(
        self, method: str, path: str, data: Optional[Dict[str, Any]] = None, timeout: int = 90
    ) -> Dict[str, Any]:
        # Keep the event loop free: subprocess is blocking.
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(self._run_cli_api_sync, method, path, data, timeout),
                timeout=timeout + 5,
            )
        except asyncio.TimeoutError:
            raise RuntimeTimeout(f"OpenCode request timed out ({method} {path}).")
        except asyncio.CancelledError:
            raise RuntimeCancelled("OpenCode request was cancelled.")

    async def request_api(self, method: str, path: str, data: Optional[Dict[str, Any]] = None,
                          timeout: int = 180) -> Any:
        """Forward a narrowly allowlisted SDK request through the local OpenCode CLI.

        The renderer SDK speaks the legacy ``/session/{id}/message`` prompt surface,
        while opencode v2 exposes ``/api/session`` + ``/api/session/{id}/prompt``.
        We translate the prompt call here (POST message -> prompt + wait) and return
        a message payload the SDK/front-end can read directly.
        """
        method = method.upper()
        if method not in {"GET", "POST"} or not path.startswith("/api/session"):
            raise RuntimeModelUnavailable("OpenCode SDK request is not allowed by the local proxy.")

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
                if last_assistant is not None and self._assistant_text(last_assistant).strip():
                    return last_assistant
                raise RuntimeTimeout(f"OpenCode session {session_id} did not finish within {timeout}s.")
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
        cost_arr = m.get("cost", [])
        inp_cost, out_cost, is_zero = 0.0, 0.0, False
        if cost_arr and isinstance(cost_arr, list):
            first = cost_arr[0] or {}
            try:
                inp_cost = float(first.get("input", 0.0))
                out_cost = float(first.get("output", 0.0))
            except Exception:
                inp_cost, out_cost = 0.0, 0.0
            # Free eligibility: explicit 0/0 pricing ONLY (never name-based).
            is_zero = inp_cost == 0.0 and out_cost == 0.0
        variants = [v.get("id") for v in (m.get("variants") or []) if isinstance(v, dict) and "id" in v]
        is_active = m.get("status") == "active" and m.get("enabled", True)
        return ModelCatalogEntry(
            id=m.get("id") or m.get("modelID", "unknown"),
            name=m.get("name", m.get("id", "Unknown")),
            provider_id=m.get("providerID", "opencode"),
            cost=ModelCost(input=inp_cost, output=out_cost),
            variants=variants,
            is_free=is_zero,
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
            f"Model '{model_id}' is not confirmed free (0/0) and active. No paid fallback used."
        )

    # -- execution ----------------------------------------------------------
    async def execute_task(self, task: ExecuteAgentTask) -> AgentResult:
        if not task.prompt or not task.prompt.strip():
            raise RuntimeErrorBase("Empty prompt: refusing to invoke the model.")
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

        try:
            sess_res = await self._run_cli_api(
                "POST",
                "/api/session",
                {"title": task.session_title, "agent": "build", "model": model_ref},
                timeout=30,
            )
            session_id = (sess_res.get("data") or {}).get("id")
            if not session_id:
                raise RuntimeErrorBase("OpenCode did not return a session id.")

            assistant = await self._prompt_and_wait(
                session_id=session_id,
                text=prompt,
                model_ref=model_ref,
                timeout=task.timeout,
            )
        except (RuntimeTimeout, RuntimeCancelled):
            raise
        except RuntimeErrorBase:
            raise
        except Exception as e:
            raise RuntimeOffline(f"OpenCode transport failure: {_redact(str(e))}")

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
            cost=float(assistant.get("cost", 0.0) or 0.0),
            tokens=assistant.get("tokens", {}) or {},
            duration_ms=duration_ms,
            session_id=session_id,
        )

    async def abort_task(self, session_id: str) -> None:
        try:
            await self._run_cli_api("POST", f"/api/session/{session_id}/interrupt", timeout=15)
        except RuntimeErrorBase as e:
            logger.warning("Could not interrupt session %s: %s", session_id, _redact(str(e)))
        except Exception as e:
            logger.warning("Could not interrupt session %s: %s", session_id, _redact(str(e)))
