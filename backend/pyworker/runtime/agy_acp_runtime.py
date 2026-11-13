"""
AgyAcpRuntime: an AgentRuntime adapter over the Antigravity CLI's Agent Client
Protocol (ACP) server: `agy acp --stdio`. NOT Gemini CLI, NOT `agy -p`
(that is agy_runtime.AgyCliRuntime). Nothing imports this module until a
caller wires it in.

One task = one `agy acp` process = one ACP session
  argv : agy acp --stdio --sandbox --include-default-customizations=false
  cwd  : a fresh empty temp directory (removed afterwards)
  wire : newline-delimited JSON-RPC 2.0 over stdin/stdout, stderr discarded
  flow : initialize(protocolVersion=1, fs/terminal capabilities all false)
         -> session/new {cwd, mcpServers: []}
         -> session/set_mode "plan"                      (required)
         -> session/set_model | session/set_config_option (required)
         -> session/prompt, collecting session/update agent_message_chunk
         -> stopReason; process is closed/killed.

Deny-only posture (what this adapter does and does NOT claim)
  * Every session/request_permission from the agent is answered with the ACP
    outcome "cancelled" (denied). Nothing is ever approved, there is no
    allow-list and no "always" option.
  * Every other agent->client request (fs/*, terminal/*, unknown) gets a
    JSON-RPC -32601 error. We advertise no fs/terminal capability and pass no
    MCP servers; `--include-default-customizations=false` is passed so the
    user's default agy customizations are not loaded.
  * Plan mode and the requested model must both be ACCEPTED by the agent
    (JSON-RPC success); a refusal, an unknown mode/model, or a later
    `current_mode_update` away from plan aborts the task (fail closed).
  * This is NOT workspace isolation and NOT a native-approval flow. The empty
    cwd is a convenience, `--sandbox` is the CLI's own unverified "terminal
    restrictions", and agy itself may still act on its own authority. Plan mode
    plus permission denial are best-effort restrictions enforced by the agent.
  * No billing certainty: GEMINI_API_KEY / GOOGLE_API_KEY are stripped from
    the child env by default (existing agy login is used). cost/tokens are
    unknown; models are reported as status="unverified", is_free=False.
  * Stateless: session_id is always None; the process dies with the task.
    `variant` is unsupported (ACP exposes no effort setting here) and rejected.

Bounds: one line <= MAX_LINE_BYTES, whole conversation <= MAX_TOTAL_BYTES /
MAX_MESSAGES, reply text <= MAX_OUTPUT_CHARS, wall clock = task.timeout.
Malformed JSON, non-JSON-RPC frames, oversize lines, unknown response ids or
early exit all abort the task. Raw agent output is never echoed in errors.

Timeout / cancel: `session/cancel` is sent, the agent gets CANCEL_GRACE_SECONDS
to answer the prompt, then the process (tree, on Windows) is killed.
`abort_task(key)` works for an in-flight task; key is the task's session_title.
"""

import asyncio
import contextlib
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Any, AsyncIterator, Dict, List, Optional, Sequence, Tuple, Union

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

logger = logging.getLogger("agentos.runtime.agy_acp")

ACP_ARGS = ("acp", "--stdio", "--sandbox", "--include-default-customizations=false")
PROTOCOL_VERSION = 1
REQUIRED_MODE = "plan"

MAX_LINE_BYTES = 1024 * 1024
MAX_TOTAL_BYTES = 16 * 1024 * 1024
MAX_MESSAGES = 50_000
MAX_PROMPT_CHARS = 200_000
MAX_OUTPUT_CHARS = 60_000
MAX_REASONING_CHARS = 20_000
MAX_ID_CHARS = 200
MIN_TASK_TIMEOUT = 5
MAX_TASK_TIMEOUT = 600
PROBE_TIMEOUT_SECONDS = 30
CANCEL_GRACE_SECONDS = 1.0
EXIT_GRACE_SECONDS = 2.0
WRITE_TIMEOUT_SECONDS = 10.0
PROBE_CACHE_SECONDS = 30.0

_STRIPPED_ENV = ("AGENTOS_LOCAL_TOKEN", "GEMINI_API_KEY", "GOOGLE_API_KEY")
_OK_STOP_REASONS = ("end_turn",)

CmdSpec = Union[str, Sequence[str]]


class RuntimeUnsupported(RuntimeErrorBase):
    code = "unsupported_capability"
    retryable = False


class _RpcError(Exception):
    """JSON-RPC error from the agent. `hint` is for classification only and is
    never surfaced to callers."""

    def __init__(self, code: Any, hint: str):
        super().__init__("rpc error")
        self.code = code if isinstance(code, int) and not isinstance(code, bool) else 0
        self.hint = hint


def _protocol(reason: str) -> RuntimeErrorBase:
    return RuntimeErrorBase(f"agy acp protocol violation: {reason}.")


def _is_id(v: Any) -> bool:
    return (isinstance(v, int) and not isinstance(v, bool)) or (
        isinstance(v, str) and 0 < len(v) <= MAX_ID_CHARS)


def _safe_id(v: Any) -> bool:
    return isinstance(v, str) and 0 < len(v) <= MAX_ID_CHARS


def _classify_rpc(err: _RpcError, what: str) -> RuntimeErrorBase:
    low = err.hint
    if err.code == -32000 or any(k in low for k in (
            "auth", "login", "unauthenticated", "unauthorized", "401", "sign in")):
        return RuntimeAuthError("agy is not authenticated. Sign in with the agy CLI yourself.")
    if any(k in low for k in ("429", "rate limit", "quota", "credits", "resource_exhausted")):
        return RuntimeRateLimit("agy rate/quota/credit limit reached. No fallback was used.")
    if "model" in low and any(k in low for k in ("not found", "unknown", "invalid",
                                                  "not available", "unsupported")):
        return RuntimeModelUnavailable("Requested model is not available to this agy account.")
    return RuntimeErrorBase(f"agy acp {what} failed (code {err.code}).")


def _flatten_options(options: Any) -> List[Dict[str, Any]]:
    """ACP select options may be flat or grouped; return flat value dicts."""
    out: List[Dict[str, Any]] = []
    if not isinstance(options, list):
        return out
    for o in options:
        if not isinstance(o, dict):
            continue
        if isinstance(o.get("options"), list):
            out.extend(_flatten_options(o["options"]))
        elif "value" in o:
            out.append(o)
    return out


def _find_model_config(config_options: Any, model: str) -> Optional[str]:
    """id of a select config option that is the model picker and offers `model`."""
    if not isinstance(config_options, list):
        return None
    for opt in config_options:
        if not isinstance(opt, dict) or not _safe_id(opt.get("id")):
            continue
        if opt.get("category") != "model" and opt.get("id") != "model":
            continue
        if any(o.get("value") == model for o in _flatten_options(opt.get("options"))):
            return opt["id"]
    return None


class _Conn:
    """JSON-RPC conversation with one `agy acp` process."""

    def __init__(self, proc: asyncio.subprocess.Process, timeout: float):
        self.proc = proc
        self.timeout = timeout
        self.deadline = asyncio.get_running_loop().time() + timeout
        self.queue: "asyncio.Queue[Tuple[str, Any]]" = asyncio.Queue()
        self.pump_task: Optional[asyncio.Future] = None
        self.closed = asyncio.Event()
        self.next_id = 1
        self.session_id: Optional[str] = None
        self.prompt_id: Optional[int] = None
        self.prompt_done = False
        self.graceful = False
        self.text: List[str] = []
        self.text_len = 0
        self.reasoning: List[str] = []
        self.reasoning_len = 0
        self.denied_permissions = 0
        self.rejected_requests = 0

    # -- inbound ------------------------------------------------------------
    async def pump(self) -> None:
        """Read newline-delimited frames; bounded; never raises."""
        total = 0
        count = 0
        try:
            while True:
                try:
                    line = await self.proc.stdout.readline()
                except (ValueError, asyncio.LimitOverrunError):
                    self.queue.put_nowait(("error", "oversize line"))
                    return
                if not line:
                    self.queue.put_nowait(("eof", None))
                    return
                total += len(line)
                count += 1
                if total > MAX_TOTAL_BYTES or count > MAX_MESSAGES:
                    self.queue.put_nowait(("error", "output budget exceeded"))
                    return
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    msg = json.loads(stripped.decode("utf-8"))
                except (UnicodeDecodeError, ValueError):
                    self.queue.put_nowait(("error", "malformed json"))
                    return
                if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
                    self.queue.put_nowait(("error", "not a json-rpc 2.0 object"))
                    return
                self.queue.put_nowait(("msg", msg))
        except asyncio.CancelledError:
            raise
        except Exception:
            self.queue.put_nowait(("error", "read failure"))

    async def _next(self) -> Dict[str, Any]:
        remaining = self.deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            raise RuntimeTimeout(f"agy acp did not finish within {self.timeout:g}s; process killed.")
        try:
            kind, payload = await asyncio.wait_for(self.queue.get(), remaining)
        except asyncio.TimeoutError:
            raise RuntimeTimeout(f"agy acp did not finish within {self.timeout:g}s; process killed.")
        if kind == "msg":
            return payload
        if kind == "cancel":
            raise RuntimeCancelled("agy task was cancelled.")
        if kind == "eof":
            raise RuntimeErrorBase("agy acp process exited unexpectedly.")
        raise _protocol(str(payload))

    # -- outbound -----------------------------------------------------------
    async def send(self, obj: Dict[str, Any]) -> None:
        data = (json.dumps(obj, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")
        try:
            self.proc.stdin.write(data)
            await asyncio.wait_for(self.proc.stdin.drain(), WRITE_TIMEOUT_SECONDS)
        except (OSError, ConnectionError, RuntimeError, asyncio.TimeoutError):
            raise RuntimeErrorBase("agy acp connection closed.")

    async def _reply(self, rid: Any, *, result: Any = None, error: Optional[Dict[str, Any]] = None) -> None:
        msg: Dict[str, Any] = {"jsonrpc": "2.0", "id": rid}
        if error is not None:
            msg["error"] = error
        else:
            msg["result"] = result
        await self.send(msg)

    async def _answer_request(self, msg: Dict[str, Any]) -> None:
        """Deny-only policy for every agent->client request."""
        rid = msg.get("id")
        if not _is_id(rid):
            raise _protocol("bad request id")
        if msg.get("method") == "session/request_permission":
            self.denied_permissions += 1
            await self._reply(rid, result={"outcome": {"outcome": "cancelled"}})
        else:
            self.rejected_requests += 1
            await self._reply(rid, error={"code": -32601, "message": "Method not supported by client"})

    def _on_update(self, params: Any) -> None:
        if not isinstance(params, dict) or params.get("sessionId") != self.session_id:
            return
        update = params.get("update")
        if not isinstance(update, dict):
            return
        kind = update.get("sessionUpdate")
        if kind == "current_mode_update":
            if update.get("currentModeId") != REQUIRED_MODE:
                raise RuntimeUnsupported("agy acp left plan mode; aborting (fail closed).")
            return
        if self.prompt_id is None:
            return  # ignore anything (e.g. replays) before our prompt
        content = update.get("content")
        if kind in ("agent_message_chunk", "agent_thought_chunk") and isinstance(content, dict) \
                and content.get("type") == "text" and isinstance(content.get("text"), str):
            piece = content["text"]
            if kind == "agent_message_chunk":
                room = MAX_OUTPUT_CHARS - self.text_len
                if len(piece) > room:
                    raise RuntimeErrorBase("agy reply exceeded the output limit; partial text was discarded.")
                if room > 0:
                    self.text.append(piece[:room])
                    self.text_len += min(len(piece), room)
            else:
                room = MAX_REASONING_CHARS - self.reasoning_len
                if room > 0:
                    self.reasoning.append(piece[:room])
                    self.reasoning_len += min(len(piece), room)

    async def _dispatch_incoming(self, msg: Dict[str, Any]) -> None:
        method = msg.get("method")
        if not isinstance(method, str) or not method:
            raise _protocol("bad method")
        if "id" in msg:
            await self._answer_request(msg)
        elif method == "session/update":
            self._on_update(msg.get("params"))
        # other notifications are ignored

    async def request(self, method: str, params: Dict[str, Any]) -> Dict[str, Any]:
        rid = self.next_id
        self.next_id += 1
        if method == "session/prompt":
            self.prompt_id = rid
        await self.send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        while True:
            msg = await self._next()
            if "method" in msg:
                await self._dispatch_incoming(msg)
                continue
            mid = msg.get("id")
            if type(mid) is not int or mid != rid:
                raise _protocol("unexpected response id")
            if rid == self.prompt_id:
                self.prompt_done = True
            if "error" in msg:
                err = msg["error"]
                code = err.get("code") if isinstance(err, dict) else 0
                hint = str(err.get("message", ""))[:300].lower() if isinstance(err, dict) else ""
                raise _RpcError(code, hint)
            if "result" not in msg:
                raise _protocol("response without result")
            res = msg["result"]
            if res is None:
                return {}
            if not isinstance(res, dict):
                raise _protocol("non-object result")
            return res

    async def best_effort_cancel(self) -> None:
        """Send session/cancel and give the agent a short grace to answer the
        outstanding prompt. Never raises."""
        if self.session_id is None or self.proc.returncode is not None:
            return
        try:
            await self.send({"jsonrpc": "2.0", "method": "session/cancel",
                             "params": {"sessionId": self.session_id}})
        except Exception:
            return
        if self.prompt_id is None or self.prompt_done:
            return
        end = asyncio.get_running_loop().time() + CANCEL_GRACE_SECONDS
        try:
            while True:
                rem = end - asyncio.get_running_loop().time()
                if rem <= 0:
                    return
                kind, payload = await asyncio.wait_for(self.queue.get(), rem)
                if kind in ("eof", "error"):
                    return
                if kind != "msg":
                    continue
                if "method" in payload:
                    if "id" in payload and _is_id(payload.get("id")):
                        await self._answer_request(payload)  # still deny-only
                elif payload.get("id") == self.prompt_id:
                    return
        except Exception:
            return

    # -- protocol steps ------------------------------------------------------
    async def handshake(self, cwd: str) -> Dict[str, Any]:
        try:
            init = await self.request("initialize", {
                "protocolVersion": PROTOCOL_VERSION,
                "clientInfo": {"name": "agentos", "title": "AgentOS", "version": "0"},
                "clientCapabilities": {"fs": {"readTextFile": False, "writeTextFile": False},
                                       "terminal": False},
            })
        except _RpcError as e:
            raise _classify_rpc(e, "initialize")
        pv = init.get("protocolVersion")
        if type(pv) is not int or pv != PROTOCOL_VERSION:
            raise RuntimeUnsupported("agy acp negotiated an unsupported protocol version.")
        try:
            new = await self.request("session/new", {"cwd": cwd, "mcpServers": []})
        except _RpcError as e:
            raise _classify_rpc(e, "session/new")
        sid = new.get("sessionId")
        if not _safe_id(sid):
            raise _protocol("missing sessionId")
        self.session_id = sid

        models: List[Dict[str, str]] = []
        mblock = new.get("models")
        if isinstance(mblock, dict) and isinstance(mblock.get("availableModels"), list):
            for m in mblock["availableModels"]:
                if isinstance(m, dict) and _safe_id(m.get("modelId")):
                    name = m.get("name")
                    models.append({"id": m["modelId"],
                                   "name": name if isinstance(name, str) and name else m["modelId"]})
        modes: List[str] = []
        kblock = new.get("modes")
        if isinstance(kblock, dict) and isinstance(kblock.get("availableModes"), list):
            modes = [m["id"] for m in kblock["availableModes"]
                     if isinstance(m, dict) and _safe_id(m.get("id"))]
        return {"models": models, "modes": modes, "config_options": new.get("configOptions")}

    async def require_plan_mode(self) -> None:
        try:
            await self.request("session/set_mode", {"sessionId": self.session_id, "modeId": REQUIRED_MODE})
        except _RpcError:
            raise RuntimeUnsupported("agy acp did not accept plan mode; refusing to run (fail closed).")

    async def require_model(self, model: str, config_options: Any) -> None:
        try:
            await self.request("session/set_model", {"sessionId": self.session_id, "modelId": model})
            return
        except _RpcError as e:
            if e.code != -32601:  # only "method not found" may fall back
                raise RuntimeModelUnavailable("agy acp did not accept the requested model.")
        config_id = _find_model_config(config_options, model)
        if config_id is None:
            raise RuntimeModelUnavailable(
                "agy acp supports neither session/set_model nor a model config option for this model.")
        try:
            res = await self.request("session/set_config_option", {
                "sessionId": self.session_id, "configId": config_id, "value": model})
        except _RpcError:
            raise RuntimeModelUnavailable("agy acp did not accept the requested model.")
        returned = res.get("configOptions")
        if isinstance(returned, list):
            for opt in returned:
                if isinstance(opt, dict) and opt.get("id") == config_id \
                        and "currentValue" in opt and opt["currentValue"] != model:
                    raise RuntimeModelUnavailable("agy acp kept a different model than requested.")

    async def prompt(self, text: str) -> Tuple[str, Optional[str]]:
        try:
            res = await self.request("session/prompt", {
                "sessionId": self.session_id, "prompt": [{"type": "text", "text": text}]})
        except _RpcError as e:
            raise _classify_rpc(e, "prompt")
        stop = res.get("stopReason")
        if not isinstance(stop, str):
            raise _protocol("missing stopReason")
        if stop == "cancelled":
            raise RuntimeCancelled("agy task was cancelled.")
        if stop == "refusal":
            raise RuntimeErrorBase("agy declined to answer (stopReason=refusal).")
        if stop not in _OK_STOP_REASONS:
            raise RuntimeErrorBase("agy acp stopped abnormally; partial text was discarded.")
        reply = "".join(self.text).strip()
        if not reply:
            raise RuntimeErrorBase("agy returned no text content.")
        reasoning = "".join(self.reasoning).strip() or None
        return reply, reasoning


class AgyAcpRuntime:
    def __init__(self, cli_cmd: Optional[CmdSpec] = None, allow_api_key_env: bool = False):
        """cli_cmd: executable path or argv prefix (tests pass [python, fake.py])."""
        if cli_cmd is None:
            cli_cmd = shutil.which("agy") or None
        if isinstance(cli_cmd, str):
            cli_cmd = [cli_cmd]
        self._cmd: Optional[List[str]] = list(cli_cmd) if cli_cmd else None
        self._allow_api_key_env = allow_api_key_env
        self._inflight: Dict[str, Optional[_Conn]] = {}
        self._cancelled: set = set()
        self._probe_cache: Optional[Tuple[float, List[Dict[str, str]], List[str]]] = None

    # -- process plumbing ----------------------------------------------------
    def _env(self) -> Dict[str, str]:
        env = dict(os.environ)
        for k in _STRIPPED_ENV:
            if k == "AGENTOS_LOCAL_TOKEN" or not self._allow_api_key_env:
                env.pop(k, None)
        return env

    @staticmethod
    def _kill_tree_sync(pid: int) -> None:
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
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        try:
            await asyncio.wait_for(proc.wait(), timeout=5)
        except asyncio.TimeoutError:
            logger.warning("agy acp process did not confirm exit after kill.")

    async def _shutdown(self, conn: _Conn) -> None:
        proc = conn.proc
        try:
            with contextlib.suppress(Exception):
                proc.stdin.close()
            if conn.graceful and proc.returncode is None:
                try:
                    await asyncio.wait_for(proc.wait(), timeout=EXIT_GRACE_SECONDS)
                except asyncio.TimeoutError:
                    pass
            await self._kill(proc)
        finally:
            if conn.pump_task is not None:
                conn.pump_task.cancel()
                with contextlib.suppress(BaseException):
                    await conn.pump_task
            conn.closed.set()

    @contextlib.asynccontextmanager
    async def _session(self, timeout: float) -> AsyncIterator[Tuple[_Conn, str]]:
        if not self._cmd:
            raise RuntimeOffline("agy CLI not found on PATH.")
        with tempfile.TemporaryDirectory(prefix="agentos-agy-acp-", ignore_cleanup_errors=True) as cwd:
            kwargs: Dict[str, Any] = {}
            if sys.platform == "win32":
                kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            try:
                proc = await asyncio.create_subprocess_exec(
                    *self._cmd, *ACP_ARGS, stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
                    cwd=cwd, env=self._env(), limit=MAX_LINE_BYTES, **kwargs)
            except (FileNotFoundError, PermissionError, OSError):
                raise RuntimeOffline("agy CLI could not be started.")
            conn = _Conn(proc, timeout)
            conn.pump_task = asyncio.ensure_future(conn.pump())
            try:
                yield conn, cwd
            except (RuntimeTimeout, RuntimeCancelled, asyncio.CancelledError):
                await conn.best_effort_cancel()
                raise
            finally:
                await self._shutdown(conn)

    # -- health / models -----------------------------------------------------
    async def _probe(self) -> Tuple[List[Dict[str, str]], List[str]]:
        """initialize + session/new only. No model turn."""
        now = time.monotonic()
        if self._probe_cache and now - self._probe_cache[0] < PROBE_CACHE_SECONDS:
            return self._probe_cache[1], self._probe_cache[2]
        async with self._session(PROBE_TIMEOUT_SECONDS) as (conn, cwd):
            info = await conn.handshake(cwd)
            conn.graceful = True
        self._probe_cache = (time.monotonic(), info["models"], info["modes"])
        return info["models"], info["modes"]

    async def check_health(self) -> RuntimeHealth:
        """ok = acp handshake + session/new accepted, plan mode and >=1 model
        offered. Never starts a model turn; model access stays 'unverified'."""
        if not self._cmd:
            return RuntimeHealth(status="offline", service_url=None, default_model_status="unavailable",
                                 models_count=0, free_models_count=0)
        try:
            models, modes = await self._probe()
        except RuntimeOffline:
            return RuntimeHealth(status="offline", default_model_status="unavailable",
                                 models_count=0, free_models_count=0)
        except RuntimeErrorBase:
            return RuntimeHealth(status="degraded", default_model_status="unavailable",
                                 models_count=0, free_models_count=0)
        ok = bool(models) and REQUIRED_MODE in modes
        return RuntimeHealth(status="ok" if ok else "degraded", service_url=None,
                             default_model_status="unverified", models_count=len(models),
                             free_models_count=0)

    async def list_available_models(self, free_only: bool = True) -> List[ModelCatalogEntry]:
        """Models offered by session/new; never 'free' (billing unknown)."""
        if free_only or not self._cmd:
            return []
        try:
            models, _ = await self._probe()
        except RuntimeErrorBase:
            return []
        return [ModelCatalogEntry(id=m["id"], name=m["name"], provider_id="antigravity",
                                  cost=ModelCost(), variants=[], is_free=False, status="unverified")
                for m in models]

    # -- execution -------------------------------------------------------------
    async def execute_task(self, task: ExecuteAgentTask) -> AgentResult:
        if task.mcp_servers:
            raise RuntimeUnsupported("Scoped MCP tools are currently supported by the Claude adapter.")
        if not task.prompt or not task.prompt.strip():
            raise RuntimeErrorBase("Empty prompt: refusing to invoke the model.")
        if not self._cmd:
            raise RuntimeOffline("agy CLI not found on PATH.")
        if task.variant not in (None, "", "default"):
            raise RuntimeUnsupported("agy acp adapter does not support model variants.")
        model = (task.model_id or "").strip()
        if not _safe_id(model):
            raise RuntimeModelUnavailable("Invalid agy model identifier.")
        timeout = max(MIN_TASK_TIMEOUT, min(int(task.timeout or 90), MAX_TASK_TIMEOUT))
        key = task.session_title or f"task-{time.time_ns()}"
        if key in self._inflight:
            raise RuntimeErrorBase("An agy task with this session_title is already running.")
        self._inflight[key] = None  # reserved until the process exists
        t0 = time.time()
        try:
            async with self._session(timeout) as (conn, cwd):
                self._inflight[key] = conn
                if key in self._cancelled:
                    raise RuntimeCancelled("agy task was cancelled.")
                info = await conn.handshake(cwd)
                if REQUIRED_MODE not in info["modes"]:
                    raise RuntimeUnsupported("agy acp does not offer plan mode; refusing to run.")
                if model not in {m["id"] for m in info["models"]}:
                    raise RuntimeModelUnavailable("Requested model is not offered by agy acp.")
                await conn.require_plan_mode()
                await conn.require_model(model, info["config_options"])
                text, reasoning = await conn.prompt(task.prompt[:MAX_PROMPT_CHARS])
                conn.graceful = True
            if conn.denied_permissions or conn.rejected_requests:
                logger.info("agy acp: denied %d permission request(s), rejected %d other request(s).",
                            conn.denied_permissions, conn.rejected_requests)
            return AgentResult(text=text[:MAX_OUTPUT_CHARS], reasoning=reasoning, model_id=model,
                               variant_id=None, cost=None, tokens={},
                               duration_ms=int((time.time() - t0) * 1000), session_id=None)
        finally:
            self._inflight.pop(key, None)
            self._cancelled.discard(key)

    async def abort_task(self, session_id: str) -> None:
        """Cancel an in-flight task; `session_id` is the task's session_title.
        Returns after the process is gone (bounded wait). No-op if unknown."""
        if session_id not in self._inflight:
            logger.info("abort_task: no in-flight agy acp task for the given key.")
            return
        conn = self._inflight[session_id]
        if conn is None:
            self._cancelled.add(session_id)  # picked up as soon as the process exists
            return
        conn.queue.put_nowait(("cancel", None))
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(conn.closed.wait(), CANCEL_GRACE_SECONDS + EXIT_GRACE_SECONDS + 10)
