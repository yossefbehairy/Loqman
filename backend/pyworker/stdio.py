"""stdin/stdout transport.

* One reader thread (blocking readline is unusable on Windows asyncio) feeds a
  BOUNDED asyncio queue; when full the thread blocks, so the OS pipe gives the
  parent natural backpressure.
* Size limit is enforced on the raw bytes BEFORE json decoding; an oversized
  line is drained and reported, never buffered whole.
* One writer task owns stdout. Responses await a bounded queue (never dropped);
  live events are best-effort (dropped when full — they are persisted and can
  be replayed with run.events).
"""

from __future__ import annotations

import asyncio
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import BinaryIO, Callable

from .protocol import encode

log = logging.getLogger("pyworker.stdio")

LINE = "line"
OVERSIZE = "oversize"
EOF = "eof"


class StdioTransport:
    def __init__(
        self,
        stdin: BinaryIO,
        stdout: BinaryIO,
        max_message_bytes: int,
        inbox_size: int,
        outbox_size: int,
    ):
        self._stdin = stdin
        self._stdout = stdout
        self._max = max_message_bytes
        self._inbox_size = inbox_size
        self._outbox_size = outbox_size
        self._loop: asyncio.AbstractEventLoop | None = None
        self._inbox: asyncio.Queue | None = None
        self._outbox: asyncio.Queue | None = None
        self._writer_task: asyncio.Task | None = None
        self._write_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="pyworker-stdout")
        self._broken = False
        self._on_broken: Callable[[], None] | None = None
        self.dropped_events = 0

    # -- lifecycle ------------------------------------------------------
    def start(self, on_broken: Callable[[], None]) -> None:
        self._loop = asyncio.get_running_loop()
        self._inbox = asyncio.Queue(maxsize=self._inbox_size)
        self._outbox = asyncio.Queue(maxsize=self._outbox_size)
        self._on_broken = on_broken
        self._writer_task = self._loop.create_task(self._writer(), name="stdout-writer")
        threading.Thread(target=self._reader, name="pyworker-stdin", daemon=True).start()

    async def close(self, timeout: float) -> None:
        """Flush queued output (bounded by timeout), then stop the writer."""
        if self._writer_task is None:
            return
        try:
            await asyncio.wait_for(self._outbox.put(None), timeout)
            await asyncio.wait_for(self._writer_task, timeout)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            self._writer_task.cancel()
        self._write_pool.shutdown(wait=False, cancel_futures=True)

    # -- inbound --------------------------------------------------------
    def _reader(self) -> None:
        """Runs in a daemon thread. Never touches asyncio objects directly."""
        limit = self._max
        try:
            while True:
                chunk = self._stdin.readline(limit + 2)
                if not chunk:
                    self._post((EOF, None))
                    return
                if chunk.endswith(b"\n"):
                    payload = chunk[:-1]
                    if payload.endswith(b"\r"):
                        payload = payload[:-1]
                    item = (LINE, payload) if len(payload) <= limit else (OVERSIZE, None)
                elif len(chunk) >= limit + 2:
                    # No newline within the limit: drain the rest of the line.
                    while True:
                        rest = self._stdin.readline(65536)
                        if not rest or rest.endswith(b"\n"):
                            break
                    item = (OVERSIZE, None)
                else:
                    # Final line without trailing newline, then EOF.
                    item = (LINE, chunk) if len(chunk) <= limit else (OVERSIZE, None)
                if not self._post(item):
                    return
        except Exception:  # pragma: no cover - defensive
            log.exception("stdin reader failed")
            self._post((EOF, None))

    def _post(self, item) -> bool:
        try:
            asyncio.run_coroutine_threadsafe(self._inbox.put(item), self._loop).result()
            return True
        except Exception:
            return False  # loop closed: process is exiting

    async def read(self):
        return await self._inbox.get()

    # -- outbound -------------------------------------------------------
    async def send(self, message: dict) -> None:
        """Reliable send (responses, ready). Applies backpressure."""
        if self._broken:
            return
        await self._outbox.put(encode(message))

    def send_event_nowait(self, message: dict) -> None:
        """Best-effort send for live events; persisted copy is authoritative."""
        if self._broken:
            return
        try:
            self._outbox.put_nowait(encode(message))
        except asyncio.QueueFull:
            self.dropped_events += 1

    def _write(self, data: bytes) -> None:
        self._stdout.write(data)
        self._stdout.flush()

    async def _writer(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            data = await self._outbox.get()
            if data is None:
                return
            if self._broken:
                continue
            try:
                await loop.run_in_executor(self._write_pool, self._write, data)
            except (OSError, ValueError):
                # Parent closed our stdout: nobody is listening any more.
                self._broken = True
                log.warning("stdout closed by parent; shutting down")
                if self._on_broken:
                    self._on_broken()
