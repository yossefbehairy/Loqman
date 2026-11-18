"""Generic Agent Client Protocol (ACP) JSON-RPC 2.0 connection over newline-delimited stdio.

This module owns framing and correlation only; it knows nothing about any engine. Engine dialects (launch arguments,
required modes, permission policy, error classification, session persistence) live in the drivers that use it.

* Framing: bytes are fed in by the supervisor's stream tee (``feed``; ``None`` is EOF) on the event-loop thread. Lines are
  bounded (``max_line_bytes``), the whole stream is bounded (``max_total_bytes``, ``max_messages``) and the parsed-message
  queue is bounded (``max_queue``): overflow fails the connection explicitly instead of dropping data silently.
* Tolerance: a line that is not JSON (startup banners, log lines, auth URLs) is *diagnostic noise*: it is counted, a short
  bounded tail is kept and ``on_diagnostic`` sees it, but it never reaches the protocol. A JSON object that is not a
  JSON-RPC 2.0 frame, an unknown response id or a malformed response is a protocol violation and fails the connection.
* Correlation: ``request`` allocates integer ids and keeps a bounded pending table (``max_pending``). Responses, server
  notifications and server requests are dispatched strictly in arrival order by ONE dispatcher task, so every update that
  precedes a response is handled before the awaiting caller resumes.
* Sessions: ``session/update`` notifications are routed by ``params.sessionId`` to the handler bound for that session;
  updates for unbound sessions and replay updates (``_meta.isReplay``) are dropped and counted.
* Writes: one lock serialises every outgoing frame (a bounded wait; a stuck pipe fails the connection).

Failures are exceptions stored in ``fatal``; every pending request is failed with it, so no caller waits forever.
"""

from __future__ import annotations

import asyncio
import json
from collections import deque
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional

MAX_LINE_BYTES = 1024 * 1024
MAX_TOTAL_BYTES = 16 * 1024 * 1024
MAX_MESSAGES = 50_000
MAX_PENDING = 16
MAX_QUEUE = 4096
MAX_DIAGNOSTIC_LINES = 2000
DIAGNOSTIC_TAIL_LINES = 20
DIAGNOSTIC_LINE_CHARS = 300
MAX_ID_CHARS = 200
MAX_TOMBSTONES = 64
WRITE_TIMEOUT_S = 10.0
PROTOCOL_VERSION = 1

CLIENT_CAPABILITIES = {"fs": {"readTextFile": False, "writeTextFile": False}, "terminal": False}


class AcpTransportError(Exception):
    """The connection itself failed. ``kind``: eof | protocol | write | pending | closed."""

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind, self.message = kind, message


class AcpTimeout(Exception):
    """A request got no response within its deadline (the connection stays usable: its id is tombstoned, so one late
    response for it is ignored instead of being a protocol violation)."""

    def __init__(self, method: str):
        super().__init__(method)
        self.method = method


class AcpError(Exception):
    """A JSON-RPC error response from the peer, with nested detail preserved and bounded."""

    def __init__(self, code: Any, message: Any, data: Any = None):
        self.code = code if isinstance(code, int) and not isinstance(code, bool) else 0
        self.message = str(message or "")[:500]
        self.data = data
        super().__init__(self.message)

    def hint(self) -> str:
        """Lower-cased bounded text built from the message and the usual nested places (``data.details``, ``data.message``,
        ``data.error.message``, ``data.errorName``). For classification only; never show it to callers."""
        parts = [self.message]
        data = self.data
        if isinstance(data, str):
            parts.append(data)
        elif isinstance(data, dict):
            for value in (data.get("details"), data.get("message"), data.get("errorName"), data.get("service")):
                if isinstance(value, str):
                    parts.append(value)
            inner = data.get("error")
            if isinstance(inner, dict):
                for value in (inner.get("message"), inner.get("details"), inner.get("status")):
                    if isinstance(value, str):
                        parts.append(value)
            elif isinstance(inner, str):
                parts.append(inner)
        return " ".join(parts)[:1500].lower()

    def data_keys(self) -> tuple:
        return tuple(self.data) if isinstance(self.data, dict) else ()


def _consume(fut: "asyncio.Future") -> None:
    if not fut.cancelled():
        fut.exception()


def is_rpc_id(value: Any) -> bool:
    return (type(value) is int) or (isinstance(value, str) and 0 < len(value) <= MAX_ID_CHARS)


def safe_id(value: Any) -> bool:
    return isinstance(value, str) and 0 < len(value) <= MAX_ID_CHARS


@dataclass(frozen=True)
class AgentCapabilities:
    """What the agent advertised in the ``initialize`` result (never assumed)."""
    load_session: bool = False
    resume: bool = False
    close: bool = False


def parse_capabilities(init: dict) -> AgentCapabilities:
    caps = init.get("agentCapabilities") if isinstance(init, dict) else None
    if not isinstance(caps, dict):
        return AgentCapabilities()
    sess = caps.get("sessionCapabilities") if isinstance(caps.get("sessionCapabilities"), dict) else {}

    def advertised(value: Any) -> bool:  # ACP spells a supported capability as an object, often empty
        return value is not None and value is not False

    return AgentCapabilities(load_session=caps.get("loadSession") is True,
                             resume=advertised(sess.get("resume")), close=advertised(sess.get("close")))


def flatten_options(options: Any) -> list:
    """ACP select options may be flat or grouped; return the flat list of value dicts."""
    out: list = []
    if not isinstance(options, list):
        return out
    for option in options:
        if not isinstance(option, dict):
            continue
        if isinstance(option.get("options"), list):
            out.extend(flatten_options(option["options"]))
        elif "value" in option:
            out.append(option)
    return out


def find_model_config(config_options: Any, model: str) -> Optional[str]:
    """Id of a select config option that is the model picker and offers ``model``."""
    if not isinstance(config_options, list):
        return None
    for option in config_options:
        if not isinstance(option, dict) or not safe_id(option.get("id")):
            continue
        if option.get("category") != "model" and option.get("id") != "model":
            continue
        if any(o.get("value") == model for o in flatten_options(option.get("options"))):
            return option["id"]
    return None


Handler = Callable[[dict, dict], Awaitable[None]]
# ("result"|"error", payload) or ("result"|"error", payload, after): ``after`` is a no-arg callable (sync or async) run
# AFTER the reply frame was written, so a slow or failing sink can never delay or skip the reply. Whatever it raises
# fails the connection (a driver that must not stop the reply swallows its own sink errors inside ``after``).
RequestHandler = Callable[[str, Any, Any], Awaitable[tuple]]


class AcpConnection:
    def __init__(self, *, max_line_bytes: int = MAX_LINE_BYTES, max_total_bytes: int = MAX_TOTAL_BYTES,
                 max_messages: int = MAX_MESSAGES, max_pending: int = MAX_PENDING, max_queue: int = MAX_QUEUE,
                 max_diagnostic_lines: int = MAX_DIAGNOSTIC_LINES, write_timeout: float = WRITE_TIMEOUT_S,
                 on_request: Optional[RequestHandler] = None,
                 on_notification: Optional[Callable[[str, Any], Awaitable[None]]] = None,
                 on_diagnostic: Optional[Callable[[str], None]] = None):
        self.max_line_bytes, self.max_total_bytes, self.max_messages = max_line_bytes, max_total_bytes, max_messages
        self.max_pending, self.max_diagnostic_lines, self.write_timeout = max_pending, max_diagnostic_lines, write_timeout
        self.on_request, self.on_notification, self.on_diagnostic = on_request, on_notification, on_diagnostic
        self.fatal: Optional[BaseException] = None
        self.diagnostics: deque = deque(maxlen=DIAGNOSTIC_TAIL_LINES)
        self.diagnostic_count = 0
        self.replay_dropped = 0
        self.unrouted_dropped = 0
        self._buf = bytearray()
        self._skipping = False
        self._total = 0
        self._count = 0
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=max_queue)
        self._pending: dict = {}
        self._hooks: dict = {}
        self._tombstones: dict = {}
        self._next_id = 0
        self._sessions: dict = {}
        self._write_fn: Optional[Callable[[bytes], Awaitable[None]]] = None
        self._wlock = asyncio.Lock()
        self._task: Optional[asyncio.Task] = None
        self._eof = False

    # -- lifecycle ----------------------------------------------------------------------------------------------------
    def start(self, write_fn: Callable[[bytes], Awaitable[None]]) -> None:
        self._write_fn = write_fn
        self._task = asyncio.create_task(self._dispatch_loop(), name="acp-dispatch")

    async def close(self) -> None:
        """Stop dispatching and fail anything still pending. Idempotent; never raises."""
        self.fail(AcpTransportError("closed", "ACP connection closed."))
        task, self._task = self._task, None
        if task is not None and task is not asyncio.current_task():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    def fail(self, exc: BaseException) -> None:
        if self.fatal is None:
            self.fatal = exc
        for fut in list(self._pending.values()):
            if not fut.done():
                fut.set_exception(exc)
        self._pending.clear()
        self._hooks.clear()
        self._buf.clear()
        try:
            self._queue.put_nowait(("stop", None))  # wake the dispatcher so it can exit
        except asyncio.QueueFull:
            pass

    # -- inbound framing ----------------------------------------------------------------------------------------------
    def feed(self, data: Optional[bytes]) -> None:
        """Event-loop thread only. ``None`` = end of stream."""
        if self.fatal is not None:
            return
        if data is None:
            if self._eof:
                return
            self._eof = True
            if self._buf.strip() and not self._skipping:
                self._ingest(bytes(self._buf))
            self._buf.clear()
            if self.fatal is None:
                self._put(("eof", None))
            return
        self._total += len(data)
        if self._total > self.max_total_bytes:
            self.fail(AcpTransportError("protocol", "output budget exceeded"))
            return
        self._buf += data
        while self.fatal is None:
            nl = self._buf.find(b"\n")
            if nl < 0:
                break
            line = bytes(self._buf[:nl])
            del self._buf[:nl + 1]
            if self._skipping:  # tail of an oversized diagnostic line
                self._skipping = False
                continue
            self._ingest(line)
        if self.fatal is None and len(self._buf) > self.max_line_bytes:
            if self._buf[:1] == b"{":
                self.fail(AcpTransportError("protocol", "oversize line"))
            else:
                self._note_diagnostic(bytes(self._buf[:DIAGNOSTIC_LINE_CHARS]).decode("utf-8", "replace"))
                self._buf.clear()
                self._skipping = True

    def _put(self, item: tuple) -> None:
        try:
            self._queue.put_nowait(item)
        except asyncio.QueueFull:
            self.fail(AcpTransportError("protocol", "message queue overflow"))

    def _note_diagnostic(self, text: str) -> None:
        self.diagnostic_count += 1
        text = text.strip()[:DIAGNOSTIC_LINE_CHARS]
        if text:
            self.diagnostics.append(text)
            if self.on_diagnostic is not None:
                try:
                    self.on_diagnostic(text)
                except Exception:
                    pass
        if self.diagnostic_count > self.max_diagnostic_lines:
            self.fail(AcpTransportError("protocol", "too much non-protocol output"))

    def _ingest(self, raw: bytes) -> None:
        raw = raw.strip()
        if not raw:
            return
        try:
            message = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            self._note_diagnostic(raw.decode("utf-8", "replace"))
            return
        if not isinstance(message, dict):
            self._note_diagnostic(raw.decode("utf-8", "replace"))
            return
        if message.get("jsonrpc") != "2.0":
            self.fail(AcpTransportError("protocol", "not a json-rpc 2.0 object"))
            return
        self._count += 1
        if self._count > self.max_messages:
            self.fail(AcpTransportError("protocol", "output budget exceeded"))
            return
        self._put(("msg", message))

    # -- dispatch -----------------------------------------------------------------------------------------------------
    async def _dispatch_loop(self) -> None:
        try:
            while self.fatal is None:
                kind, payload = await self._queue.get()
                if kind == "stop":
                    return
                if kind == "eof":
                    self.fail(AcpTransportError("eof", "agent closed the stream"))
                    return
                await self._handle(payload)
        except asyncio.CancelledError:
            raise
        except BaseException as exc:  # a handler's failure (including a driver's DriverTurnError) fails the connection
            self.fail(exc)

    async def _handle(self, msg: dict) -> None:
        method = msg.get("method")
        if "method" in msg:
            if not isinstance(method, str) or not method:
                raise AcpTransportError("protocol", "bad method")
            params = msg.get("params")
            if "id" in msg:
                rid = msg["id"]
                if not is_rpc_id(rid):
                    raise AcpTransportError("protocol", "bad request id")
                if self.on_request is None:
                    outcome = ("error", {"code": -32601, "message": "Method not supported by client"})
                else:
                    outcome = await self.on_request(method, params, rid)
                frame: dict = {"jsonrpc": "2.0", "id": rid}
                frame["error" if outcome[0] == "error" else "result"] = outcome[1]
                await self._write(frame)
                after = outcome[2] if len(outcome) > 2 else None
                if after is not None:
                    done = after()
                    if asyncio.iscoroutine(done):
                        await done
            elif method == "session/update":
                await self._route_update(params)
            elif self.on_notification is not None:
                await self.on_notification(method, params)
            return
        rid = msg.get("id")
        fut = self._pending.pop(rid, None) if type(rid) is int else None
        if fut is None:
            if type(rid) is int and rid in self._tombstones:
                del self._tombstones[rid]  # the late answer to a request that timed out: ignored exactly once
                return
            raise AcpTransportError("protocol", "unexpected response id")
        hook = self._hooks.pop(rid, None)
        if hook is not None:
            hook()  # synchronous, before the awaiting caller can run: later frames are "after the response"
        if "error" in msg:
            err = msg["error"]
            err = err if isinstance(err, dict) else {}
            if not fut.done():
                fut.set_exception(AcpError(err.get("code"), err.get("message"), err.get("data")))
            return
        if "result" not in msg:
            raise AcpTransportError("protocol", "response without result")
        result = msg["result"]
        if result is not None and not isinstance(result, dict):
            raise AcpTransportError("protocol", "non-object result")
        if not fut.done():
            fut.set_result(result if result is not None else {})

    async def _route_update(self, params: Any) -> None:
        if not isinstance(params, dict):
            return
        update = params.get("update")
        meta = params.get("_meta") if isinstance(params.get("_meta"), dict) else {}
        umeta = update.get("_meta") if isinstance(update, dict) and isinstance(update.get("_meta"), dict) else {}
        if meta.get("isReplay") is True or umeta.get("isReplay") is True:
            self.replay_dropped += 1
            return
        handler = self._sessions.get(params.get("sessionId"))
        if handler is None or not isinstance(update, dict):
            self.unrouted_dropped += 1
            return
        await handler(update, params)

    def bind_session(self, session_id: str, handler: Handler) -> None:
        self._sessions[session_id] = handler

    def unbind_session(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)

    # -- outbound -----------------------------------------------------------------------------------------------------
    async def _write(self, obj: dict) -> None:
        if self.fatal is not None:
            raise self.fatal
        data = (json.dumps(obj, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")
        async with self._wlock:
            if self.fatal is not None:
                raise self.fatal
            try:
                await asyncio.wait_for(self._write_fn(data), self.write_timeout)
            except (OSError, ValueError, ConnectionError, RuntimeError, asyncio.TimeoutError):
                err = AcpTransportError("write", "agent connection closed")
                self.fail(err)
                raise err

    async def notify(self, method: str, params: dict) -> None:
        await self._write({"jsonrpc": "2.0", "method": method, "params": params})

    async def send_request(self, method: str, params: dict, *, before_write: Optional[Callable[[], None]] = None,
                           on_response: Optional[Callable[[], None]] = None):
        """Write a request and return its response future (the caller awaits it with its own deadline).

        ``before_write`` runs under the write lock immediately before the frame is written; it may raise to abort the
        write (used so a cancelled turn can never submit its prompt after the stop decision)."""
        if self.fatal is not None:
            raise self.fatal
        if len(self._pending) >= self.max_pending:
            raise AcpTransportError("pending", "too many outstanding requests")
        self._next_id += 1
        rid = self._next_id
        fut = asyncio.get_running_loop().create_future()
        fut.add_done_callback(_consume)
        self._pending[rid] = fut
        if on_response is not None:
            self._hooks[rid] = on_response
        frame = {"jsonrpc": "2.0", "id": rid, "method": method, "params": params}
        try:
            if before_write is None:
                await self._write(frame)
            else:
                data = (json.dumps(frame, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")
                async with self._wlock:
                    if self.fatal is not None:
                        raise self.fatal
                    before_write()
                    try:
                        await asyncio.wait_for(self._write_fn(data), self.write_timeout)
                    except (OSError, ValueError, ConnectionError, RuntimeError, asyncio.TimeoutError):
                        err = AcpTransportError("write", "agent connection closed")
                        self.fail(err)
                        raise err
        except BaseException:
            self._pending.pop(rid, None)
            self._hooks.pop(rid, None)
            raise
        return rid, fut

    async def request(self, method: str, params: dict, timeout: Optional[float] = None, *,
                      before_write: Optional[Callable[[], None]] = None,
                      on_response: Optional[Callable[[], None]] = None) -> dict:
        rid, fut = await self.send_request(method, params, before_write=before_write, on_response=on_response)
        try:
            return await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            self._pending.pop(rid, None)
            self._hooks.pop(rid, None)
            self._tombstones[rid] = None
            while len(self._tombstones) > MAX_TOMBSTONES:
                del self._tombstones[next(iter(self._tombstones))]
            raise AcpTimeout(method)
        finally:
            if not fut.done():
                self._pending.pop(rid, None)
                self._hooks.pop(rid, None)

    # -- generic ACP operations (no engine rules) ---------------------------------------------------------------------
    async def initialize(self, timeout: Optional[float] = None, *, protocol_version: int = PROTOCOL_VERSION,
                         client_info: Optional[dict] = None) -> dict:
        return await self.request("initialize", {
            "protocolVersion": protocol_version,
            "clientInfo": client_info or {"name": "agentos", "title": "AgentOS", "version": "0"},
            "clientCapabilities": CLIENT_CAPABILITIES}, timeout)

    async def new_session(self, cwd: str, timeout: Optional[float] = None) -> dict:
        return await self.request("session/new", {"cwd": cwd, "mcpServers": []}, timeout)

    async def reopen_session(self, method: str, session_id: str, cwd: str, timeout: Optional[float] = None) -> dict:
        """``method`` is ``session/resume`` or ``session/load`` (the dialect decides which)."""
        if method not in ("session/resume", "session/load"):
            raise ValueError("unsupported reopen method")
        return await self.request(method, {"sessionId": session_id, "cwd": cwd, "mcpServers": []}, timeout)

    async def cancel_session(self, session_id: str) -> None:
        await self.notify("session/cancel", {"sessionId": session_id})
