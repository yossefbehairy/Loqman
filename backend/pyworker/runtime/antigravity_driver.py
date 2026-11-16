"""Native Antigravity turn driver: ``agy acp`` over the shared ACP transport (``acp_transport``).

Launch is EXACTLY the legacy adapter's: ``agy acp --stdio --sandbox --include-default-customizations=false`` (the
``ACP_ARGS`` constant of ``agy_acp_runtime`` is reused, not copied), one owned process per turn launched through
``supervisor.SupervisedProcess`` (Job Object on Windows), stderr never echoed, environment = ``child_env`` allowlist
with the opt-in ``GEMINI_API_KEY`` / ``GOOGLE_API_KEY`` (never ``AGENTOS_LOCAL_TOKEN``).

Dialect rules kept from the legacy adapter (fail closed):
  * ``initialize`` must negotiate protocol version 1; no fs/terminal capability is advertised and no MCP server passed.
  * ``plan`` mode is REQUIRED (offered by ``session/new`` and accepted by ``session/set_mode``); a later
    ``current_mode_update`` that leaves ``plan`` aborts the turn. The requested model must be offered and accepted
    (``session/set_model``, falling back to ``session/set_config_option`` only on "method not found").
  * Every ``session/request_permission`` is answered ``{"outcome": {"outcome": "cancelled"}}`` and a
    ``permission_denied`` notice is emitted; nothing is ever approved. Any other agent request gets ``-32601``.
    NEW relative to the legacy adapter (which only counted denials): if a tool whose permission was denied later
    reports ``completed``, the agent acted anyway and the turn fails (fail closed).

Sessions: only conversation scopes persist a native session (``runtime_sessions``, protocol ``acp-resume``, one working
directory per conversation). Resume is PROBED, never assumed: only ``sessionCapabilities.resume`` -> ``session/resume``
(``session/load`` is NEVER called: it replays the transcript instead of restoring it). A successful resume (whose result
``sessionId`` is absent or the requested one) sends only the incremental prompt. If resume is unsupported or refused with
an explicit not-found / does-not-exist error (both strictly before the prompt is written) the SAME process starts a
fresh ``session/new`` with the caller's full history-seeded prompt plus a notice, generation + 1. Auth, malformed or
invalid parameters (``-32602``, "invalid sessionId") and other errors are not "missing session" and fail the turn. Rows
stored under the old ``acp-antigravity`` protocol are not resumable (a new session is started). Plan mode and the model are
re-applied and verified on every session, resumed or new. CEO / mission / workflow scopes use a fresh empty scratch
directory and a fresh session every call. Once the prompt frame is written nothing is ever replayed.

Interrupt: ``session/cancel`` notification; if the prompt response does not arrive within ``interrupt_grace_s`` the owned
process tree is killed. Cancellation during handshake prevents the prompt from ever being written.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import shutil
import re
import tempfile
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Optional

from backend.pyworker.db.core import new_id, ts
from .acp_transport import (
    PROTOCOL_VERSION, AcpConnection, AcpError, AcpTimeout, AcpTransportError, find_model_config, parse_capabilities,
    safe_id,
)
from .agy_acp_runtime import ACP_ARGS, AgyAcpRuntime
from .base import RuntimeHealth
from .child_env import child_env
from .claude_driver import _StreamProcess, _preview, conversation_resume_check  # noqa: F401 (re-exported helper)
from .driver import (
    ApprovalAnswer, CleanupIssue, DriverCapabilities, DriverEvent, DriverFailure, DriverTurnError, Emit, StartTurn,
    TurnRef,
)
from .sessions import SessionRepository

logger = logging.getLogger("agentos.runtime.antigravity_driver")

PROTOCOL = "acp-resume"  # the contract dialect (driver.SessionProtocol); only session/resume is ever used
NAMESPACE = "antigravity:default"
REQUIRED_MODE = "plan"
MAX_PROMPT_CHARS = 200_000
MAX_OUTPUT_CHARS = 60_000
MAX_REASONING_CHARS = 20_000
MAX_TIMEOUT_S = 600.0
STDERR_TAIL_BYTES = 8192
CANCEL_GRACE_S = 1.0
MAX_DENIAL_NOTICES = 20
MAX_USAGE_EVENTS = 100
MAX_TOOL_UPDATES = 50
STREAM_BYTES = 64 * 1024 * 1024
AUTH_LINK_MARKER = "authenticate the acp server"
_STRIPPED_ENV = ("GEMINI_API_KEY", "GOOGLE_API_KEY")  # opt-in only; AGENTOS_LOCAL_TOKEN is never forwarded
_OK_STOP = "end_turn"
# Only an explicit not-found / does-not-exist signal means "session missing". "invalid", "unknown" or -32602 are
# malformed requests and fail the turn.
_MISSING_RE = re.compile(r"(session|conversation)[^.\n]{0,60}(not found|does not exist|doesn't exist|no longer exists)|"
                         r"no such (session|conversation)", re.I)
_ACTED_AFTER_DENIAL = "agy performed an action after its permission request was denied; aborting (fail closed)."
_TOOL_TITLES = {"read": "Read file", "edit": "Edit file", "delete": "Delete file", "move": "Move file",
                "search": "Search", "execute": "Run command", "think": "Think", "fetch": "Fetch", "switch_mode": "Switch mode"}


def scope_dir_name(scope_id: str) -> str:
    """One working directory per conversation scope: sanitised id plus a short hash so distinct ids never collide."""
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", scope_id)[:48]
    return f"{safe}-{hashlib.sha256(scope_id.encode('utf-8')).hexdigest()[:10]}"


def default_cli_cmd() -> Optional[list]:
    path = shutil.which("agy")
    return [path] if path else None


class _Cancelled(Exception):
    pass


def _failure(category, message, phase, acceptance, retryable=False, native_code=None):
    return DriverTurnError(DriverFailure(category, message, retryable, phase, acceptance, native_code))


def classify_rpc(err: AcpError, what: str) -> tuple:
    """(category, safe message, retryable) from a JSON-RPC error; the raw text is never surfaced."""
    low = err.hint()
    if err.code == -32000 or any(k in low for k in ("auth", "login", "unauthenticated", "unauthorized", "401", "sign in")):
        return "auth", "agy is not authenticated. Sign in with the agy CLI yourself.", False
    if any(k in low for k in ("quota", "credits", "resource_exhausted")):
        return "quota", "agy rate/quota/credit limit reached. No fallback was used.", False
    if any(k in low for k in ("429", "rate limit", "too many requests")):
        return "rate_limit", "agy rate/quota/credit limit reached. No fallback was used.", True
    if "model" in low and any(k in low for k in ("not found", "unknown", "invalid", "not available", "unsupported")):
        return "model_unavailable", "Requested model is not available to this agy account.", False
    return "runtime_error", f"agy acp {what} failed (code {err.code}).", False


@dataclass
class _Item:
    id: str
    kind: str            # assistant | reasoning
    msg_id: Optional[str] = None
    text: str = ""
    closed: bool = False
    emitted: bool = False
    epoch: int = 0


@dataclass
class _Turn:
    turn: StartTurn
    emit: Emit
    task: Optional[asyncio.Task] = None
    seq: int = 0
    terminal: bool = False
    terminal_sending: bool = False
    violation: Optional[DriverTurnError] = None
    started: bool = False
    cancelled: bool = False
    stop: Optional[asyncio.Task] = None
    proc: Any = None
    conn: Optional[AcpConnection] = None
    plan: Optional[dict] = None
    deadline: float = 0.0
    generation: int = 1
    resumed: bool = False
    rebuilt: bool = False
    completion_data: dict = field(default_factory=dict)
    # per-attempt state
    stderr: bytearray = field(default_factory=bytearray)
    step: str = "initialize"
    session_id: Optional[str] = None
    plan_set: bool = False
    prompt_sent: bool = False
    prompt_done: bool = False
    prompt_fut: Optional[asyncio.Future] = None
    submitted: bool = False
    output_seen: bool = False
    items: list = field(default_factory=list)
    cur_msg: Optional[_Item] = None
    cur_think: Optional[_Item] = None
    epoch: int = 0
    tools: dict = field(default_factory=dict)       # tool id -> completed?
    unkeyed: list = field(default_factory=list)
    tool_status: dict = field(default_factory=dict)
    tool_updates: int = 0
    denied: set = field(default_factory=set)
    denied_unkeyed: bool = False
    completed_ok: set = field(default_factory=set)
    denied_count: int = 0
    rejected_count: int = 0
    text_chars: int = 0
    reasoning_chars: int = 0
    usage_events: int = 0
    last_context: Optional[tuple] = None
    final_ids: list = field(default_factory=list)
    model_seen: Optional[str] = None
    auth_link: bool = False


class AntigravityAcpDriver:
    engine = "antigravity"
    # Dialect hooks (class attributes / small methods) so a sibling ACP driver (opencode_driver) reuses everything else.
    namespace = NAMESPACE
    acp_args = tuple(ACP_ARGS)
    sessions_dir = "antigravity-sessions"
    capabilities = DriverCapabilities(True, True, False, False, "resume", "relaunch", False)

    def __init__(self, cli_cmd=None, *, runtime_key: str = "default", sessions: Optional[SessionRepository] = None,
                 resume_prompt: Optional[str] = None, resume_check: Optional[Callable[[dict], bool]] = None,
                 cwd_root: Optional[str] = None, extra_env: Optional[dict] = None, allow_api_key_env: bool = False,
                 interrupt_grace_s: float = 3.0, exit_grace_s: float = 2.0, cancel_grace_s: float = CANCEL_GRACE_S):
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
        self.interrupt_grace_s, self.exit_grace_s, self.cancel_grace_s = interrupt_grace_s, exit_grace_s, cancel_grace_s
        self._turns: dict = {}
        self._issues: list = []
        self._health_rt = None

    # -- environment --------------------------------------------------------------------------------------------------
    def _env(self) -> dict:
        env = child_env(_STRIPPED_ENV if self._allow_api_key_env else ())
        env.update(self._extra_env or {})
        return env

    def _conversation_cwd(self, scope_id: str) -> str:
        root = self._cwd_root
        if root is None:
            from backend.pyworker.appsettings import settings
            root = os.path.join(os.path.dirname(os.path.abspath(settings.SQLITE_DB_PATH)), self.sessions_dir)
        path = os.path.join(root, "conversation", scope_dir_name(scope_id))
        os.makedirs(path, exist_ok=True)
        return path

    # -- admission ----------------------------------------------------------------------------------------------------
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
        state.task = asyncio.create_task(self._run(state), name=f"agy-driver-{turn.turn_id}")
        return TurnRef(turn.turn_id, turn.run_id)

    # -- event plumbing -----------------------------------------------------------------------------------------------
    async def _send(self, s: _Turn, type_: str, data: dict, *, item_id=None, settle: bool = False):
        if s.terminal or s.terminal_sending or (s.cancelled and not settle and type_ not in {"turn.started", "turn.completed"}):
            return
        s.seq += 1
        event = DriverEvent(1, new_id(), s.turn.turn_id, s.turn.run_id, s.seq, self.engine, self.runtime_key, ts(),
                            type_, data, native_session_id=s.session_id, item_id=item_id)
        if type_ != "turn.completed":
            await s.emit(event)
            return
        s.terminal_sending = True  # the sole settlement gate: one caller passes; ``terminal`` only after the sink accepts
        try:
            try:
                await s.emit(event)
            except Exception as exc:
                logger.warning("antigravity driver terminal event rejected by the sink (%s); retrying once", type(exc).__name__)
                await s.emit(event)  # same event (same id and seq) so a sink can deduplicate
            s.terminal = True
        finally:
            s.terminal_sending = False

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
        cwd = self._conversation_cwd(t.scope_id) if persist else None
        resume_id, row, generation = None, None, 1
        if persist:
            row = self.sessions.get(t.scope_kind, t.scope_id, self.engine, self.runtime_key)
            if t.session is not None:
                if t.session.protocol != PROTOCOL:
                    raise _failure("unsupported_capability", "Session protocol is not supported by Antigravity.", "resume", "not-submitted")
                resume_id, generation = t.session.native_session_id, t.session.generation
            elif (row is not None and not force_fresh and not t.session_reset and row["state"] != "invalid"
                  and row["last_turn_id"] and row["protocol"] == PROTOCOL and row["namespace"] == self.namespace
                  and row["cwd"] == cwd and (self.resume_check is None or self.resume_check(row))):
                resume_id, generation = row["native_session_id"], row["generation"]
            if resume_id is None and row is not None:
                generation = row["generation"] + 1
        incremental = self.resume_prompt if self.resume_prompt else t.prompt
        for text in (t.prompt, incremental):
            if len(text) > MAX_PROMPT_CHARS:
                raise _failure("request_refused", "Prompt is too long for Antigravity.", "launch", "not-submitted")
        return {"persist": persist, "cwd": cwd, "resume_id": resume_id, "generation": generation, "full_prompt": t.prompt,
                "prompt": incremental if resume_id else t.prompt, "row": row,
                "rebuilt": bool(persist and resume_id is None and row is not None)}

    def _mode_for(self, s: _Turn) -> str:
        """The session mode this turn must run in (a restricted mode); anything else fails closed."""
        return REQUIRED_MODE

    def _model_for(self, s: _Turn) -> str:
        """The model id as the agent's ACP dialect spells it (what is offered / set / compared)."""
        return s.turn.model_id

    async def _prepare(self, s: _Turn) -> None:
        """Async admission checks that need I/O, run once after ``_validate`` and before any process is launched."""

    def _fingerprint(self, s: _Turn) -> str:
        blob = json.dumps({"args": list(self.acp_args), "mode": self._mode_for(s), "model": self._model_for(s), "permissions": "cancelled"},
                          sort_keys=True)
        return hashlib.sha256(blob.encode()).hexdigest()[:32]

    def _save_session(self, s: _Turn, plan: dict, state: str, last_turn_id: Optional[str]):
        if not plan["persist"] or not s.session_id:
            return
        try:
            self.sessions.put(
                scope_kind=s.turn.scope_kind, scope_id=s.turn.scope_id, engine=self.engine, runtime_key=self.runtime_key,
                protocol=PROTOCOL, namespace=self.namespace, native_session_id=s.session_id, generation=s.generation,
                state=state, model_id=s.turn.model_id, provider_id=s.turn.provider_id, variant=s.turn.variant,
                agent_id=s.turn.agent_id, cwd=plan["cwd"], config_fingerprint=self._fingerprint(s),
                last_turn_id=last_turn_id)
        except Exception as exc:  # persistence must never fail a turn; the session just is not resumable
            logger.warning("could not persist Antigravity session binding (%s)", type(exc).__name__)

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
            logger.warning("could not invalidate Antigravity session binding (%s)", type(exc).__name__)

    # -- the turn -----------------------------------------------------------------------------------------------------
    async def _run(self, s: _Turn):
        t0 = time.monotonic()
        loop = asyncio.get_running_loop()
        s.deadline = loop.time() + min(float(s.turn.timeout_s), MAX_TIMEOUT_S)
        failure = None
        completion = None
        try:
            await self._started(s)
            self._check(s)
            self._validate(s)
            await self._prepare(s)
            if not self._cmd:
                raise _failure("cli_missing", "agy CLI not found on PATH.", "launch", "not-submitted")
            plan = self._plan(s)
            completion = await self._attempt(s, plan)
            if s.violation is not None:  # drift/denial violation seen after the prompt result: never succeed
                raise s.violation
        except (_Cancelled, asyncio.CancelledError):
            s.cancelled = True
        except DriverTurnError as exc:
            failure = exc.failure
        except (FileNotFoundError, PermissionError, OSError):
            failure = DriverFailure("cli_missing", "agy CLI could not be started.", False, "launch", "not-submitted")
        except Exception as exc:
            logger.warning("antigravity driver internal error (%s)", type(exc).__name__)
            failure = DriverFailure("runtime_error", "Antigravity driver failed unexpectedly.", False, "running", "unknown")
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
            logger.warning("antigravity driver could not deliver terminal event (%s)", type(exc).__name__)

    def _check(self, s: _Turn):
        if s.cancelled:
            raise _Cancelled()

    def _validate(self, s: _Turn):
        t = s.turn
        if t.tool_policy.mcp_servers:
            raise _failure("unsupported_capability", "Scoped MCP tools are currently supported by the Claude adapter.",
                           "launch", "not-submitted")
        if t.variant not in (None, "", "default") or t.effort not in (None, "", "default"):
            raise _failure("unsupported_capability", "agy acp adapter does not support model variants.", "launch", "not-submitted")
        if t.system:
            raise _failure("unsupported_capability", "A separate system prompt is not supported.", "launch", "not-submitted")
        if not safe_id((t.model_id or "").strip()) or t.model_id != t.model_id.strip():
            raise _failure("model_unavailable", "Invalid agy model identifier.", "launch", "not-submitted")
        if not (t.prompt or "").strip():
            raise _failure("request_refused", "Empty prompt: refusing to invoke the model.", "launch", "not-submitted")

    def _reset_attempt(self, s: _Turn):
        s.stderr = bytearray()
        s.step, s.session_id, s.plan_set = "initialize", None, False
        s.prompt_sent = s.prompt_done = s.submitted = s.output_seen = False
        s.prompt_fut = None
        s.items, s.cur_msg, s.cur_think, s.epoch = [], None, None, 0
        s.tools, s.unkeyed, s.tool_status, s.tool_updates = {}, [], {}, 0
        s.denied, s.denied_count, s.rejected_count = set(), 0, 0
        s.denied_unkeyed, s.completed_ok, s.violation = False, set(), None
        s.text_chars = s.reasoning_chars = s.usage_events = 0
        s.last_context, s.final_ids, s.auth_link = None, [], False

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
            self._issues.append(CleanupIssue(s.turn.turn_id, None, "Antigravity process cleanup failed."))

    # -- one launch ---------------------------------------------------------------------------------------------------
    async def _attempt(self, s: _Turn, plan: dict) -> str:
        self._check(s)
        self._reset_attempt(s)
        s.generation, s.resumed, s.plan = plan["generation"], False, plan
        s.rebuilt = s.rebuilt or plan["rebuilt"]
        conn = AcpConnection(on_request=lambda m, p, r: self._on_request(s, m, p, r),
                             on_diagnostic=lambda text: self._on_diagnostic(s, text))
        s.conn = conn
        with tempfile.TemporaryDirectory(prefix="agentos-agy-acp-", ignore_cleanup_errors=True) as scratch:
            cwd = plan["cwd"] or scratch
            proc = await _StreamProcess.launch(
                [*self._cmd, *self.acp_args], cwd=cwd, env=self._env(), on_out=conn.feed, on_err=lambda d: self._stderr(s, d),
                max_bytes=STREAM_BYTES)
            s.proc = proc
            exited = exit_timer = None
            try:
                self._check(s)
                stdin = proc.proc.stdin

                def write_blocking(data: bytes):
                    stdin.write(data)
                    stdin.flush()

                async def write(data: bytes):
                    await asyncio.to_thread(write_blocking, data)

                conn.start(write)
                loop = asyncio.get_running_loop()
                exited = asyncio.ensure_future(asyncio.to_thread(proc.proc.wait))
                # A descendant may keep the pipe open after the root exits: give the stream a grace, then treat as EOF.
                exited.add_done_callback(lambda _f: loop.call_later(self.exit_grace_s, conn.feed, None))
                try:
                    return await self._conversation(s, plan, cwd, conn)
                except AcpTransportError as exc:
                    raise self._transport_failure(s, exc)
                except AcpError as exc:
                    raise self._rpc_failure(s, exc)
                except AcpTimeout:
                    await self._cancel_prompt(s, self.cancel_grace_s)
                    raise _failure("timeout", f"agy acp did not finish within {int(s.turn.timeout_s)}s; process killed.",
                                   "running" if s.submitted else "handshake", self._acceptance(s), True)
            finally:
                if exited is not None:
                    exited.cancel()
                s.conn = None
                await conn.close()
                await self._release(s, proc, graceful=s.prompt_done and s.cancelled is False and s.violation is None)

    def _acceptance(self, s: _Turn) -> str:
        return "output-seen" if s.output_seen else ("unknown" if s.submitted else "not-submitted")

    def _remaining(self, s: _Turn) -> float:
        remaining = s.deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            raise AcpTimeout(s.step)
        return remaining

    async def _call(self, s: _Turn, step: str, coro_fn, *args, **kw):
        """Run one request with the turn deadline; ``step`` names it for failure messages."""
        self._check(s)
        s.step = step
        return await coro_fn(*args, timeout=self._remaining(s), **kw)

    # -- protocol flow ------------------------------------------------------------------------------------------------
    async def _conversation(self, s: _Turn, plan: dict, cwd: str, conn: AcpConnection) -> str:
        t = s.turn
        init = await self._call(s, "initialize", conn.initialize)
        version = init.get("protocolVersion")
        if type(version) is not int or version != PROTOCOL_VERSION:
            raise _failure("unsupported_capability", "agy acp negotiated an unsupported protocol version.", "handshake",
                           "not-submitted")
        caps = parse_capabilities(init)
        info = None
        prompt = plan["prompt"]
        if plan["resume_id"]:
            reopened = None
            if not caps.resume:  # session/load is never used: only an advertised session/resume restores a session
                await self._send(s, "runtime.notice", {"code": "session_resume_unsupported", "message":
                                 "This agy build does not advertise session resume; a new session was started from the conversation history."})
            else:
                reopened = await self._reopen(s, plan, cwd, conn, "session/resume")
            if reopened is None:
                prompt = self._fresh_plan(s, plan)
            else:
                info = reopened
                s.resumed = True
        if info is None:
            info = await self._new_session(s, cwd, conn)
        sid = s.session_id
        await self._select_mode_and_model(s, conn, sid, info)
        self._check(s)
        await self._send(s, "session.started", {
            "session_id": sid, "protocol": PROTOCOL, "namespace": self.namespace, "generation": s.generation,
            "model_id": s.model_seen or t.model_id, "resumed": s.resumed, "rebuilt": s.rebuilt})
        row = plan["row"]
        self._save_session(s, plan, "unverified", row["last_turn_id"] if (row and s.resumed) else None)  # durable before submit
        self._check(s)

        def before_write():
            if s.cancelled:
                raise _Cancelled()  # decided under the write lock: a stopped turn never submits its prompt
            s.prompt_sent = s.submitted = True

        def on_response():
            s.prompt_done = True

        s.step = "session/prompt"
        rid, fut = await conn.send_request(
            "session/prompt", {"sessionId": sid, "prompt": [{"type": "text", "text": prompt}]},
            before_write=before_write, on_response=on_response)
        s.prompt_fut = fut
        done, _pending = await asyncio.wait({fut}, timeout=self._remaining(s))  # not wait_for: it would cancel the future
        if not done:
            raise AcpTimeout("session/prompt")
        return await self._finish(s, plan, fut.result())

    def _fresh_plan(self, s: _Turn, plan: dict) -> str:
        """Switch the current attempt from a resume to a brand-new session (before anything was submitted)."""
        s.rebuilt = True
        row = plan["row"]
        plan["resume_id"] = None
        plan["generation"] = (row["generation"] + 1) if row is not None else 1
        s.generation = plan["generation"]
        return plan["full_prompt"]

    async def _reopen(self, s: _Turn, plan: dict, cwd: str, conn: AcpConnection, method: str):
        """``session/resume`` the stored session. Returns its result, or None when it is missing/unsupported (new session next)."""
        sid = plan["resume_id"]
        s.session_id = sid
        conn.bind_session(sid, lambda u, p: self._on_update(s, u, p))
        try:
            result = await self._call(s, method, conn.reopen_session, method, sid, cwd)
        except AcpError as exc:
            conn.unbind_session(sid)
            s.session_id = None
            hint = exc.hint()
            auth = exc.code == -32000 or any(k in hint for k in ("unauthor", "login", "401", "sign in", "unauthenticated"))
            missing = (not auth) and _MISSING_RE.search(hint) is not None
            if exc.code == -32601 and not auth:
                await self._send(s, "runtime.notice", {"code": "session_resume_unsupported", "message":
                                 "agy refused session resume as unsupported; a new session was started from the conversation history."})
                return None
            if missing:
                self._invalidate(plan)
                await self._send(s, "runtime.notice", {"code": "session_rebuilt", "message":
                                 "The previous Antigravity session could not be resumed; a new session was started from the conversation history."})
                return None
            raise
        returned = result.get("sessionId")
        if returned is not None and returned != sid:  # updates for another id would never be routed to the policy checks
            conn.unbind_session(sid)
            s.session_id = None
            raise _failure("protocol_error", "agy acp protocol violation: resumed a different session.", "resume", "not-submitted")
        return result

    async def _new_session(self, s: _Turn, cwd: str, conn: AcpConnection) -> dict:
        result = await self._call(s, "session/new", conn.new_session, cwd)
        sid = result.get("sessionId")
        if not safe_id(sid):
            raise _failure("protocol_error", "agy acp protocol violation: missing sessionId.", "handshake", "not-submitted")
        s.session_id = sid
        conn.bind_session(sid, lambda u, p: self._on_update(s, u, p))
        return result

    @staticmethod
    def _offered(info: dict):
        models = None
        block = info.get("models")
        if isinstance(block, dict) and isinstance(block.get("availableModels"), list):
            models = {m["modelId"] for m in block["availableModels"] if isinstance(m, dict) and safe_id(m.get("modelId"))}
        modes = None
        block = info.get("modes")
        if isinstance(block, dict) and isinstance(block.get("availableModes"), list):
            modes = {m["id"] for m in block["availableModes"] if isinstance(m, dict) and safe_id(m.get("id"))}
        return models, modes

    async def _select_mode_and_model(self, s: _Turn, conn: AcpConnection, sid: str, info: dict):
        model, required = self._model_for(s), self._mode_for(s)
        models, modes = self._offered(info)
        fresh = not s.resumed  # a session/new result is authoritative; a resume result may omit the lists
        if (modes is None and fresh) or (modes is not None and required not in modes):
            raise _failure("unsupported_capability", "agy acp does not offer plan mode; refusing to run.", "handshake", "not-submitted")
        if (models is None and fresh) or (models is not None and model not in models):
            raise _failure("model_unavailable", "Requested model is not offered by agy acp.", "handshake", "not-submitted")
        try:
            await self._call(s, "session/set_mode", s.conn.request, "session/set_mode", {"sessionId": sid, "modeId": required})
        except AcpError:
            raise _failure("unsupported_capability", "agy acp did not accept plan mode; refusing to run (fail closed).",
                           "handshake", "not-submitted")
        s.plan_set = True
        try:
            await self._call(s, "session/set_model", s.conn.request, "session/set_model", {"sessionId": sid, "modelId": model})
            s.model_seen = s.turn.model_id
            return
        except AcpError as exc:
            if exc.code != -32601:  # only "method not found" may fall back
                raise _failure("model_unavailable", "agy acp did not accept the requested model.", "handshake", "not-submitted")
        config_id = find_model_config(info.get("configOptions"), model)
        if config_id is None:
            raise _failure("model_unavailable",
                           "agy acp supports neither session/set_model nor a model config option for this model.",
                           "handshake", "not-submitted")
        try:
            res = await self._call(s, "session/set_config_option", s.conn.request, "session/set_config_option",
                                   {"sessionId": sid, "configId": config_id, "value": model})
        except AcpError:
            raise _failure("model_unavailable", "agy acp did not accept the requested model.", "handshake", "not-submitted")
        returned = res.get("configOptions")
        if isinstance(returned, list):
            for opt in returned:
                if isinstance(opt, dict) and opt.get("id") == config_id and "currentValue" in opt and opt["currentValue"] != model:
                    raise _failure("model_unavailable", "agy acp kept a different model than requested.", "handshake", "not-submitted")
        s.model_seen = s.turn.model_id

    # -- agent -> client ----------------------------------------------------------------------------------------------
    def _violate(self, s: _Turn, message: str, phase: str = "running") -> DriverTurnError:
        """Record a turn-level failure that _finish and the terminal section both honour (first one wins)."""
        if s.violation is None:
            s.violation = _failure("runtime_error" if "denied" in message else "unsupported_capability", message, phase,
                                   self._acceptance(s))
        return s.violation

    async def _on_request(self, s: _Turn, method: str, params: Any, rid: Any) -> tuple:
        """Deny-only policy for every agent request. The cancelled reply is returned WITHOUT awaiting anything, so the
        transport writes it before the sink is touched; the notice (and any violation) run afterwards."""
        if method == "session/request_permission":
            s.denied_count += 1
            call = params.get("toolCall") if isinstance(params, dict) and isinstance(params.get("toolCall"), dict) else {}
            violation = None
            if safe_id(call.get("toolCallId")):
                s.denied.add(call["toolCallId"])
                if call["toolCallId"] in s.completed_ok:  # it already finished before asking: it acted without permission
                    violation = self._violate(s, _ACTED_AFTER_DENIAL)
            else:
                s.denied_unkeyed = True  # no id to match: any tool that reaches a terminal status afterwards is suspect
            notify = s.denied_count <= MAX_DENIAL_NOTICES

            async def after():
                if notify:
                    try:
                        await self._send(s, "runtime.notice", {"code": "permission_denied",
                                                               "message": "A tool permission request was denied."})
                    except Exception as exc:  # the reply is already written; a sink failure must not undo or stop it
                        logger.warning("antigravity driver could not deliver permission notice (%s)", type(exc).__name__)
                if violation is not None:
                    raise violation

            return "result", {"outcome": {"outcome": "cancelled"}}, after
        s.rejected_count += 1
        return "error", {"code": -32601, "message": "Method not supported by client"}

    def _on_diagnostic(self, s: _Turn, text: str):
        if AUTH_LINK_MARKER in text.lower() and not s.auth_link and s.conn is not None:
            s.auth_link = True  # the agent is waiting for an interactive Google sign-in nobody can complete here
            s.conn.fail(_failure("auth", "agy is not authenticated. Sign in with the agy CLI yourself.", "handshake",
                                 "submitted" if s.submitted else "not-submitted"))

    def _stderr(self, s: _Turn, data):
        if data:
            s.stderr += data
            del s.stderr[:-STDERR_TAIL_BYTES]

    # -- updates ------------------------------------------------------------------------------------------------------
    async def _on_update(self, s: _Turn, update: dict, params: dict):
        kind = update.get("sessionUpdate")
        if kind == "current_mode_update":
            if s.plan_set and update.get("currentModeId") != self._mode_for(s):  # also AFTER the prompt result: fails the turn
                raise self._violate(s, "agy acp left plan mode; aborting (fail closed).")
            return
        if kind == "config_option_update":
            self._config_update(s, update)
            return
        if not s.prompt_sent or s.prompt_done or s.terminal:
            return  # pre-prompt transcript, late frames after the prompt response
        if kind in ("agent_message_chunk", "agent_thought_chunk"):
            await self._text(s, update, assistant=kind == "agent_message_chunk")
        elif kind == "tool_call":
            await self._tool_call(s, update)
        elif kind == "tool_call_update":
            await self._tool_update(s, update)
        elif kind == "usage_update":
            await self._usage_update(s, update)

    def _config_update(self, s: _Turn, update: dict):
        if s.model_seen is None:  # before the requested model was applied: resume transcript / initial state, not drift
            return
        for option in update.get("configOptions") or ():
            if isinstance(option, dict) and (option.get("category") == "model" or option.get("id") == "model") \
                    and isinstance(option.get("currentValue"), str):
                if option["currentValue"] != self._model_for(s):
                    raise self._violate(s, "agy acp switched to a different model than requested; aborting (fail closed).")

    def _close_items(self, s: _Turn):
        for item in (s.cur_msg, s.cur_think):
            if item is not None:
                item.closed = True

    async def _text(self, s: _Turn, update: dict, *, assistant: bool):
        content = update.get("content")
        if not (isinstance(content, dict) and content.get("type") == "text" and isinstance(content.get("text"), str)):
            return
        text = content["text"]
        if not text:
            return
        if assistant:
            if s.text_chars + len(text) > min(s.turn.max_output_chars, MAX_OUTPUT_CHARS):
                raise _failure("output_limit", "agy reply exceeded the output limit; partial text was discarded.", "running", "output-seen")
            s.text_chars += len(text)
        else:
            room = MAX_REASONING_CHARS - s.reasoning_chars
            if room <= 0:
                return
            text = text[:room]
            s.reasoning_chars += len(text)
        mid = update.get("messageId") if isinstance(update.get("messageId"), str) else None
        cur = s.cur_msg if assistant else s.cur_think
        if cur is None or cur.closed or (mid and cur.msg_id and mid != cur.msg_id):
            n = sum(1 for i in s.items if i.kind == ("assistant" if assistant else "reasoning")) + 1
            cur = _Item(f"{s.turn.turn_id}:{'m' if assistant else 'r'}{n}", "assistant" if assistant else "reasoning",
                        msg_id=mid, epoch=s.epoch)
            s.items.append(cur)
            if assistant:
                s.cur_msg = cur
            else:
                s.cur_think = cur
        cur.text += text
        s.output_seen = True
        await self._send(s, "content.delta", {"stream_kind": "assistant_text" if assistant else "reasoning_text",
                                              "delta": text}, item_id=cur.id)

    @staticmethod
    def _tool_title(u: dict) -> str:
        """Generic title from the tool kind: neither the raw command nor the agent's free-text title reach renderer events."""
        kind = u.get("kind")
        return _TOOL_TITLES.get(kind, "Tool call") if isinstance(kind, str) else "Tool call"

    async def _tool_call(self, s: _Turn, u: dict):
        self._close_items(s)
        s.epoch += 1
        status = u.get("status")
        finished = status in ("completed", "failed")
        tid = u.get("toolCallId") if safe_id(u.get("toolCallId")) else None
        if tid is not None and tid in s.tools:
            await self._tool_update(s, u)
            return
        if tid is None:
            tid = f"{s.turn.turn_id}:tool{len(s.tools) + 1}"
            if not finished:
                s.unkeyed.append(tid)
        s.tools[tid] = False
        s.output_seen = True
        data = {"item_type": "tool", "title": self._tool_title(u), "status": "running"}
        if u.get("rawInput") is not None:
            data["input"] = _preview(u["rawInput"])
        await self._send(s, "item.started", data, item_id=tid)
        if finished:
            await self._tool_end(s, tid, u)

    async def _tool_update(self, s: _Turn, u: dict):
        status = u.get("status")
        finished = status in ("completed", "failed")
        tid = u.get("toolCallId") if safe_id(u.get("toolCallId")) else None
        if tid is None and finished and s.unkeyed:
            tid = s.unkeyed.pop(0)
        if tid is None:
            return
        if tid not in s.tools:
            s.tools[tid] = False
            s.output_seen = True
            await self._send(s, "item.started", {"item_type": "tool", "title": self._tool_title(u), "status": "running",
                                                 "synthetic": True}, item_id=tid)
        if finished:
            await self._tool_end(s, tid, u)
        elif isinstance(status, str) and s.tool_status.get(tid) != status and s.tool_updates < MAX_TOOL_UPDATES \
                and not s.tools.get(tid):
            s.tool_status[tid] = status
            s.tool_updates += 1
            await self._send(s, "item.updated", {"item_type": "tool", "status": "running"}, item_id=tid)

    async def _tool_end(self, s: _Turn, tid: str, u: dict):
        if s.tools.get(tid):
            return
        s.tools[tid] = True
        ok = u.get("status") == "completed"
        if ok:
            s.completed_ok.add(tid)
        if tid in s.denied or s.denied_unkeyed:  # ANY terminal status after a denial means the agent went on regardless
            raise self._violate(s, _ACTED_AFTER_DENIAL)
        data = {"item_type": "tool", "status": "completed" if ok else "failed", "ok": ok}
        out = u.get("rawOutput")
        if out is None and isinstance(u.get("content"), list):
            out = [c.get("content", {}).get("text") for c in u["content"]
                   if isinstance(c, dict) and isinstance(c.get("content"), dict) and c["content"].get("text")] or None
        if out is not None:
            data["output"] = _preview(out)
        await self._send(s, "item.completed", data, item_id=tid)

    async def _settle_tools(self, s: _Turn, status: str):
        for tid, done in list(s.tools.items()):
            if not done:
                s.tools[tid] = True
                await self._send(s, "item.completed", {"item_type": "tool", "status": status, "ok": False}, item_id=tid,
                                 settle=True)

    async def _usage_update(self, s: _Turn, u: dict):
        used, size = u.get("used"), u.get("size")
        if not isinstance(used, (int, float)) and not isinstance(size, (int, float)):
            return
        key = (used, size)
        if key == s.last_context or s.usage_events >= MAX_USAGE_EVENTS:
            return
        s.last_context = key
        s.usage_events += 1
        await self._send(s, "usage", {"scope": "context", "measurement_id": "context", "final": False,
                                      "context_size": size if isinstance(size, (int, float)) else None,
                                      "context_used": used if isinstance(used, (int, float)) else None})

    # -- completion ---------------------------------------------------------------------------------------------------
    async def _flush_items(self, s: _Turn):
        """Complete every buffered text item: trailing text (after the last tool call) is the reply, the rest commentary."""
        live = [i for i in s.items if i.text.strip()]
        answers = [i for i in live if i.kind == "assistant" and i.epoch == s.epoch]
        if not answers:
            answers = [i for i in live if i.kind == "assistant"][-1:]
        for item in live:
            data = {"item_type": item.kind, "text": item.text}
            final = item.kind == "assistant" and item in answers
            if item.kind == "assistant":
                data["phase"] = "final" if final else "commentary"
            item.emitted = True
            await self._send(s, "item.completed", data, item_id=item.id)
            if final:
                s.final_ids.append(item.id)

    async def _finish(self, s: _Turn, plan: dict, result: dict) -> str:
        stop = result.get("stopReason")
        if not isinstance(stop, str):
            raise _failure("protocol_error", "agy acp protocol violation: missing stopReason.", "running", self._acceptance(s))
        if stop == "cancelled":
            raise _Cancelled()
        self._check(s)
        if stop == "refusal":
            raise _failure("request_refused", "agy declined to answer (stopReason=refusal).", "running", self._acceptance(s))
        if stop != _OK_STOP:
            label = "".join(c for c in stop[:40] if c.isalnum() or c in "_-")
            raise _failure("runtime_error", f"agy acp stopped abnormally (stopReason={label}); partial text was discarded.",
                           "running", self._acceptance(s))
        if s.violation is not None:
            raise s.violation
        await self._flush_items(s)
        if not s.final_ids:
            message = ("agy finished without a text reply (tool-only turn)." if s.tools else "agy returned no text content.")
            raise _failure("runtime_error", message, "running", "output-seen")
        await self._settle_tools(s, "unknown")
        usage = self._usage(result)
        if usage["tokens"]:
            await self._send(s, "usage", {"scope": "turn", "measurement_id": s.turn.turn_id, "final": True, **usage})
        if s.denied & s.completed_ok:  # rescan: a completion recorded before its denial arrived
            self._violate(s, _ACTED_AFTER_DENIAL)
        if s.violation is not None:  # drift seen while the tail events above were being delivered
            raise s.violation
        self._save_session(s, plan, "ready", s.turn.turn_id)  # no await between the final check and the save
        s.completion_data = {"status": "succeeded", "stop_reason": "end_turn", "final_item_ids": list(s.final_ids),
                             "model_id": s.model_seen or s.turn.model_id, "variant": None, "usage": usage,
                             "session_id": s.session_id}
        return "completed"

    @staticmethod
    def _usage(result: dict) -> dict:
        raw = result.get("usage") if isinstance(result.get("usage"), dict) else {}
        tokens = {}
        for out_key, key in (("input_tokens", "inputTokens"), ("output_tokens", "outputTokens"),
                             ("reasoning_output_tokens", "thoughtTokens"), ("cached_input_tokens", "cachedReadTokens")):
            value = raw.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                tokens[out_key] = value
        return {"tokens": tokens, "cost": None, "input": tokens.get("input_tokens"), "output": tokens.get("output_tokens"),
                "cache_read": tokens.get("cached_input_tokens"), "cache_write": None,
                "reasoning": tokens.get("reasoning_output_tokens"), "context_size": None}

    # -- failures -----------------------------------------------------------------------------------------------------
    def _phase(self, s: _Turn) -> str:
        if s.submitted:
            return "running"
        return "resume" if s.step in ("session/resume", "session/load") else "handshake"

    def _transport_failure(self, s: _Turn, exc: AcpTransportError) -> BaseException:
        if exc.kind == "eof":
            low = bytes(s.stderr).decode("utf-8", "replace").lower() + " " + " ".join(s.conn.diagnostics if s.conn else ())
            if any(k in low for k in ("not logged in", "unauthenticated", "sign in", "login")):
                return _failure("auth", "agy is not authenticated. Sign in with the agy CLI yourself.", self._phase(s),
                                self._acceptance(s))
            return _failure("protocol_error", "agy acp process exited unexpectedly.", self._phase(s), self._acceptance(s))
        if exc.kind == "write":
            return _failure("protocol_error", "agy acp connection closed.", self._phase(s), self._acceptance(s))
        return _failure("protocol_error", f"agy acp protocol violation: {exc.message}.", self._phase(s), self._acceptance(s))

    def _rpc_failure(self, s: _Turn, exc: AcpError) -> DriverTurnError:
        category, message, retryable = classify_rpc(exc, s.step.replace("session/", "") if s.step.startswith("session/") else s.step)
        acceptance = ("output-seen" if s.output_seen else "submitted") if s.submitted else "not-submitted"
        return _failure(category, message, self._phase(s), acceptance, retryable,
                        exc.code if isinstance(exc.code, (int, str)) and exc.code != 0 else None)

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

    # -- interrupt / lifecycle ----------------------------------------------------------------------------------------
    async def _cancel_prompt(self, s: _Turn, grace: float):
        """session/cancel, then wait (bounded) for the agent to answer the outstanding prompt. Never raises."""
        conn, fut = s.conn, s.prompt_fut
        if conn is None or conn.fatal is not None or not s.session_id or not s.prompt_sent or fut is None or fut.done():
            return
        try:
            await conn.cancel_session(s.session_id)
            await asyncio.wait({fut}, timeout=grace)
        except BaseException as exc:
            if isinstance(exc, asyncio.CancelledError):
                raise
            return

    async def _stop(self, s: _Turn):
        await self._cancel_prompt(s, self.interrupt_grace_s)
        if s.conn is not None:
            s.conn.fail(_Cancelled())  # kill fallback: wakes whoever waits; a no-op once the turn already settled
        if s.task is not None and not s.task.done():
            done, _ = await asyncio.wait({s.task}, timeout=self.interrupt_grace_s + 10.0)
            if not done:
                s.task.cancel()
                self._issues.append(CleanupIssue(s.turn.turn_id, None, "Antigravity turn did not settle after interrupt; task cancelled."))
                await asyncio.wait({s.task}, timeout=self.interrupt_grace_s)
        if not s.terminal:
            await self._started(s)
            await self._settle_tools(s, "cancelled")
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
            self._health_rt = AgyAcpRuntime(self._cmd, self._allow_api_key_env)
        return self._health_rt

    async def check_health(self) -> RuntimeHealth:
        return await self._runtime().check_health()

    async def list_available_models(self, free_only=True, *, refresh=False) -> list:
        return await self._runtime().list_available_models(free_only)
