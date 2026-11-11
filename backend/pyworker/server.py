"""Server loop: one persistent event loop, concurrent request handling.

RPC lifetime vs run lifetime
----------------------------
A request is "finished" when its response is written. `run.start` returns
right after the run is created; the run lives on in its own task and is
observed through events / run.status / run.events. Cancelling or finishing a
request never touches a run; only run.cancel, shutdown, EOF or a crash do.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os

from . import PROTOCOL, WORKER_VERSION
from .app import Application
from .appsettings import load_settings
from .config import Settings
from .errors import (
    DUPLICATE_REQUEST_ID,
    INTERNAL_ERROR,
    MESSAGE_TOO_LARGE,
    OVERLOADED,
    SHUTTING_DOWN,
    WorkerError,
)
from .protocol import parse_request, response_error, response_ok
from .stdio import EOF, LINE, OVERSIZE, StdioTransport
from .streams import StreamRegistry

log = logging.getLogger("pyworker.server")


def fatal_line(code: str, message: str) -> dict:
    return {"type": "fatal", "error": {"code": code, "message": message}}


class Server:
    def __init__(self, settings: Settings, transport: StdioTransport):
        self.settings = settings
        self.transport = transport
        self._stop = asyncio.Event()
        self._inflight_ids: set = set()
        self._tasks: set[asyncio.Task] = set()
        self._app: Application | None = None

    def request_stop(self) -> None:
        self._stop.set()

    # -- bootstrap ------------------------------------------------------
    def bootstrap(self) -> Application:
        """Open the DB, upgrade/recover, wire the application and its streams."""
        app_settings = load_settings(
            AGENTOS_DATA_DIR=self.settings.data_dir, RUNTIME_MODE=self.settings.runtime_mode)
        self._app = Application.create(settings=app_settings, db_path=self.settings.db_path)
        self._app.ctx.streams = StreamRegistry(self._app.ctx, self.transport.send)
        return self._app

    # -- main loop ------------------------------------------------------
    async def serve(self) -> int:
        assert self._app is not None
        self.transport.start(on_broken=self.request_stop)
        await self.transport.send(
            {
                "type": "ready",
                "protocol": PROTOCOL,
                "worker_version": WORKER_VERSION,
                "pid": os.getpid(),
                "max_message_bytes": self.settings.max_message_bytes,
                "runtime_mode": self._app.ctx.settings.RUNTIME_MODE,
                "methods": len(self._app.method_names()),
                "recovery": self._app.recovery,
                "interrupted_runs": self._app.interrupted_runs,
            }
        )
        stop_wait = asyncio.ensure_future(self._stop.wait())
        try:
            while not self._stop.is_set():
                read = asyncio.ensure_future(self.transport.read())
                done, _ = await asyncio.wait({read, stop_wait}, return_when=asyncio.FIRST_COMPLETED)
                if read not in done:
                    read.cancel()
                    break
                kind, payload = read.result()
                if kind == EOF:
                    log.info("stdin closed (EOF): shutting down")
                    break
                if kind == OVERSIZE:
                    await self.transport.send(
                        response_error(
                            None,
                            WorkerError(
                                MESSAGE_TOO_LARGE,
                                f"Message exceeds {self.settings.max_message_bytes} bytes.",
                            ),
                        )
                    )
                    continue
                await self._accept(payload)
        finally:
            stop_wait.cancel()
        return await self._shutdown()

    async def _accept(self, raw: bytes) -> None:
        try:
            req = parse_request(raw)
        except WorkerError as err:
            await self.transport.send(response_error(err.request_id, err))
            return
        if req.id in self._inflight_ids:
            await self.transport.send(
                response_error(req.id, WorkerError(DUPLICATE_REQUEST_ID, "Request id is already in flight."))
            )
            return
        if len(self._inflight_ids) >= self.settings.max_inflight_requests:
            await self.transport.send(response_error(req.id, WorkerError(OVERLOADED, "Too many requests in flight.")))
            return
        self._inflight_ids.add(req.id)
        task = asyncio.get_running_loop().create_task(self._handle(req))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _handle(self, req) -> None:
        try:
            if req.method == "shutdown":
                result = {"status": "shutting_down"}
                await self.transport.send(response_ok(req.id, result))
                self.request_stop()
                return
            msg = response_ok(req.id, await self._app.handle(req.method, req.params))
        except WorkerError as err:
            msg = response_error(req.id, err)
        except asyncio.CancelledError:
            raise
        except Exception:
            # Full detail to stderr only; the client sees a stable message.
            log.exception("unhandled error in %s", req.method)
            msg = response_error(req.id, WorkerError(INTERNAL_ERROR, "Internal error."))
        finally:
            self._inflight_ids.discard(req.id)
        await self.transport.send(msg)

    async def _shutdown(self) -> int:
        timeout = self.settings.shutdown_timeout_s
        deadline = asyncio.get_running_loop().time() + timeout
        left = await self._app.shutdown(timeout)
        if left:
            log.warning("%d background task(s) still running at shutdown deadline", left)
        pending = [t for t in self._tasks if not t.done()]
        if pending:
            remaining = max(0.0, deadline - asyncio.get_running_loop().time())
            _d, still = await asyncio.wait(pending, timeout=remaining)
            for t in still:
                t.cancel()
        await self.transport.close(max(0.5, deadline - asyncio.get_running_loop().time()))
        return 0


async def run(settings: Settings, stdin, stdout) -> int:
    transport = StdioTransport(
        stdin, stdout, settings.max_message_bytes, settings.inbox_size, settings.outbox_size
    )
    server = Server(settings, transport)
    try:
        server.bootstrap()
    except Exception as exc:
        log.exception("startup failed")
        transport._stdout.write(
            (json.dumps(fatal_line("startup_failed", _startup_message(exc))) + "\n").encode("ascii"))
        transport._stdout.flush()
        return 2
    return await server.serve()


def _startup_message(exc: Exception) -> str:
    """Client-safe reason for a failed start (no paths, no tracebacks)."""
    import sqlite3

    if exc.__class__.__name__ in ("SchemaTooNew", "ForeignDatabase"):
        return str(exc)[:200]
    if isinstance(exc, sqlite3.DatabaseError):
        return "The database could not be opened or upgraded."
    if isinstance(exc, (OSError, ValueError)):
        return "The data directory or settings are invalid."
    return "Startup failed."
