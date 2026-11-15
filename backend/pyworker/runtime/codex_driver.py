"""Native Codex turn driver: ``codex app-server`` over stdio JSON-RPC (newline-delimited JSON).

Methods and shapes were confirmed from ``codex app-server generate-json-schema`` (codex-cli 0.167.0, protocol marked
experimental): client requests ``initialize`` (then the ``initialized`` notification), ``thread/start``,
``thread/resume`` (``threadId``), ``turn/start`` (``threadId``, ``input``, ``model``, ``effort``, ``approvalPolicy``,
``sandboxPolicy``) and ``turn/interrupt`` (``threadId``, ``turnId``); server notifications ``item/agentMessage/delta``,
``item/reasoning/textDelta``, ``item/reasoning/summaryTextDelta``, ``item/started``, ``item/completed``,
``thread/tokenUsage/updated``, ``error`` and ``turn/completed``; server requests (all denied) for command/file/
permission approvals, user input, MCP elicitation and dynamic tool calls. The wire has no ``jsonrpc`` member.

One owned process per turn, launched through ``supervisor.SupervisedProcess`` (Job Object on Windows). Permission level
is exactly the legacy ``codex exec`` adapter: ``sandbox`` ``read-only``, ``approvalPolicy`` ``never`` (set in ``-c``, in
the thread parameters and again in the turn parameters, so a stored thread can never loosen it). Every approval or
question request that still arrives is answered with a denial and a ``permission_denied`` notice.

Sessions: only conversation scopes persist a native thread (``runtime_sessions``). The first turn runs ``thread/start``
(persistent, durable binding saved BEFORE the prompt is submitted); later turns run ``thread/resume`` and send only the
incremental prompt. CEO / mission / workflow scopes run an ephemeral thread in a scratch directory. If ``thread/resume``
is refused with an explicit missing-thread error (which can only happen before ``turn/start``), the binding is marked
invalid and ONE fresh thread is started with the caller's full history-seeded prompt plus a ``session_rebuilt`` notice.
Any failure after ``turn/start`` was sent is never replayed.

Interrupt: ``turn/interrupt`` is sent once; if ``turn/completed`` (interrupted) does not arrive within
``interrupt_grace_s`` (or the turn id is not known yet) the owned process tree is killed. Notifications that arrive
before the ``turn/start`` response that names their turn are buffered and replayed in order.

NOT verified against the live CLI: the exact error text of a refused ``thread/resume`` and ``codexErrorInfo`` values
seen in production; both are matched defensively. The live protocol check is left to the orchestrator.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Optional

from backend.pyworker.db.core import new_id, ts
from .base import RuntimeHealth
from .child_env import child_env
from .claude_driver import _StreamProcess, _preview, conversation_resume_check  # noqa: F401 (re-exported helper)
from .codex_runtime import (
    FORBIDDEN_EFFORTS, MAX_PROMPT_CHARS, _EFFORT_RE, _MODEL_RE, _STRIPPED_ENV, CodexCliRuntime,
)
from .driver import (
    ApprovalAnswer, CleanupIssue, DriverCapabilities, DriverEvent, DriverFailure, DriverTurnError, Emit, StartTurn,
    TurnRef,
)
from .sessions import SessionRepository

logger = logging.getLogger("agentos.runtime.codex_driver")

PROTOCOL = "codex-app-server"
MAX_LINE_BYTES = 4 * 1024 * 1024
MAX_STREAM_BYTES = 64 * 1024 * 1024
MAX_TIMEOUT_S = 600.0
STDERR_TAIL_BYTES = 8192
CLIENT_INFO = {"name": "agentos", "title": "AgentOS", "version": "0"}
# The same tool-surface hardening RuseBot applies; unknown feature keys are ignored by the CLI (no --strict-config).
CONFIG_OVERRIDES = ('approval_policy="never"', 'sandbox_mode="read-only"', "features.browser_use=false",
                    "features.browser_use_external=false", "features.computer_use=false")
REQUIRED_HELP = ("--config",)
_REFUSED_RE = re.compile(r"no rollout found|thread[^.\n]{0,40}(not found|does not exist|no longer exists|is archived)|"
                         r"(unknown|no such|missing|invalid) thread|no thread", re.I)
_FLAG_CACHE: dict = {}


def default_cli_cmd() -> Optional[list]:
    path = shutil.which("codex")
    return [path] if path else None


class _Cancelled(Exception):
    pass


class _Eof(Exception):
    pass


class _RpcError(Exception):
    def __init__(self, code, message, data=None):
        super().__init__(str(message))
        self.code, self.message, self.data = code, str(message or ""), data


def _failure(category, message, phase, acceptance, retryable=False, native_code=None):
    return DriverTurnError(DriverFailure(category, message, retryable, phase, acceptance, native_code))


def _consume(fut):
    if not fut.cancelled():
        fut.exception()


def classify_text(text: str) -> tuple:
    """(category, safe message, retryable) from bounded CLI text; never echoes the raw text."""
    low = (text or "").lower()
    if any(k in low for k in ("not logged in", "codex login", "unauthorized", "401", "invalid api key", "authentication",
                              "sign in")):
        return "auth", "Codex is not authenticated. Run `codex login` yourself.", False
    if any(k in low for k in ("usage limit", "quota")):
        return "quota", "Codex usage limit reached. No fallback was used.", False
    if any(k in low for k in ("429", "rate limit", "too many requests", "overloaded")):
        return "rate_limit", "Codex rate limit reached. No fallback was used.", True
    if "model" in low and any(k in low for k in ("not found", "unknown", "invalid", "not supported", "not available",
                                                 "does not exist", "access")):
        return "model_unavailable", "Requested model is not available to this Codex account.", False
    if any(k in low for k in ("econnrefused", "enotfound", "network", "connection", "timed out", "dns")):
        return "offline", "Codex could not reach its service.", True
    return "runtime_error", "Codex execution failed.", False


_INFO_MAP = {
    "unauthorized": ("auth", "Codex is not authenticated. Run `codex login` yourself.", False),
    "usageLimitExceeded": ("quota", "Codex usage limit reached. No fallback was used.", False),
    "sessionBudgetExceeded": ("quota", "Codex usage limit reached. No fallback was used.", False),
    "rateLimitExceeded": ("rate_limit", "Codex rate limit reached. No fallback was used.", True),
    "serverOverloaded": ("rate_limit", "Codex rate limit reached. No fallback was used.", True),
    "flexUnavailable": ("rate_limit", "Codex rate limit reached. No fallback was used.", True),
    "policyCapacityUnavailable": ("rate_limit", "Codex rate limit reached. No fallback was used.", True),
    "modelNotFound": ("model_unavailable", "Requested model is not available to this Codex account.", False),
    "contextWindowExceeded": ("request_refused", "The conversation exceeds the model context window.", False),
    "httpConnectionFailed": ("offline", "Codex could not reach its service.", True),
    "responseStreamConnectionFailed": ("offline", "Codex could not reach its service.", True),
    "responseStreamDisconnected": ("offline", "Codex could not reach its service.", True),
    "responseTooManyFailedAttempts": ("offline", "Codex could not reach its service.", True),
}


def classify_error(error: Any) -> tuple:
    """(category, message, retryable, native_code) from a ``TurnError`` object (codexErrorInfo first, then text)."""
    if not isinstance(error, dict):
        return (*classify_text(""), None)
    info = error.get("codexErrorInfo")
    code = info if isinstance(info, str) else (next(iter(info)) if isinstance(info, dict) and info else None)
    if code in _INFO_MAP:
        return (*_INFO_MAP[code], code)
    category, message, retryable = classify_text(str(error.get("message") or "") + " " + str(error.get("additionalDetails") or ""))
    return category, message, retryable, code


@dataclass
class _Item:
    id: str
    kind: str            # assistant | reasoning
    text: str = ""
    phase: Optional[str] = None
    completed: bool = False
    emitted: bool = False


@dataclass
class _Turn:
    turn: StartTurn
    emit: Emit
    task: Optional[asyncio.Task] = None
    seq: int = 0
    terminal: bool = False
    started: bool = False
    cancelled: bool = False
    stop: Optional[asyncio.Task] = None
    queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    proc: Any = None
    reader: Optional[asyncio.Task] = None
    plan: Optional[dict] = None
    deadline: float = 0.0
    generation: int = 1
    resumed: bool = False
    rebuilt: bool = False
    completion_data: dict = field(default_factory=dict)
    # per-attempt state
    stderr: bytearray = field(default_factory=bytearray)
    wlock: Optional[asyncio.Lock] = None
    next_id: int = 0
    pending: dict = field(default_factory=dict)
    fatal: Optional[BaseException] = None
    completed: Optional[asyncio.Future] = None
    thread_id: Optional[str] = None
    native_turn_id: Optional[str] = None
    awaiting_turn: bool = False
    backlog: list = field(default_factory=list)
    submitted: bool = False
    output_seen: bool = False
    api_error: Optional[str] = None
    api_error_info: Optional[dict] = None
    items: dict = field(default_factory=dict)
    order: list = field(default_factory=list)
    tools: dict = field(default_factory=dict)
    final_ids: list = field(default_factory=list)
    text_chars: int = 0
    usage_first: Optional[dict] = None
    usage_last: Optional[dict] = None
    model_seen: Optional[str] = None


class CodexAppServerDriver:
    engine = "codex"
    capabilities = DriverCapabilities(True, True, False, False, "resume", "relaunch", False)

    def __init__(self, cli_cmd=None, *, runtime_key: str = "default", sessions: Optional[SessionRepository] = None,
                 resume_prompt: Optional[str] = None, resume_check: Optional[Callable[[dict], bool]] = None,
                 cwd_root: Optional[str] = None, extra_env: Optional[dict] = None, allow_api_key_env: bool = False,
                 interrupt_grace_s: float = 3.0, exit_grace_s: float = 2.0, cache_flags: bool = True):
        if isinstance(cli_cmd, str):
            cli_cmd = [cli_cmd]
        self._cmd = list(cli_cmd) if cli_cmd else default_cli_cmd()
        self.runtime_key = runtime_key
        self.sessions = sessions
        self.resume_prompt = resume_prompt
        self.resume_check = resume_check
        self._cwd_root = cwd_root
        self._extra_env = extra_env
        self._allow_api_key_env = allow_api_key_env
        self.interrupt_grace_s, self.exit_grace_s = interrupt_grace_s, exit_grace_s
        self._cache_flags = cache_flags
        self._turns: dict = {}
        self._issues: list = []
        self._health_rt = None

    # -- environment / flags ------------------------------------------------------------------------------------------
    def _env(self) -> dict:
        env = child_env(_STRIPPED_ENV if self._allow_api_key_env else ())
        env.update(self._extra_env or {})
        return env

    def _namespace(self) -> str:
        return "codex:" + (self._env().get("CODEX_HOME") or "default")

    def _conversation_cwd(self) -> str:
        root = self._cwd_root
        if root is None:
            from backend.pyworker.appsettings import settings
            root = os.path.join(os.path.dirname(os.path.abspath(settings.SQLITE_DB_PATH)), "codex-sessions")
        path = os.path.join(root, "conversation")
        os.makedirs(path, exist_ok=True)
        return path

    def _probe_help(self) -> str:
        kwargs: dict = {}
        if sys.platform == "win32":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            res = subprocess.run([*self._cmd, "app-server", "--help"], stdin=subprocess.DEVNULL, capture_output=True,
                                 text=True, encoding="utf-8", errors="replace", timeout=15.0, shell=False,
                                 env=self._env(), **kwargs)
        except (subprocess.TimeoutExpired, OSError, ValueError):
            return ""
        return (res.stdout or "") + (res.stderr or "")

    async def _help_ok(self) -> bool:
        key = tuple(self._cmd)
        if self._cache_flags and key in _FLAG_CACHE:
            return _FLAG_CACHE[key]
        text = await asyncio.to_thread(self._probe_help)
        ok = bool(text) and all(f in text for f in REQUIRED_HELP)
        if self._cache_flags and text:
            _FLAG_CACHE[key] = ok
        return ok

    # -- admission ------------------------------------------------------------------------------------------------------
    async def start_turn(self, turn: StartTurn, emit: Emit) -> TurnRef:
        if not all((turn.turn_id, turn.run_id, turn.scope_id, turn.model_id)) or turn.timeout_s <= 0 or turn.max_output_chars < 1:
            raise ValueError("Invalid turn arguments")
        if turn.scope_kind not in {"conversation", "ceo", "mission_attempt", "workflow"}:
            raise ValueError("Invalid scope")
        if turn.turn_id in self._turns or any(
                not s.terminal and (s.turn.scope_kind, s.turn.scope_id) == (turn.scope_kind, turn.scope_id)
                for s in self._turns.values()):
            raise _failure("busy", "Scope already has an active turn.", "launch", "not-submitted")
        if len(self._turns) >= 256:
            for key, state in list(self._turns.items()):
                if state.terminal and (state.task is None or state.task.done()):
                    del self._turns[key]
                    break
            else:
                raise _failure("busy", "Driver admission limit reached.", "launch", "not-submitted")
        state = _Turn(turn, emit)
        self._turns[turn.turn_id] = state
        state.task = asyncio.create_task(self._run(state), name=f"codex-driver-{turn.turn_id}")
        return TurnRef(turn.turn_id, turn.run_id)

    # -- event plumbing -----------------------------------------------------------------------------------------------
    async def _send(self, s: _Turn, type_: str, data: dict, *, item_id=None):
        if s.terminal or (s.cancelled and type_ not in {"turn.started", "turn.completed"}):
            return
        s.seq += 1
        if type_ == "turn.completed":
            s.terminal = True  # the sole settlement gate, before awaiting the sink
        await s.emit(DriverEvent(1, new_id(), s.turn.turn_id, s.turn.run_id, s.seq, self.engine, self.runtime_key, ts(),
                                 type_, data, native_session_id=s.thread_id, native_turn_id=s.native_turn_id,
                                 item_id=item_id))

    async def _started(self, s: _Turn):
        if not s.started:
            s.started = True
            p = s.turn.tool_policy
            await self._send(s, "turn.started", {"model_id": s.turn.model_id,
                                                  "policy": {"mode": p.mode, "approval_mode": p.approval_mode}})

    # -- planning -----------------------------------------------------------------------------------------------------
    def _plan(self, s: _Turn, *, force_fresh: bool = False) -> dict:
        t = s.turn
        persist = t.scope_kind == "conversation" and self.sessions is not None
        cwd = self._conversation_cwd() if persist else None
        resume_id, row, generation = None, None, 1
        if persist:
            row = self.sessions.get(t.scope_kind, t.scope_id, self.engine, self.runtime_key)
            if t.session is not None:
                if t.session.protocol != PROTOCOL:
                    raise _failure("unsupported_capability", "Session protocol is not supported by Codex.", "resume", "not-submitted")
                resume_id, generation = t.session.native_session_id, t.session.generation
            elif (row is not None and not force_fresh and not t.session_reset and row["state"] != "invalid"
                  and row["last_turn_id"] and row["protocol"] == PROTOCOL and row["namespace"] == self._namespace()
                  and row["cwd"] == cwd and (self.resume_check is None or self.resume_check(row))):
                resume_id, generation = row["native_session_id"], row["generation"]
            if resume_id is None and row is not None:
                generation = row["generation"] + 1
        prompt = self.resume_prompt if resume_id and self.resume_prompt else t.prompt
        if len(prompt) > MAX_PROMPT_CHARS:
            raise _failure("request_refused", "Prompt is too long for Codex.", "launch", "not-submitted")
        return {"persist": persist, "cwd": cwd, "resume_id": resume_id, "generation": generation, "prompt": prompt,
                "row": row, "rebuilt": bool(persist and resume_id is None and row is not None)}

    def _argv(self) -> list:
        argv = [*self._cmd, "app-server"]
        for override in CONFIG_OVERRIDES:
            argv += ["-c", override]
        return argv

    def _effort(self, s: _Turn) -> Optional[str]:
        value = s.turn.effort if s.turn.effort not in (None, "", "default") else s.turn.variant
        return None if value in (None, "", "default") else str(value).strip().lower()

    def _fingerprint(self, s: _Turn) -> str:
        blob = json.dumps({"model": s.turn.model_id, "effort": self._effort(s), "sandbox": "read-only",
                           "approval": "never"}, sort_keys=True)
        return hashlib.sha256(blob.encode()).hexdigest()[:32]

    def _save_session(self, s: _Turn, plan: dict, state: str, last_turn_id: Optional[str]):
        if not plan["persist"] or not s.thread_id:
            return
        try:
            self.sessions.put(
                scope_kind=s.turn.scope_kind, scope_id=s.turn.scope_id, engine=self.engine, runtime_key=self.runtime_key,
                protocol=PROTOCOL, namespace=self._namespace(), native_session_id=s.thread_id, generation=s.generation,
                state=state, model_id=s.turn.model_id, provider_id=s.turn.provider_id, variant=s.turn.variant,
                agent_id=s.turn.agent_id, cwd=plan["cwd"], config_fingerprint=self._fingerprint(s),
                last_turn_id=last_turn_id)
        except Exception as exc:  # persistence must never fail a turn; the session just is not resumable
            logger.warning("could not persist Codex session binding (%s)", type(exc).__name__)

    def _invalidate(self, plan: dict):
        row = plan["row"]
        if row is None:
            return
        try:
            self.sessions.put(
                scope_kind=row["scope_kind"], scope_id=row["scope_id"], engine=row["engine"], runtime_key=row["runtime_key"],
                protocol=row["protocol"], namespace=row["namespace"], native_session_id=row["native_session_id"],
                generation=row["generation"], state="invalid", model_id=row["model_id"], provider_id=row["provider_id"],
                variant=row["variant"], agent_id=row["agent_id"], cwd=row["cwd"],
                config_fingerprint=row["config_fingerprint"], last_turn_id=row["last_turn_id"])
        except Exception as exc:
            logger.warning("could not invalidate Codex session binding (%s)", type(exc).__name__)

    # -- the turn -----------------------------------------------------------------------------------------------------
    async def _run(self, s: _Turn):
        t0 = time.monotonic()
        loop = asyncio.get_running_loop()
        s.deadline = loop.time() + min(float(s.turn.timeout_s), MAX_TIMEOUT_S)
        failure = None
        completion = None
        try:
            await self._started(s)
            if s.cancelled:
                raise _Cancelled()
            self._validate(s)
            if not self._cmd:
                raise _failure("cli_missing", "Codex CLI not found on PATH.", "launch", "not-submitted")
            if not await self._help_ok():
                raise _failure("unsupported_capability", "Installed Codex CLI does not offer `app-server` with config "
                               "overrides; refusing to run.", "launch", "not-submitted")
            plan = self._plan(s)
            outcome = await self._attempt(s, plan)
            if outcome == "resume_refused":
                self._invalidate(plan)
                self._reset_attempt(s)
                s.rebuilt = True
                await self._send(s, "runtime.notice", {"code": "session_rebuilt", "message":
                                 "The previous Codex thread could not be resumed; a new thread was started from the conversation history."})
                plan = self._plan(s, force_fresh=True)
                outcome = await self._attempt(s, plan)
                if outcome == "resume_refused":
                    raise _failure("runtime_error", "Codex refused a brand-new thread.", "launch", "not-submitted")
            completion = outcome
        except (_Cancelled, asyncio.CancelledError):
            s.cancelled = True
        except DriverTurnError as exc:
            failure = exc.failure
        except (FileNotFoundError, OSError):
            failure = DriverFailure("cli_missing", "Codex CLI could not be started.", False, "launch", "not-submitted")
        except Exception as exc:
            logger.warning("codex driver internal error (%s)", type(exc).__name__)
            failure = DriverFailure("runtime_error", "Codex driver failed unexpectedly.", False, "running", "unknown")
        finally:
            await self._cleanup(s)
        duration = int((time.monotonic() - t0) * 1000)
        try:
            if s.terminal:
                return
            if s.cancelled:
                await self._settle_tools(s, "cancelled")
                await self._send(s, "turn.completed", {"status": "cancelled", "stop_reason": "cancelled",
                                                       "duration_ms": duration, "final_item_ids": []})
            elif failure is not None:
                await self._settle_tools(s, "cancelled")
                await self._send(s, "runtime.error", asdict(failure))
                await self._send(s, "turn.completed", {"status": "cancelled" if failure.category == "cancelled" else "failed",
                                                       "stop_reason": failure.category, "duration_ms": duration,
                                                       "final_item_ids": [], "failure": asdict(failure)})
            elif completion == "completed":
                await self._send(s, "turn.completed", s.completion_data | {"duration_ms": duration})
        except Exception as exc:
            logger.warning("codex driver could not deliver terminal event (%s)", type(exc).__name__)

    def _validate(self, s: _Turn):
        t = s.turn
        if t.tool_policy.mcp_servers:
            raise _failure("unsupported_capability", "Scoped MCP tools are currently supported by the Claude adapter.",
                           "launch", "not-submitted")
        effort = self._effort(s)
        if effort is not None:
            if effort in FORBIDDEN_EFFORTS:
                raise _failure("unsupported_capability", "Codex 'ultra' (automatic delegation) is not allowed.", "launch", "not-submitted")
            if not _EFFORT_RE.match(effort):
                raise _failure("unsupported_capability", "Invalid Codex reasoning-effort variant.", "launch", "not-submitted")
        if t.system:
            raise _failure("unsupported_capability", "A separate system prompt is not supported.", "launch", "not-submitted")
        if not _MODEL_RE.match(t.model_id or ""):
            raise _failure("model_unavailable", "Invalid Codex model identifier.", "launch", "not-submitted")
        if not (t.prompt or "").strip():
            raise _failure("request_refused", "Empty prompt: refusing to invoke the model.", "launch", "not-submitted")

    def _reset_attempt(self, s: _Turn):
        s.stderr = bytearray()
        s.queue = asyncio.Queue()
        s.next_id, s.pending, s.fatal, s.completed = 0, {}, None, None
        s.thread_id, s.native_turn_id, s.awaiting_turn, s.backlog = None, None, False, []
        s.submitted, s.output_seen, s.api_error, s.api_error_info = False, False, None, None
        s.items, s.order, s.tools, s.final_ids, s.text_chars = {}, [], {}, [], 0
        s.usage_first = s.usage_last = None

    async def _cleanup(self, s: _Turn):
        proc, s.proc = s.proc, None
        if proc is None:
            return
        try:
            self._issues.extend(await proc.close())
            self._issues[:] = self._issues[-256:]
        except asyncio.CancelledError:
            raise
        except Exception:
            self._issues.append(CleanupIssue(s.turn.turn_id, None, "Codex process cleanup failed."))

    # -- JSON-RPC plumbing --------------------------------------------------------------------------------------------
    async def _write(self, s: _Turn, obj: dict):
        data = (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")
        async with s.wlock:
            try:
                await asyncio.to_thread(s.proc.proc.stdin.write, data)
                await asyncio.to_thread(s.proc.proc.stdin.flush)
            except (BrokenPipeError, OSError, ValueError):
                raise _Eof()

    async def _call(self, s: _Turn, method: str, params: dict, hook=None):
        if s.fatal is not None:
            raise s.fatal
        s.next_id += 1
        rid = s.next_id
        fut = asyncio.get_running_loop().create_future()
        fut.add_done_callback(_consume)
        s.pending[rid] = (fut, hook)
        try:
            await self._write(s, {"id": rid, "method": method, "params": params})
        except BaseException:
            s.pending.pop(rid, None)
            raise
        return await self._wait(s, fut)

    async def _wait(self, s: _Turn, fut):
        remaining = s.deadline - asyncio.get_running_loop().time()
        try:
            return await asyncio.wait_for(fut, max(remaining, 0.001))
        except asyncio.TimeoutError:
            raise _failure("timeout", f"Codex did not finish within {int(s.turn.timeout_s)}s; process killed.", "running",
                           "output-seen" if s.output_seen else ("unknown" if s.submitted else "not-submitted"), True)

    def _fail_all(self, s: _Turn, exc: BaseException):
        if s.fatal is None:
            s.fatal = exc
        for fut, _hook in list(s.pending.values()):
            if not fut.done():
                fut.set_exception(exc)
        s.pending.clear()
        if s.completed is not None and not s.completed.done():
            s.completed.set_exception(exc)

    async def _reader(self, s: _Turn, proc):
        loop = asyncio.get_running_loop()
        buf = bytearray()
        try:
            while True:
                item = await s.queue.get()
                kind = item[0]
                if kind == "cancel":
                    raise _Cancelled()
                if kind == "exit":
                    loop.call_later(self.exit_grace_s, s.queue.put_nowait, ("eof",))  # descendant may hold the pipe open
                    continue
                if kind == "eof":
                    if buf.strip():
                        await self._line(s, bytes(buf))
                    raise _Eof()
                buf += item[1]
                if proc._overflow.is_set():
                    raise _failure("output_limit", "Engine output exceeded the allowed byte limit.", "running", "output-seen")
                while (nl := buf.find(b"\n")) >= 0:
                    line = bytes(buf[:nl])
                    del buf[:nl + 1]
                    await self._line(s, line)
                if len(buf) > MAX_LINE_BYTES:
                    raise _failure("protocol_error", "Codex protocol line exceeded the size limit.", "running", "output-seen")
        except asyncio.CancelledError:
            self._fail_all(s, _Cancelled())
            raise
        except Exception as exc:
            self._fail_all(s, exc)

    async def _line(self, s: _Turn, raw: bytes):
        raw = raw.strip()
        if not raw:
            return
        try:
            o = json.loads(raw.decode("utf-8", "replace"))
        except ValueError:
            return  # non-protocol noise on stdout is ignored
        if not isinstance(o, dict):
            return
        if "method" in o and "id" in o:
            await self._server_request(s, o)
        elif "method" in o:
            await self._notification(s, o)
        elif "id" in o:
            entry = s.pending.pop(o["id"], None)
            if entry is None:
                return
            fut, hook = entry
            if fut.done():
                return
            if isinstance(o.get("error"), dict):
                err = o["error"]
                fut.set_exception(_RpcError(err.get("code"), err.get("message"), err.get("data")))
                return
            result = o.get("result")
            if hook is not None:
                await hook(result)  # runs in reader order, before any later notification
            fut.set_result(result)

    # -- server requests: every approval/question is denied -----------------------------------------------------------
    async def _server_request(self, s: _Turn, o: dict):
        method, rid = o.get("method"), o["id"]
        replies = {
            "item/commandExecution/requestApproval": {"decision": "decline"},
            "item/fileChange/requestApproval": {"decision": "decline"},
            "execCommandApproval": {"decision": "denied"},
            "applyPatchApproval": {"decision": "denied"},
            "item/permissions/requestApproval": {"permissions": {}, "scope": "turn"},
            "item/tool/requestUserInput": {"answers": {}},
            "mcpServer/elicitation/request": {"action": "decline"},
            "item/tool/call": {"contentItems": [], "success": False},
        }
        if method in replies:
            await self._send(s, "runtime.notice", {"code": "permission_denied",
                                                   "message": "A tool permission or question request was denied."})
            msg = {"id": rid, "result": replies[method]}
        else:
            msg = {"id": rid, "error": {"code": -32601, "message": "Method not supported by AgentOS."}}
        try:
            await self._write(s, msg)
        except _Eof:
            pass

    # -- notifications ------------------------------------------------------------------------------------------------
    async def _notification(self, s: _Turn, o: dict):
        params = o.get("params") if isinstance(o.get("params"), dict) else {}
        method = o.get("method")
        if s.completed is not None and s.completed.done():
            return
        tid = params.get("turnId")
        if tid is None and isinstance(params.get("turn"), dict):
            tid = params["turn"].get("id")
        if tid is not None:
            if s.native_turn_id is None:
                if s.awaiting_turn:
                    s.backlog.append(o)  # arrived before the response that names its turn
                return
            if tid != s.native_turn_id:
                return
        await self._dispatch(s, method, params)

    async def _dispatch(self, s: _Turn, method: str, params: dict):
        if method == "item/agentMessage/delta":
            await self._delta(s, params, "assistant")
        elif method in ("item/reasoning/textDelta", "item/reasoning/summaryTextDelta"):
            await self._delta(s, params, "reasoning")
        elif method == "item/started":
            await self._item_started(s, params.get("item"))
        elif method == "item/completed":
            await self._item_completed(s, params.get("item"))
        elif method == "thread/tokenUsage/updated":
            usage = params.get("tokenUsage")
            if isinstance(usage, dict):
                s.usage_first = s.usage_first or usage
                s.usage_last = usage
        elif method == "error":
            error = params.get("error") if isinstance(params.get("error"), dict) else {}
            if params.get("willRetry") is True:
                await self._send(s, "runtime.notice", {"code": "engine_retrying", "message": "Codex is retrying a failed request."})
            else:
                s.api_error = str(error.get("message") or "")[:500]
                s.api_error_info = error
        elif method == "turn/completed":
            turn = params.get("turn") if isinstance(params.get("turn"), dict) else {}
            if s.completed is not None and not s.completed.done():
                s.completed.set_result(turn)

    def _item(self, s: _Turn, item_id: str, kind: str) -> _Item:
        item = s.items.get(item_id)
        if item is None:
            item = s.items[item_id] = _Item(item_id, kind)
            s.order.append(item_id)
        return item

    def _check_text_budget(self, s: _Turn, extra: int):
        s.text_chars += extra
        if s.text_chars > s.turn.max_output_chars:
            raise _failure("output_limit", "Engine output exceeded the allowed character limit.", "running", "output-seen")

    async def _delta(self, s: _Turn, params: dict, kind: str):
        item_id, text = params.get("itemId"), params.get("delta")
        if not isinstance(item_id, str) or not isinstance(text, str) or not text:
            return
        item = self._item(s, item_id, kind)
        self._check_text_budget(s, len(text))
        item.text += text
        s.output_seen = True
        await self._send(s, "content.delta", {"stream_kind": "assistant_text" if kind == "assistant" else "reasoning_text",
                                              "delta": text}, item_id=item_id)

    async def _emit_item(self, s: _Turn, item: _Item, phase: str):
        item.emitted = True
        data = {"item_type": item.kind, "text": item.text}
        if item.kind == "assistant":
            data["phase"] = phase
        await self._send(s, "item.completed", data, item_id=item.id)

    async def _flush_items(self, s: _Turn, *, final: bool):
        """Complete buffered text items. Text before a tool call is commentary; unemitted text at the end is the reply."""
        pending = [s.items[i] for i in s.order if not s.items[i].emitted and s.items[i].text.strip()]
        if final:
            answers = [i for i in pending if i.kind == "assistant" and i.phase != "commentary"]
            if not answers:
                last = [i for i in pending if i.kind == "assistant"]
                answers = last[-1:]
        for item in pending:
            is_final = final and item.kind == "assistant" and item in answers
            await self._emit_item(s, item, "final" if is_final else "commentary")
            if is_final:
                s.final_ids.append(item.id)

    def _tool_title(self, item: dict) -> str:
        kind = item.get("type")
        if kind == "mcpToolCall":
            return f"{item.get('server', 'mcp')}/{item.get('tool', 'tool')}"[:80]
        if kind == "dynamicToolCall":
            return str(item.get("tool") or "tool")[:80]
        return {"commandExecution": "command", "fileChange": "file_change", "webSearch": "web_search"}.get(kind, str(kind))[:80]

    def _tool_input(self, item: dict):
        kind = item.get("type")
        if kind == "commandExecution":
            return _preview(item.get("command"))
        if kind in ("mcpToolCall", "dynamicToolCall"):
            return _preview(item.get("arguments"))
        if kind == "webSearch":
            return _preview(item.get("query"))
        return None

    async def _item_started(self, s: _Turn, item: Any):
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            return
        kind = item.get("type")
        if kind == "agentMessage":
            self._item(s, item["id"], "assistant").phase = item.get("phase")
        elif kind == "reasoning":
            self._item(s, item["id"], "reasoning")
        elif kind in ("commandExecution", "fileChange", "mcpToolCall", "dynamicToolCall", "webSearch", "imageGeneration"):
            await self._flush_items(s, final=False)
            s.tools[item["id"]] = False
            s.output_seen = True
            data = {"item_type": "tool", "title": self._tool_title(item), "status": "running"}
            if self._tool_input(item) is not None:
                data["input"] = self._tool_input(item)
            await self._send(s, "item.started", data, item_id=item["id"])

    async def _item_completed(self, s: _Turn, item: Any):
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            return
        kind, iid = item.get("type"), item["id"]
        if kind == "agentMessage":
            cur = self._item(s, iid, "assistant")
            text = item.get("text") if isinstance(item.get("text"), str) else cur.text
            self._check_text_budget(s, len(text) - len(cur.text))
            if text and not cur.text:
                s.output_seen = True
                await self._send(s, "content.delta", {"stream_kind": "assistant_text", "delta": text}, item_id=iid)
            cur.text = text  # authoritative snapshot, never appended again
            cur.phase, cur.completed = item.get("phase"), True
        elif kind in ("commandExecution", "fileChange", "mcpToolCall", "dynamicToolCall", "webSearch", "imageGeneration"):
            if iid not in s.tools:
                s.tools[iid] = False
                await self._send(s, "item.started", {"item_type": "tool", "title": self._tool_title(item), "status": "running",
                                                     "synthetic": True}, item_id=iid)
            status = str(item.get("status") or "completed")
            ok = status == "completed" and item.get("exitCode") in (None, 0)
            s.tools[iid] = True
            out = item.get("aggregatedOutput") if kind == "commandExecution" else (item.get("result") or item.get("error"))
            data = {"item_type": "tool", "status": "completed" if ok else ("declined" if status == "declined" else "failed"),
                    "ok": ok}
            if out is not None:
                data["output"] = _preview(out)
            await self._send(s, "item.completed", data, item_id=iid)

    async def _settle_tools(self, s: _Turn, status: str):
        for tid, done in list(s.tools.items()):
            if not done:
                s.tools[tid] = True
                await self._send(s, "item.completed", {"item_type": "tool", "status": status, "ok": False}, item_id=tid)

    # -- one launch ---------------------------------------------------------------------------------------------------
    async def _attempt(self, s: _Turn, plan: dict) -> str:
        if s.cancelled:
            raise _Cancelled()
        s.generation, s.resumed, s.plan = plan["generation"], plan["resume_id"] is not None, plan
        s.rebuilt = s.rebuilt or plan["rebuilt"]
        s.wlock = asyncio.Lock()
        s.completed = asyncio.get_running_loop().create_future()
        s.completed.add_done_callback(_consume)
        with tempfile.TemporaryDirectory(prefix="agentos-codex-", ignore_cleanup_errors=True) as scratch:
            cwd = plan["cwd"] or scratch
            queue = s.queue
            proc = await _StreamProcess.launch(
                self._argv(), cwd=cwd, env=self._env(),
                on_out=lambda d: queue.put_nowait(("eof",) if d is None else ("out", d)),
                on_err=lambda d: self._stderr(s, d))
            s.proc = proc
            exited = None
            try:
                if s.cancelled:
                    raise _Cancelled()
                exited = asyncio.ensure_future(asyncio.to_thread(proc.proc.wait))
                exited.add_done_callback(lambda _f: queue.put_nowait(("exit",)))
                s.reader = asyncio.create_task(self._reader(s, proc))
                try:
                    return await self._conversation(s, plan, cwd)
                except _Eof:
                    raise self._eof_failure(s)
                except _RpcError as exc:
                    raise self._rpc_failure(s, exc)
            finally:
                if exited is not None:
                    exited.cancel()
                if s.reader is not None:
                    s.reader.cancel()
                    await asyncio.gather(s.reader, return_exceptions=True)
                await self._release(s, proc, graceful=s.completed is not None and s.completed.done()
                                    and not s.completed.cancelled() and s.completed.exception() is None)

    async def _conversation(self, s: _Turn, plan: dict, cwd: str) -> str:
        t = s.turn
        await self._call(s, "initialize", {"clientInfo": CLIENT_INFO, "capabilities": {"experimentalApi": False}})
        await self._write(s, {"method": "initialized", "params": {}})
        base = {"cwd": cwd, "model": t.model_id, "approvalPolicy": "never", "sandbox": "read-only"}
        if plan["resume_id"]:
            try:
                resp = await self._call(s, "thread/resume", {"threadId": plan["resume_id"], "excludeTurns": True, **base})
            except _RpcError as exc:
                if _REFUSED_RE.search(exc.message) and not re.search(r"unauthor|not logged in|login|401", exc.message, re.I):
                    return "resume_refused"
                raise
        else:
            resp = await self._call(s, "thread/start", {"ephemeral": not plan["persist"], **base})
        thread = resp.get("thread") if isinstance(resp, dict) and isinstance(resp.get("thread"), dict) else {}
        if not isinstance(thread.get("id"), str) or not thread["id"]:
            raise _failure("protocol_error", "Codex returned no thread id.", "handshake", "not-submitted")
        s.thread_id = thread["id"]
        if isinstance(resp.get("model"), str):
            s.model_seen = resp["model"]
        await self._send(s, "session.started", {
            "session_id": s.thread_id, "protocol": PROTOCOL, "namespace": self._namespace(), "generation": s.generation,
            "model_id": s.model_seen or t.model_id, "resumed": s.resumed, "rebuilt": s.rebuilt})
        row = plan["row"]
        self._save_session(s, plan, "unverified", row["last_turn_id"] if (row and s.resumed) else None)  # durable before submit
        if s.cancelled:
            raise _Cancelled()
        params = {"threadId": s.thread_id, "input": [{"type": "text", "text": plan["prompt"], "text_elements": []}],
                  "cwd": cwd, "model": t.model_id, "approvalPolicy": "never", "sandboxPolicy": {"type": "readOnly"}}
        if self._effort(s):
            params["effort"] = self._effort(s)

        async def adopt(result):
            turn = result.get("turn") if isinstance(result, dict) and isinstance(result.get("turn"), dict) else {}
            if isinstance(turn.get("id"), str):
                s.native_turn_id = turn["id"]
            s.awaiting_turn = False
            backlog, s.backlog = s.backlog, []
            for note in backlog:  # replay, in arrival order, what came before the response
                await self._notification(s, note)

        s.awaiting_turn = True
        s.submitted = True
        try:
            await self._call(s, "turn/start", params, hook=adopt)
        except _RpcError as exc:
            s.submitted = False
            raise exc
        if s.native_turn_id is None:
            raise _failure("protocol_error", "Codex returned no turn id.", "running", "unknown")
        turn_obj = await self._wait(s, s.completed)
        return await self._finish(s, plan, turn_obj)

    def _eof_failure(self, s: _Turn) -> DriverTurnError:
        stderr = bytes(s.stderr).decode("utf-8", "replace")
        category, message, retryable = classify_text(stderr + " " + (s.api_error or ""))
        if category == "runtime_error":
            category, message = "protocol_error", "Codex exited without completing the turn."
        return _failure(category, message, "running" if s.submitted else "handshake",
                        "output-seen" if s.output_seen else ("unknown" if s.submitted else "not-submitted"), retryable)

    def _rpc_failure(self, s: _Turn, exc: _RpcError) -> DriverTurnError:
        category, message, retryable = classify_text(exc.message)
        phase = "running" if s.submitted else ("resume" if s.plan and s.plan["resume_id"] and s.thread_id is None else "handshake")
        return _failure(category, message, phase, "not-submitted", retryable, exc.code if isinstance(exc.code, (int, str)) else None)

    async def _finish(self, s: _Turn, plan: dict, turn: dict) -> str:
        status = turn.get("status")
        if status == "interrupted":
            raise _Cancelled()
        if status != "completed":
            error = turn.get("error") if isinstance(turn.get("error"), dict) else s.api_error_info
            category, message, retryable, code = classify_error(error)
            raise _failure(category, message, "running", "output-seen" if s.output_seen else "submitted", retryable, code)
        await self._flush_items(s, final=True)
        if not s.final_ids:
            raise _failure("runtime_error", "Codex returned no text content.", "running", "output-seen")
        await self._settle_tools(s, "unknown")
        usage = self._usage(s)
        if usage["tokens"]:
            await self._send(s, "usage", {"scope": "turn", "measurement_id": s.turn.turn_id, "final": True, **usage})
        self._save_session(s, plan, "ready", s.turn.turn_id)
        s.completion_data = {"status": "succeeded", "stop_reason": "end_turn", "final_item_ids": list(s.final_ids),
                             "model_id": s.model_seen or s.turn.model_id, "variant": self._effort(s), "usage": usage,
                             "session_id": s.thread_id}
        return "completed"

    def _usage(self, s: _Turn) -> dict:
        """Turn usage = thread total now minus the thread total before this turn's first model call."""
        tokens: dict = {}
        first, last = s.usage_first, s.usage_last
        if isinstance(first, dict) and isinstance(last, dict):
            def num(block, key):
                value = (block or {}).get(key) if isinstance(block, dict) else None
                return value if isinstance(value, (int, float)) else None

            for out_key, key in (("input_tokens", "inputTokens"), ("cached_input_tokens", "cachedInputTokens"),
                                 ("output_tokens", "outputTokens"), ("reasoning_output_tokens", "reasoningOutputTokens")):
                end, start, one = num(last.get("total"), key), num(first.get("total"), key), num(first.get("last"), key)
                if end is not None:
                    tokens[out_key] = end - (start - one if start is not None and one is not None else 0)
        return {"tokens": tokens, "cost": None, "input": tokens.get("input_tokens"), "output": tokens.get("output_tokens"),
                "cache_read": tokens.get("cached_input_tokens"), "cache_write": None,
                "reasoning": tokens.get("reasoning_output_tokens"),
                "context_size": (last or {}).get("modelContextWindow") if isinstance(last, dict) else None}

    async def _release(self, s: _Turn, proc, *, graceful: bool):
        """Always end an attempt with the owned tree dead and the readers joined (before the scratch dir goes)."""
        if graceful:
            try:
                await asyncio.to_thread(proc.proc.stdin.close)
            except Exception:
                pass
            deadline = asyncio.get_running_loop().time() + self.exit_grace_s
            while proc.proc.poll() is None and asyncio.get_running_loop().time() < deadline:
                await asyncio.sleep(0.02)
        s.proc = None
        self._issues.extend(await proc.close())
        self._issues[:] = self._issues[-256:]

    def _stderr(self, s: _Turn, data):
        if data:
            s.stderr += data
            del s.stderr[:-STDERR_TAIL_BYTES]

    # -- interrupt / lifecycle ------------------------------------------------------------------------------------------
    async def _stop(self, s: _Turn):
        live = s.proc is not None and s.thread_id and s.native_turn_id and s.completed is not None and not s.completed.done()
        if live:
            try:  # protocol interrupt first; the response itself is not awaited
                s.next_id += 1
                await self._write(s, {"id": s.next_id, "method": "turn/interrupt",
                                      "params": {"threadId": s.thread_id, "turnId": s.native_turn_id}})
                await asyncio.wait({s.completed}, timeout=self.interrupt_grace_s)
            except (_Eof, Exception):
                pass
        s.queue.put_nowait(("cancel",))  # kill fallback (no-op when the turn already settled)
        if s.task is not None and not s.task.done():
            done, _ = await asyncio.wait({s.task}, timeout=self.interrupt_grace_s + 10.0)
            if not done:
                s.task.cancel()
                self._issues.append(CleanupIssue(s.turn.turn_id, None, "Codex turn did not settle after interrupt; task cancelled."))
                await asyncio.wait({s.task}, timeout=self.interrupt_grace_s)
        if not s.terminal:
            await self._started(s)
            await self._send(s, "turn.completed", {"status": "cancelled", "stop_reason": "cancelled", "duration_ms": 0,
                                                   "final_item_ids": []})

    async def interrupt(self, turn_id: str):
        s = self._turns.get(turn_id)
        if s is None:
            return "unknown"
        if s.terminal:
            if s.task is not None and not s.task.done():
                await asyncio.wait({s.task}, timeout=self.interrupt_grace_s + 10.0)
            return "already-terminal"
        s.cancelled = True
        if s.stop is None:
            s.stop = asyncio.create_task(self._stop(s))
        await asyncio.shield(s.stop)
        return "requested"

    async def respond(self, turn_id: str, request_id: str, answer: ApprovalAnswer):
        return "unavailable"

    async def close(self, scope_kind, scope_id):
        for s in list(self._turns.values()):
            if (s.turn.scope_kind, s.turn.scope_id) == (scope_kind, scope_id):
                await self.interrupt(s.turn.turn_id)
        return tuple(self._issues)

    async def stop_all(self):
        for s in list(self._turns.values()):
            await self.close(s.turn.scope_kind, s.turn.scope_id)
        return tuple(self._issues)

    def _runtime(self):
        if self._health_rt is None:
            self._health_rt = CodexCliRuntime(self._cmd, self._allow_api_key_env)
        return self._health_rt

    async def check_health(self) -> RuntimeHealth:
        return await self._runtime().check_health()

    async def list_available_models(self, free_only=True, *, refresh=False) -> list:
        return await self._runtime().list_available_models(free_only)
