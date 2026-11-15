"""Native Claude Code turn driver: ``claude -p --input-format stream-json --output-format stream-json``.

One owned process per turn, launched through ``supervisor.SupervisedProcess`` (Job Object on Windows). Claude's
stream frames are mapped to the normalised events of ``driver.py``; exactly one ``turn.completed`` settles a turn.

Tool policy is unchanged from ``ClaudeCodeRuntime``: tool-less by default (``--tools ""``, ``--strict-mcp-config``) and
the existing scoped MCP broker path when the caller supplies ``tool_policy.mcp_servers``. Permission prompts are never
approved: ``--permission-prompts none`` when the CLI offers it, and any ``can_use_tool`` control request is denied.

Sessions: only conversation scopes persist a native session (``runtime_sessions`` via ``SessionRepository``). The first
turn launches ``--session-id <uuid>`` (binding known before the prompt is submitted); later turns launch
``--resume <id>``. CEO and mission scopes use ``--no-session-persistence`` (fresh, isolated per call). If a resume is
refused by Claude *before* it initialises (proven: no ``system/init`` frame and an explicit missing-session message),
the stored session is marked invalid and ONE fresh session is started with the caller's full (history-seeded) prompt and
a ``session_rebuilt`` notice. Any other failure, including timeout or EOF after submission, is never replayed.

Interrupt: the stream-json input channel has no established graceful interrupt (RuseBot also kills), so ``interrupt``
terminates the owned process tree and settles the turn as cancelled; the bound is the supervisor's own kill bound
plus ``interrupt_grace_s`` for the settlement.

Hooks/settings: ``--setting-sources project`` (never ``user``/``local``) so user hooks and user settings do not load;
the cwd is an AgentOS-owned empty directory so there are no project settings either. ``--bare`` and ``--safe-mode`` are
deliberately NOT used: ``--bare`` disables OAuth/keychain login and ``--safe-mode`` would also disable MCP servers.
"""

from __future__ import annotations

import asyncio
import contextvars
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
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Optional

from backend.pyworker.db.core import new_id, ts
from .base import AgentResult, ModelCatalogEntry, RuntimeHealth
from .driver import (
    ApprovalAnswer, CleanupIssue, DriverCapabilities, DriverEvent, DriverFailure, DriverTurnError,
    Emit, SessionRef, StartTurn, TurnRef,
)
from .sessions import SessionRepository
from .supervisor import SupervisedProcess

logger = logging.getLogger("agentos.runtime.claude_driver")

PROTOCOL = "claude-stream-json"
MAX_PROMPT_CHARS = 24_000
MAX_LINE_BYTES = 4 * 1024 * 1024
MAX_STREAM_BYTES = 64 * 1024 * 1024
MAX_TIMEOUT_S = 600.0
PREVIEW_CHARS = 400
STDERR_TAIL_BYTES = 8192
MCP_ALLOWED_TOOLS = "mcp__agentos_workspace__*"
_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-\[\]]{0,63}$")
_REFUSED_RE = re.compile(r"no conversation found|session[^.\n]{0,60}(not found|does not exist|no longer exists|invalid)", re.I)
_SECRET_KEY_RE = re.compile(r"token|secret|password|passwd|api[_-]?key|authorization|credential", re.I)

# Flags the driver relies on; every one must be advertised by `claude --help` or the driver refuses to start.
REQUIRED_FLAGS = ("--print", "--input-format", "--output-format", "--verbose", "--include-partial-messages",
                  "--tools", "--strict-mcp-config", "--model", "--setting-sources", "--disable-slash-commands",
                  "--no-session-persistence", "--resume", "--session-id", "--mcp-config", "--allowedTools")
OPTIONAL_FLAGS = ("--permission-prompts",)

# -- S5: explicit child environment ---------------------------------------------------------------------------------
# Claude's login lives in the user profile (credentials file / keychain), not in the environment, so nothing secret is
# needed. API keys/tokens (ANTHROPIC_*, AGENTOS_*, cloud credentials, ...) are simply never copied.
_ENV_NAMES = frozenset((
    "PATH SYSTEMROOT SYSTEMDRIVE WINDIR COMSPEC PATHEXT TEMP TMP TMPDIR USERPROFILE USERNAME USERDOMAIN COMPUTERNAME "
    "HOMEDRIVE HOMEPATH HOME APPDATA LOCALAPPDATA PROGRAMDATA OS NUMBER_OF_PROCESSORS PROCESSOR_ARCHITECTURE "
    "LANG LANGUAGE TZ CLAUDE_CONFIG_DIR CLAUDE_CODE_GIT_BASH_PATH SSL_CERT_FILE SSL_CERT_DIR NODE_EXTRA_CA_CERTS "
    "HTTP_PROXY HTTPS_PROXY ALL_PROXY NO_PROXY").split())
_ENV_PREFIXES = ("PROGRAMFILES", "COMMONPROGRAMFILES", "PROGRAMW6432", "LC_")
_PROXY_NAMES = frozenset(("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"))


def claude_child_env(source: Optional[dict] = None, extra: Optional[dict] = None) -> dict:
    """Allowlisted environment for the Claude child. ``extra`` is for explicit, caller-reviewed additions (tests)."""
    source = os.environ if source is None else source
    env = {}
    for key, value in source.items():
        name = key.upper()
        if name not in _ENV_NAMES and not name.startswith(_ENV_PREFIXES):
            continue
        if name in _PROXY_NAMES and "@" in value.split("://", 1)[-1].split("/", 1)[0]:
            continue  # proxy URL with embedded credentials: never forwarded
        env[key] = value
    env.update(extra or {})
    return env


# -- feature routing helpers ---------------------------------------------------------------------------------------
_FLAG_CACHE: dict = {}


def default_cli_cmd() -> Optional[list]:
    path = shutil.which("claude")
    return [path] if path else None


def conversation_resume_check(db, conversation_id: str, run_id: str) -> Callable[[dict], bool]:
    """A stored session is resumable only if it saw the conversation's latest successful turn (no gaps)."""
    def check(row: dict) -> bool:
        last = db.scalar("SELECT id FROM conversation_runs WHERE conversation_id=? AND status='succeeded' AND id<>? "
                         "ORDER BY created_at DESC, rowid DESC LIMIT 1", (conversation_id, run_id))
        return bool(last) and last == row.get("last_turn_id")
    return check


def _preview(value: Any) -> Any:
    """Bounded, key-redacted preview for renderer events (full native arguments never leave the driver)."""
    def scrub(v, depth=0):
        if isinstance(v, dict) and depth < 4:
            return {k: ("[redacted]" if _SECRET_KEY_RE.search(str(k)) else scrub(x, depth + 1)) for k, x in list(v.items())[:30]}
        if isinstance(v, list) and depth < 4:
            return [scrub(x, depth + 1) for x in v[:30]]
        return v
    try:
        text = value if isinstance(value, str) else json.dumps(scrub(value), ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        text = str(value)
    return text if len(text) <= PREVIEW_CHARS else text[:PREVIEW_CHARS] + "..."


# -- supervised process with streaming tee ---------------------------------------------------------------------------
_SINKS: contextvars.ContextVar = contextvars.ContextVar("claude_driver_sinks")


class _TeeStream:
    """Wraps a Popen pipe so SupervisedProcess's own bounded drain also feeds our incremental parser."""

    def __init__(self, raw, sink):
        self._raw, self._sink = raw, sink

    def fileno(self):
        return self._raw.fileno()

    def read1(self, n):
        data = self._raw.read1(n)
        if data:
            self._sink(data)
        return data

    def close(self):
        try:
            self._raw.close()
        finally:
            self._sink(None)


class _StreamProcess(SupervisedProcess):
    def __init__(self, proc, job, max_bytes, issues):
        on_out, on_err = _SINKS.get()
        loop = asyncio.get_running_loop()

        def threadsafe(callback):
            def sink(data):
                try:
                    loop.call_soon_threadsafe(callback, data)
                except RuntimeError:
                    pass  # loop already closed during teardown
            return sink

        proc.stdout = _TeeStream(proc.stdout, threadsafe(on_out))
        proc.stderr = _TeeStream(proc.stderr, threadsafe(on_err))
        super().__init__(proc, job, max_bytes, issues)

    @classmethod
    async def launch(cls, argv, *, cwd, env, on_out, on_err, max_bytes=MAX_STREAM_BYTES):
        token = _SINKS.set((on_out, on_err))
        try:
            return await cls.start(argv, cwd=cwd, env=env, max_bytes=max_bytes)
        finally:
            _SINKS.reset(token)


class _Cancelled(Exception):
    pass


@dataclass
class _Item:
    id: str
    kind: str            # assistant | reasoning
    msg_id: Optional[str] = None
    text: str = ""
    completed: bool = False
    emitted: bool = False
    streamed: bool = False


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
    proc: Optional[SupervisedProcess] = None
    # per-attempt state
    stderr: bytearray = field(default_factory=bytearray)
    session_id: Optional[str] = None
    saw_init: bool = False
    submitted: bool = False
    output_seen: bool = False
    result: Optional[dict] = None
    api_error: Optional[str] = None
    items: list = field(default_factory=list)
    blocks: dict = field(default_factory=dict)
    msg_id: Optional[str] = None
    tools: dict = field(default_factory=dict)       # tool_use id -> completed?
    final_ids: list = field(default_factory=list)
    text_chars: int = 0
    generation: int = 1
    resumed: bool = False
    rebuilt: bool = False
    model_seen: Optional[str] = None
    plan: Optional[dict] = None
    deadline: float = 0.0
    completion_data: dict = field(default_factory=dict)


def _failure(category, message, phase, acceptance, retryable=False, native_code=None):
    return DriverTurnError(DriverFailure(category, message, retryable, phase, acceptance, native_code))


def classify_text(text: str) -> tuple:
    """(category, safe message, retryable) from bounded CLI text; never echoes the raw text."""
    low = (text or "").lower()
    if any(k in low for k in ("not logged in", "/login", "invalid api key", "unauthorized", "authentication", "401",
                              "oauth token")):
        return "auth", "Claude Code is not authenticated. Run `claude auth login` yourself.", False
    if any(k in low for k in ("usage limit", "quota")):
        return "quota", "Claude Code usage limit reached. No fallback was used.", False
    if any(k in low for k in ("429", "rate limit", "overloaded")):
        return "rate_limit", "Claude Code rate limit reached. No fallback was used.", True
    if "model" in low and any(k in low for k in ("not found", "unknown", "invalid", "not available",
                                                 "may not exist", "access")):
        return "model_unavailable", "Requested model is not available to this Claude account.", False
    if any(k in low for k in ("econnrefused", "enotfound", "network", "connection", "timed out")):
        return "offline", "Claude Code could not reach its service.", True
    return "runtime_error", "Claude Code execution failed.", False


class ClaudeStreamDriver:
    engine = "claude-code"
    capabilities = DriverCapabilities(True, True, False, False, "cli-resume", "relaunch", True)

    def __init__(self, cli_cmd=None, *, runtime_key: str = "default", sessions: Optional[SessionRepository] = None,
                 resume_prompt: Optional[str] = None, resume_check: Optional[Callable[[dict], bool]] = None,
                 cwd_root: Optional[str] = None, env: Optional[dict] = None, extra_env: Optional[dict] = None,
                 interrupt_grace_s: float = 3.0, exit_grace_s: float = 2.0, cache_flags: bool = True):
        if isinstance(cli_cmd, str):
            cli_cmd = [cli_cmd]
        self._cmd = list(cli_cmd) if cli_cmd else default_cli_cmd()
        self.runtime_key = runtime_key
        self.sessions = sessions
        self.resume_prompt = resume_prompt
        self.resume_check = resume_check
        self._cwd_root = cwd_root
        self._env_source, self._extra_env = env, extra_env
        self.interrupt_grace_s, self.exit_grace_s = interrupt_grace_s, exit_grace_s
        self._cache_flags = cache_flags
        self._turns: dict = {}
        self._issues: list = []
        self._health_rt = None

    # -- environment / flags ------------------------------------------------------------------------------------------
    def _env(self) -> dict:
        return claude_child_env(self._env_source, self._extra_env)

    def _namespace(self) -> str:
        return "claude:" + (self._env().get("CLAUDE_CONFIG_DIR") or "default")

    def _conversation_cwd(self) -> str:
        root = self._cwd_root
        if root is None:
            from backend.pyworker.appsettings import settings
            root = os.path.join(os.path.dirname(os.path.abspath(settings.SQLITE_DB_PATH)), "claude-sessions")
        path = os.path.join(root, "conversation")
        os.makedirs(path, exist_ok=True)
        return path

    def _probe_help(self) -> str:
        kwargs: dict = {}
        if sys.platform == "win32":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            res = subprocess.run([*self._cmd, "--help"], stdin=subprocess.DEVNULL, capture_output=True, text=True,
                                 encoding="utf-8", errors="replace", timeout=10.0, shell=False, env=self._env(), **kwargs)
        except (subprocess.TimeoutExpired, OSError, ValueError):
            return ""
        return (res.stdout or "") + (res.stderr or "")

    async def _flags(self) -> frozenset:
        key = tuple(self._cmd)
        if self._cache_flags and key in _FLAG_CACHE:
            return _FLAG_CACHE[key]
        text = await asyncio.to_thread(self._probe_help)
        found = frozenset(f for f in (*REQUIRED_FLAGS, *OPTIONAL_FLAGS)
                          if re.search(r"(?<![\w-])" + re.escape(f) + r"(?![\w-])", text))
        if self._cache_flags and text:
            _FLAG_CACHE[key] = found
        return found

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
        self._turns[turn.turn_id] = state  # cancellation registration precedes scheduling
        state.task = asyncio.create_task(self._run(state), name=f"claude-driver-{turn.turn_id}")
        return TurnRef(turn.turn_id, turn.run_id)

    # -- event plumbing -----------------------------------------------------------------------------------------------
    async def _send(self, s: _Turn, type_: str, data: dict, *, item_id=None, session_id=None):
        if s.terminal or (s.cancelled and type_ not in {"turn.started", "turn.completed"}):
            return
        s.seq += 1
        if type_ == "turn.completed":
            s.terminal = True  # the sole settlement gate, before awaiting the sink
        await s.emit(DriverEvent(1, new_id(), s.turn.turn_id, s.turn.run_id, s.seq, self.engine, self.runtime_key, ts(),
                                 type_, data, native_session_id=session_id or s.session_id, item_id=item_id))

    async def _started(self, s: _Turn):
        if not s.started:
            s.started = True
            p = s.turn.tool_policy
            await self._send(s, "turn.started", {"model_id": s.turn.model_id,
                                                  "policy": {"mode": p.mode, "approval_mode": p.approval_mode}})

    # -- planning -----------------------------------------------------------------------------------------------------
    def _plan(self, s: _Turn, flags: frozenset, *, force_fresh: bool = False) -> dict:
        t = s.turn
        persist = t.scope_kind == "conversation" and self.sessions is not None
        cwd = self._conversation_cwd() if persist else None
        resume_id, row = None, None
        generation = 1
        if persist:
            row = self.sessions.get(t.scope_kind, t.scope_id, self.engine, self.runtime_key)
            if t.session is not None:
                if t.session.protocol != PROTOCOL:
                    raise _failure("unsupported_capability", "Session protocol is not supported by Claude.", "resume", "not-submitted")
                resume_id, generation = t.session.native_session_id, t.session.generation
            elif (row is not None and not force_fresh and not t.session_reset and row["state"] != "invalid"
                  and row["last_turn_id"] and row["protocol"] == PROTOCOL and row["namespace"] == self._namespace() and row["cwd"] == cwd
                  and (self.resume_check is None or self.resume_check(row))):
                resume_id, generation = row["native_session_id"], row["generation"]
            if resume_id is None and row is not None:
                generation = row["generation"] + 1
        prompt = (self.resume_prompt if resume_id and self.resume_prompt else t.prompt)
        if len(prompt) > MAX_PROMPT_CHARS:
            raise _failure("request_refused", "Prompt is too long for Claude Code.", "launch", "not-submitted")
        servers = t.tool_policy.mcp_servers if t.tool_policy.mode == "scoped" else {}
        return {"persist": persist, "cwd": cwd, "resume_id": resume_id, "new_id": None if resume_id else str(uuid.uuid4()),
                "generation": generation, "prompt": prompt, "servers": servers, "row": row,
                "rebuilt": bool(persist and resume_id is None and row is not None)}

    def _argv(self, s: _Turn, plan: dict, flags: frozenset, mcp_path: Optional[str]) -> list:
        argv = [*self._cmd, "-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
                "--include-partial-messages", "--model", s.turn.model_id, "--tools", "ToolSearch" if plan["servers"] else "",
                "--strict-mcp-config", "--setting-sources", "project", "--disable-slash-commands"]
        if "--permission-prompts" in flags:
            argv += ["--permission-prompts", "none"]
        if plan["persist"]:
            argv += ["--resume", plan["resume_id"]] if plan["resume_id"] else ["--session-id", plan["new_id"]]
        else:
            argv += ["--no-session-persistence"]
        if plan["servers"]:
            argv += ["--mcp-config", mcp_path, "--allowedTools", MCP_ALLOWED_TOOLS]
        return argv

    def _fingerprint(self, s: _Turn, plan: dict) -> str:
        p = s.turn.tool_policy
        blob = json.dumps({"model": s.turn.model_id, "mode": p.mode, "tools": sorted(p.allowed_tools),
                           "mcp": sorted(plan["servers"])}, sort_keys=True)
        return hashlib.sha256(blob.encode()).hexdigest()[:32]

    def _save_session(self, s: _Turn, plan: dict, state: str, last_turn_id: Optional[str]):
        if not plan["persist"] or not s.session_id:
            return
        try:
            self.sessions.put(
                scope_kind=s.turn.scope_kind, scope_id=s.turn.scope_id, engine=self.engine, runtime_key=self.runtime_key,
                protocol=PROTOCOL, namespace=self._namespace(), native_session_id=s.session_id, generation=s.generation,
                state=state, model_id=s.turn.model_id, provider_id=s.turn.provider_id, variant=s.turn.variant,
                agent_id=s.turn.agent_id, cwd=plan["cwd"], config_fingerprint=self._fingerprint(s, plan),
                last_turn_id=last_turn_id)
        except Exception as exc:  # persistence must never fail a turn; the session just is not resumable
            logger.warning("could not persist Claude session binding (%s)", type(exc).__name__)

    def _invalidate(self, s: _Turn, plan: dict):
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
            logger.warning("could not invalidate Claude session binding (%s)", type(exc).__name__)

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
                raise _failure("cli_missing", "Claude Code CLI not found on PATH.", "launch", "not-submitted")
            flags = await self._flags()
            missing = [f for f in REQUIRED_FLAGS if f not in flags]
            if missing:
                raise _failure("unsupported_capability", "Installed Claude CLI does not advertise the flags required for "
                               "streaming tool-less mode; refusing to run.", "launch", "not-submitted")
            plan = self._plan(s, flags)
            outcome = await self._attempt(s, plan, flags)
            if outcome == "resume_refused":
                self._invalidate(s, plan)
                self._reset_attempt(s)
                s.rebuilt = True
                await self._send(s, "runtime.notice", {"code": "session_rebuilt", "message":
                                 "The previous Claude session could not be resumed; a new session was started from the conversation history."})
                plan = self._plan(s, flags, force_fresh=True)
                outcome = await self._attempt(s, plan, flags)
                if outcome == "resume_refused":
                    raise _failure("runtime_error", "Claude Code refused a brand-new session.", "launch", "not-submitted")
            completion = outcome
        except _Cancelled:
            s.cancelled = True
        except asyncio.CancelledError:
            s.cancelled = True
        except DriverTurnError as exc:
            failure = exc.failure
        except FileNotFoundError:
            failure = DriverFailure("cli_missing", "Claude Code CLI could not be started.", False, "launch", "not-submitted")
        except OSError:
            failure = DriverFailure("cli_missing", "Claude Code CLI could not be started.", False, "launch", "not-submitted")
        except Exception as exc:
            logger.warning("claude driver internal error (%s)", type(exc).__name__)
            failure = DriverFailure("runtime_error", "Claude driver failed unexpectedly.", False, "running", "unknown")
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
            logger.warning("claude driver could not deliver terminal event (%s)", type(exc).__name__)

    def _validate(self, s: _Turn):
        t = s.turn
        if t.variant not in (None, "", "default"):
            raise _failure("unsupported_capability", "Claude Code adapter does not support model variants.", "launch", "not-submitted")
        if t.system:
            raise _failure("unsupported_capability", "A separate system prompt is not supported.", "launch", "not-submitted")
        if not _MODEL_RE.match(t.model_id or ""):
            raise _failure("model_unavailable", "Invalid Claude model identifier.", "launch", "not-submitted")
        if not (t.prompt or "").strip():
            raise _failure("request_refused", "Empty prompt: refusing to invoke the model.", "launch", "not-submitted")

    def _reset_attempt(self, s: _Turn):
        s.stderr = bytearray()
        s.queue = asyncio.Queue()
        s.session_id, s.saw_init, s.submitted, s.output_seen = None, False, False, False
        s.result, s.api_error, s.items, s.blocks, s.msg_id = None, None, [], {}, None
        s.tools, s.final_ids, s.text_chars = {}, [], 0

    async def _cleanup(self, s: _Turn):
        proc, s.proc = s.proc, None
        if proc is None:
            return
        try:
            issues = await proc.close()
            self._issues.extend(issues)
            self._issues[:] = self._issues[-256:]
        except asyncio.CancelledError:
            raise
        except Exception:
            self._issues.append(CleanupIssue(s.turn.turn_id, None, "Claude process cleanup failed."))

    # -- one launch ---------------------------------------------------------------------------------------------------
    async def _attempt(self, s: _Turn, plan: dict, flags: frozenset) -> str:
        if s.cancelled:
            raise _Cancelled()
        s.generation, s.resumed, s.plan = plan["generation"], plan["resume_id"] is not None, plan
        s.rebuilt = s.rebuilt or plan["rebuilt"]
        with tempfile.TemporaryDirectory(prefix="agentos-claude-", ignore_cleanup_errors=True) as scratch:
            mcp_path = None
            if plan["servers"]:
                mcp_path = os.path.join(scratch, "mcp.json")
                with open(mcp_path, "w", encoding="utf-8") as fh:
                    json.dump({"mcpServers": plan["servers"]}, fh)
            cwd = plan["cwd"] or scratch
            argv = self._argv(s, plan, flags, mcp_path)
            queue = s.queue
            proc = await _StreamProcess.launch(
                argv, cwd=cwd, env=self._env(),
                on_out=lambda d: queue.put_nowait(("eof",) if d is None else ("out", d)),
                on_err=lambda d: self._stderr(s, d))
            s.proc = proc
            exited = None
            try:
                if s.cancelled:
                    raise _Cancelled()
                if plan["new_id"]:  # the binding is durable before the prompt is submitted
                    s.session_id = plan["new_id"]
                    self._save_session(s, plan, "unverified", None)
                exited = asyncio.ensure_future(asyncio.to_thread(proc.proc.wait))
                exited.add_done_callback(lambda _f: queue.put_nowait(("exit",)))
                frame = {"type": "user", "message": {"role": "user", "content": plan["prompt"]}}
                try:
                    await asyncio.to_thread(proc.proc.stdin.write, (json.dumps(frame, ensure_ascii=False) + "\n").encode("utf-8"))
                    await asyncio.to_thread(proc.proc.stdin.flush)
                    s.submitted = True
                except (BrokenPipeError, OSError, ValueError):
                    s.submitted = False  # the reader below reports why the process died
                await self._read(s, queue, proc)
                return await self._finish(s, plan, proc)
            finally:
                if exited is not None:
                    exited.cancel()
                await self._release(s, proc, graceful=s.result is not None)

    async def _read(self, s: _Turn, queue: asyncio.Queue, proc):
        loop = asyncio.get_running_loop()
        buf = bytearray()
        exit_seen_at = None
        while s.result is None:
            if s.cancelled:
                raise _Cancelled()
            remaining = s.deadline - loop.time()
            if exit_seen_at is not None:
                remaining = min(remaining, max(0.0, exit_seen_at + self.exit_grace_s - loop.time()))
            if remaining <= 0:
                if exit_seen_at is not None:
                    break  # process gone, pipe held open by a descendant: treat as EOF
                raise _failure("timeout", f"Claude Code did not finish within {int(s.turn.timeout_s)}s; process killed.",
                               "running", "output-seen" if s.output_seen else "unknown", True)
            try:
                item = await asyncio.wait_for(queue.get(), remaining)
            except asyncio.TimeoutError:
                continue
            kind = item[0]
            if kind == "cancel":
                raise _Cancelled()
            if kind == "exit":
                exit_seen_at = loop.time()
                continue
            if kind == "eof":
                if buf.strip():
                    await self._line(s, bytes(buf))
                break
            buf += item[1]
            if proc._overflow.is_set():
                raise _failure("output_limit", "Engine output exceeded the allowed byte limit.", "running", "output-seen")
            while s.result is None and (nl := buf.find(b"\n")) >= 0:
                line = bytes(buf[:nl])
                del buf[:nl + 1]
                await self._line(s, line)
            if len(buf) > MAX_LINE_BYTES:
                raise _failure("protocol_error", "Claude stream line exceeded the size limit.", "running", "output-seen")

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
        issues = await proc.close()
        self._issues.extend(issues)
        self._issues[:] = self._issues[-256:]

    def _stderr(self, s: _Turn, data):
        if data:
            s.stderr += data
            del s.stderr[:-STDERR_TAIL_BYTES]

    async def _finish(self, s: _Turn, plan: dict, proc) -> str:
        stderr = bytes(s.stderr).decode("utf-8", "replace")
        result = s.result
        if result is None:
            # EOF/exit with no result frame.
            if plan["resume_id"] and not s.saw_init and _REFUSED_RE.search(stderr + (s.api_error or "")):
                return "resume_refused"
            category, message, retryable = classify_text(stderr + (s.api_error or ""))
            if category == "runtime_error":
                category, message = "protocol_error", "Claude Code exited without a result."
            raise _failure(category, message, "handshake" if not s.saw_init else "running",
                           "output-seen" if s.output_seen else ("unknown" if s.submitted else "not-submitted"), retryable)
        text = result.get("result") if isinstance(result.get("result"), str) else ""
        if result.get("is_error"):
            if plan["resume_id"] and not s.saw_init and _REFUSED_RE.search(text + stderr):
                return "resume_refused"
            category, message, retryable = classify_text(text + " " + (s.api_error or "") + " " + stderr)
            raise _failure(category, message, "running", "output-seen" if s.output_seen else "submitted", retryable,
                           result.get("subtype") if isinstance(result.get("subtype"), str) else None)
        await self._flush_items(s, final=True)
        if not s.final_ids and text.strip():
            item = _Item(f"{s.turn.turn_id}:result", "assistant", text=text, completed=True)
            s.items.append(item)
            await self._emit_item(s, item, "final")
            s.final_ids.append(item.id)
        if not s.final_ids:
            raise _failure("runtime_error", "Claude Code returned no text content.", "running", "output-seen")
        await self._settle_tools(s, "unknown")
        usage_raw = result.get("usage") if isinstance(result.get("usage"), dict) else {}
        tokens = {k: usage_raw[k] for k in ("input_tokens", "output_tokens", "cache_read_input_tokens",
                                            "cache_creation_input_tokens") if isinstance(usage_raw.get(k), (int, float))}
        if isinstance(result.get("total_cost_usd"), (int, float)):
            tokens["reported_cost_usd"] = result["total_cost_usd"]  # estimate, not billing
        usage = {"tokens": tokens, "cost": None, "input": tokens.get("input_tokens"), "output": tokens.get("output_tokens"),
                 "cache_read": tokens.get("cache_read_input_tokens"), "cache_write": tokens.get("cache_creation_input_tokens"),
                 "reasoning": None, "context_size": None}
        if tokens:
            await self._send(s, "usage", {"scope": "turn", "measurement_id": s.turn.turn_id, "final": True, **usage})
        model = s.turn.model_id
        mu = result.get("modelUsage")
        if isinstance(mu, dict) and len(mu) == 1:
            model = str(next(iter(mu)))
        self._save_session(s, plan, "ready", s.turn.turn_id)
        s.completion_data = {"status": "succeeded", "stop_reason": result.get("stop_reason") or "end_turn",
                             "final_item_ids": list(s.final_ids), "model_id": model, "variant": None, "usage": usage,
                             "session_id": s.session_id}
        return "completed"

    # -- frames -------------------------------------------------------------------------------------------------------
    async def _line(self, s: _Turn, raw: bytes):
        raw = raw.strip()
        if not raw:
            return
        try:
            o = json.loads(raw.decode("utf-8", "replace"))
        except ValueError:
            return  # non-protocol noise on stdout is ignored (bounded by the line limit)
        if not isinstance(o, dict):
            return
        kind = o.get("type")
        if kind == "system":
            if o.get("subtype") == "init" and isinstance(o.get("session_id"), str):
                s.saw_init, s.session_id = True, o["session_id"]
                if isinstance(o.get("model"), str):
                    s.model_seen = o["model"]
                await self._send(s, "session.started", {
                    "session_id": s.session_id, "protocol": PROTOCOL, "namespace": self._namespace(),
                    "generation": s.generation, "model_id": s.model_seen or s.turn.model_id, "resumed": s.resumed,
                    "rebuilt": s.rebuilt})
                if s.plan is not None:
                    row = s.plan["row"]
                    self._save_session(s, s.plan, "unverified", row["last_turn_id"] if (row and s.resumed) else None)
        elif kind == "stream_event":
            if o.get("parent_tool_use_id"):
                return
            await self._stream_event(s, o.get("event") if isinstance(o.get("event"), dict) else {})
        elif kind == "assistant":
            if o.get("parent_tool_use_id"):
                return
            await self._assistant(s, o)
        elif kind == "user":
            if o.get("isReplay") is True or o.get("parent_tool_use_id"):
                return
            content = (o.get("message") or {}).get("content")
            for block in content if isinstance(content, list) else []:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    await self._tool_done(s, block)
        elif kind == "tool_progress":
            tid = o.get("tool_use_id")
            if isinstance(tid, str) and tid in s.tools:
                await self._send(s, "item.updated", {"item_type": "tool", "status": "running"}, item_id=tid)
        elif kind == "control_request":
            await self._control(s, o)
        elif kind == "result":
            if isinstance(o.get("origin"), dict) and o["origin"].get("kind") == "task-notification":
                return
            s.result = o

    async def _control(self, s: _Turn, o: dict):
        req = o.get("request") if isinstance(o.get("request"), dict) else {}
        request_id = o.get("request_id")
        if req.get("subtype") != "can_use_tool" or not isinstance(request_id, str):
            return
        response = {"type": "control_response", "response": {"subtype": "success", "request_id": request_id, "response": {
            "behavior": "deny", "message": "AgentOS does not grant interactive permissions."}}}
        await self._send(s, "runtime.notice", {"code": "permission_denied", "message": "A tool permission request was denied."})
        if s.proc is not None:
            try:
                await asyncio.to_thread(s.proc.proc.stdin.write, (json.dumps(response) + "\n").encode("utf-8"))
                await asyncio.to_thread(s.proc.proc.stdin.flush)
            except (BrokenPipeError, OSError, ValueError):
                pass

    def _check_text_budget(self, s: _Turn, extra: int):
        s.text_chars += extra
        if s.text_chars > s.turn.max_output_chars:
            raise _failure("output_limit", "Engine output exceeded the allowed character limit.", "running", "output-seen")

    async def _stream_event(self, s: _Turn, ev: dict):
        et = ev.get("type")
        if et == "message_start":
            msg = ev.get("message") if isinstance(ev.get("message"), dict) else {}
            s.msg_id = msg.get("id") if isinstance(msg.get("id"), str) else None
            s.blocks = {}
        elif et == "content_block_start":
            cb = ev.get("content_block") if isinstance(ev.get("content_block"), dict) else {}
            if cb.get("type") in ("text", "thinking"):
                s.blocks[ev.get("index")] = self._new_item(s, "assistant" if cb["type"] == "text" else "reasoning")
        elif et == "content_block_delta":
            d = ev.get("delta") if isinstance(ev.get("delta"), dict) else {}
            if d.get("type") == "text_delta" and isinstance(d.get("text"), str) and d["text"]:
                await self._delta(s, ev.get("index"), "assistant", d["text"])
            elif d.get("type") == "thinking_delta" and isinstance(d.get("thinking"), str) and d["thinking"]:
                await self._delta(s, ev.get("index"), "reasoning", d["thinking"])

    def _new_item(self, s: _Turn, kind: str) -> _Item:
        item = _Item(f"{s.turn.turn_id}:{'a' if kind == 'assistant' else 'r'}{len(s.items) + 1}", kind, msg_id=s.msg_id)
        s.items.append(item)
        return item

    async def _delta(self, s: _Turn, index, kind: str, text: str):
        item = s.blocks.get(index)
        if item is None or item.kind != kind:
            item = s.blocks[index] = self._new_item(s, kind)
        self._check_text_budget(s, len(text))
        item.text += text
        item.streamed = True
        s.output_seen = True
        await self._send(s, "content.delta", {"stream_kind": "assistant_text" if kind == "assistant" else "reasoning_text",
                                              "delta": text}, item_id=item.id)

    async def _emit_item(self, s: _Turn, item: _Item, phase: str):
        item.emitted = True
        data = {"item_type": item.kind, "text": item.text}
        if item.kind == "assistant":
            data["phase"] = phase
        await self._send(s, "item.completed", data, item_id=item.id)

    async def _flush_items(self, s: _Turn, *, final: bool):
        """Complete buffered text items. Text before a tool call is commentary; text at the end is the final reply."""
        for item in s.items:
            if item.emitted or not item.text.strip():
                continue
            await self._emit_item(s, item, "final" if (final and item.kind == "assistant") else "commentary")
            if final and item.kind == "assistant":
                s.final_ids.append(item.id)

    async def _assistant(self, s: _Turn, o: dict):
        msg = o.get("message") if isinstance(o.get("message"), dict) else {}
        content = msg.get("content") if isinstance(msg.get("content"), list) else []
        text = "".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"
                       and isinstance(b.get("text"), str))
        if o.get("is_api_error_message") is True or isinstance(o.get("error"), str):
            s.api_error = (s.api_error or "") + " " + (text or str(o.get("error")))[:500]
            await self._send(s, "runtime.notice", {"code": "api_error_frame", "message": "Claude reported an API error."})
            return
        mid = msg.get("id") if isinstance(msg.get("id"), str) else s.msg_id
        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype in ("text", "thinking") and isinstance(block.get("text" if btype == "text" else "thinking"), str):
                kind = "assistant" if btype == "text" else "reasoning"
                full = block["text" if btype == "text" else "thinking"]
                item = next((i for i in s.items if i.kind == kind and not i.completed and i.msg_id == mid and i.streamed), None)
                if item is None:
                    if not full.strip():
                        continue
                    s.msg_id = mid
                    item = self._new_item(s, kind)
                    self._check_text_budget(s, len(full))
                    item.text = full
                    s.output_seen = True
                    await self._send(s, "content.delta", {"stream_kind": "assistant_text" if kind == "assistant" else "reasoning_text",
                                                          "delta": full}, item_id=item.id)  # fallback only when nothing streamed
                else:
                    s.text_chars += len(full) - len(item.text)
                    item.text = full  # authoritative; replaces the provisional buffer, never appended again
                item.completed = True
            elif btype == "tool_use":
                await self._flush_items(s, final=False)
                tid = block.get("id") if isinstance(block.get("id"), str) and block.get("id") else f"{s.turn.turn_id}:tool{len(s.tools) + 1}"
                s.tools[tid] = False
                s.output_seen = True
                await self._send(s, "item.started", {"item_type": "tool", "title": str(block.get("name", "tool"))[:80],
                                                     "status": "running", "input": _preview(block.get("input"))}, item_id=tid)

    async def _tool_done(self, s: _Turn, block: dict):
        tid = block.get("tool_use_id")
        if not isinstance(tid, str) or not tid:
            return
        if tid not in s.tools:
            s.tools[tid] = False
            await self._send(s, "item.started", {"item_type": "tool", "title": "tool", "status": "running", "synthetic": True},
                             item_id=tid)
        ok = not block.get("is_error")
        s.tools[tid] = True
        await self._send(s, "item.completed", {"item_type": "tool", "status": "completed" if ok else "failed", "ok": ok,
                                               "output": _preview(block.get("content"))}, item_id=tid)

    async def _settle_tools(self, s: _Turn, status: str):
        for tid, done in list(s.tools.items()):
            if not done:
                s.tools[tid] = True
                await self._send(s, "item.completed", {"item_type": "tool", "status": status, "ok": False}, item_id=tid)

    # -- interrupt / lifecycle ------------------------------------------------------------------------------------------
    async def _stop(self, s: _Turn):
        s.queue.put_nowait(("cancel",))
        if s.task is not None and not s.task.done():
            done, _ = await asyncio.wait({s.task}, timeout=self.interrupt_grace_s + 10.0)
            if not done:
                s.task.cancel()
                self._issues.append(CleanupIssue(s.turn.turn_id, None, "Claude turn did not settle after interrupt; task cancelled."))
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
            if s.task is not None and not s.task.done():  # confirm process cleanup, bounded
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
            from .claude_runtime import ClaudeCodeRuntime
            self._health_rt = ClaudeCodeRuntime(self._cmd)
        return self._health_rt

    async def check_health(self) -> RuntimeHealth:
        return await self._runtime().check_health()

    async def list_available_models(self, free_only=True, *, refresh=False) -> list:
        return await self._runtime().list_available_models(free_only)
