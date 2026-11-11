"""Standalone harness that runs the Python worker as a REAL process.

Plays the role Electron will play: owns the child process and speaks JSON
Lines over its stdin/stdout. No Electron, no React, no HTTP, and always a
temporary database. Stderr (logs) is captured to a file for assertions.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

BANNED = (
    "fastapi",
    "starlette",
    "uvicorn",
    "pydantic",
    "pydantic_core",
    "pydantic_settings",
    "sqlalchemy",
    "httpx",
    "sse_starlette",
    "anyio",
    "backend.app",
)

# Launches the real entry point, but every banned import raises (and is
# reported on stderr even if the worker swallows the ImportError).
_BLOCKING_BOOT = """
import sys, runpy
BANNED = %r
class _Block:
    def find_spec(self, name, path=None, target=None):
        for b in BANNED:
            if name == b or name.startswith(b + "."):
                sys.stderr.write("BLOCKED-IMPORT " + name + "\\n"); sys.stderr.flush()
                raise ImportError("blocked import: " + name)
        return None
sys.meta_path.insert(0, _Block())
runpy.run_module("backend.pyworker", run_name="__main__", alter_sys=True)
""" % (BANNED,)


def _quiet_close(stream) -> None:
    try:
        stream.close()
    except (OSError, ValueError):
        pass


class Worker:
    def __init__(self, tmp_path: Path, db_path: Path | None = None, args=(), block_imports=False):
        self.tmp_path = tmp_path
        self.db_path = db_path if db_path is not None else (tmp_path / "worker.db")  # False = pass no --db
        self.args = list(args)
        self.block_imports = block_imports
        self.stderr_path = tmp_path / f"worker-{len(list(tmp_path.glob('worker-*.err')))}.err"
        self.proc: subprocess.Popen | None = None
        self.ready: dict | None = None
        self.fatal: dict | None = None
        self.responses: dict = {}
        self.error_responses_null_id: list[dict] = []
        self.stream_msgs: list[dict] = []
        self.stream_ends: dict = {}
        self.bad_lines: list[bytes] = []
        self._cond = threading.Condition()
        self._next_id = 0
        self._reader: threading.Thread | None = None

    # -- process --------------------------------------------------------
    def start(self, wait_ready: bool = True) -> "Worker":
        base = ["--db", str(self.db_path)] if self.db_path is not False else []
        mode = [] if "--runtime-mode" in self.args else ["--runtime-mode", "mock"]
        if self.block_imports:
            cmd = [sys.executable, "-c", _BLOCKING_BOOT, *base, *mode, *self.args]
        else:
            cmd = [sys.executable, "-m", "backend.pyworker", *base, *mode, *self.args]
        self._stderr_file = open(self.stderr_path, "wb")
        self.proc = subprocess.Popen(
            cmd,
            cwd=str(ROOT),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=self._stderr_file,
        )
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()
        if wait_ready:
            self.wait_ready()
        return self

    def _read_loop(self) -> None:
        try:
            self._read_lines()
        except (OSError, ValueError):
            pass  # the test closed our read end on purpose

    def _read_lines(self) -> None:
        for line in self.proc.stdout:
            line = line.rstrip(b"\r\n")
            try:
                msg = json.loads(line)
                if not isinstance(msg, dict) or "type" not in msg:
                    raise ValueError
            except ValueError:
                with self._cond:
                    self.bad_lines.append(line)
                    self._cond.notify_all()
                continue
            with self._cond:
                kind = msg["type"]
                if kind == "ready":
                    self.ready = msg
                elif kind == "fatal":
                    self.fatal = msg
                elif kind == "stream":
                    self.stream_msgs.append(msg)
                elif kind == "stream_end":
                    self.stream_ends[msg["stream_id"]] = msg
                elif kind == "response":
                    if msg["id"] is None:
                        self.error_responses_null_id.append(msg)
                    else:
                        assert msg["id"] not in self.responses, f"duplicate response for {msg['id']}"
                        self.responses[msg["id"]] = msg
                self._cond.notify_all()

    def _wait_for(self, predicate, timeout: float, what: str):
        deadline = time.monotonic() + timeout
        with self._cond:
            while True:
                value = predicate()
                if value:
                    return value
                left = deadline - time.monotonic()
                if left <= 0:
                    raise AssertionError(f"timeout waiting for {what}\nstderr:\n{self.stderr_text()}")
                self._cond.wait(left)

    def wait_ready(self, timeout: float = 15.0) -> dict:
        return self._wait_for(lambda: self.ready or self.fatal, timeout, "ready")

    def stderr_text(self) -> str:
        try:
            return self.stderr_path.read_text(errors="replace")
        except OSError:
            return ""

    # -- sending --------------------------------------------------------
    def send_raw(self, data: bytes) -> None:
        self.proc.stdin.write(data)
        self.proc.stdin.flush()

    def send(self, obj: dict) -> None:
        self.send_raw(json.dumps(obj).encode() + b"\n")

    def new_id(self) -> str:
        self._next_id += 1
        return f"t{self._next_id}"

    def request(self, method: str, params: dict | None = None, timeout: float = 10.0, rid=None) -> dict:
        rid = rid if rid is not None else self.new_id()
        msg = {"id": rid, "method": method}
        if params is not None:
            msg["params"] = params
        self.send(msg)
        return self.wait_response(rid, timeout)

    def wait_response(self, rid, timeout: float = 10.0) -> dict:
        return self._wait_for(lambda: self.responses.get(rid), timeout, f"response {rid!r}")

    def ok(self, method: str, params: dict | None = None, **kw) -> dict:
        resp = self.request(method, params, **kw)
        assert "result" in resp, f"{method} failed: {resp}"
        return resp["result"]

    def err(self, method: str, params: dict | None = None, **kw) -> dict:
        resp = self.request(method, params, **kw)
        assert "error" in resp, f"{method} unexpectedly succeeded: {resp}"
        return resp["error"]

    def wait_null_error(self, count: int = 1, timeout: float = 10.0) -> dict:
        return self._wait_for(
            lambda: self.error_responses_null_id[count - 1] if len(self.error_responses_null_id) >= count else None,
            timeout,
            "null-id error response",
        )["error"]

    # -- run helpers ----------------------------------------------------
    def new_conversation(self, title: str = "t") -> str:
        return self.ok("conversations.create", {"title": title})["id"]

    def start_run(self, text: str = "hello", delay_ms: int = 0, late_ms: int | None = None,
                  conversation: str | None = None) -> str:
        """Start a mock-engine turn. `delay_ms`/`late_ms` use the mock's explicit test hooks."""
        prompt = text + (f" [mock:delay={delay_ms}]" if delay_ms else "")
        if late_ms is not None:
            prompt += f" [mock:late={late_ms}]"
        return self.ok("runs.start_turn", {
            "conversation_id": conversation or self.new_conversation(), "engine": "mock",
            "model_id": "deepseek-v4.1-flash-free", "prompt": prompt})["id"]

    def run_events(self, run_id: str, after_id: int = 0) -> list[dict]:
        return self.ok("runs.events", {"run_id": run_id, "after_id": after_id})["events"]

    def wait_status(self, run_id: str, status: str, timeout: float = 10.0) -> dict:
        deadline = time.monotonic() + timeout
        while True:
            run = self.ok("runs.get", {"run_id": run_id})
            if run["status"] == status:
                return run
            if time.monotonic() > deadline:
                raise AssertionError(f"run {run_id} stuck in {run['status']}, wanted {status}")
            time.sleep(0.02)

    def open_stream(self, topic: str, key: str, last_event_id: int = 0) -> str:
        params = {"topic": topic, "id": key}
        if last_event_id:
            params["last_event_id"] = last_event_id
        return self.ok("stream.open", params)["stream_id"]

    def stream_events(self, stream_id: str) -> list[dict]:
        with self._cond:
            return [m for m in self.stream_msgs if m["stream_id"] == stream_id]

    def wait_stream_end(self, stream_id: str, timeout: float = 10.0) -> dict:
        return self._wait_for(lambda: self.stream_ends.get(stream_id), timeout, f"stream_end {stream_id}")

    def wait_stream_event(self, stream_id: str, event: str, timeout: float = 10.0) -> dict:
        return self._wait_for(
            lambda: next((m for m in self.stream_msgs if m["stream_id"] == stream_id and m["event"] == event), None),
            timeout, f"stream event {event}")

    # -- shutdown -------------------------------------------------------
    def close_stdin(self) -> None:
        try:
            self.proc.stdin.close()
        except OSError:
            pass

    def wait_exit(self, timeout: float = 10.0) -> int:
        try:
            return self.proc.wait(timeout)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            raise AssertionError(f"worker did not exit within {timeout}s\nstderr:\n{self.stderr_text()}") from None

    def kill(self) -> None:
        self.proc.kill()
        self.proc.wait(10)

    def cleanup(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait(10)
        if self.proc is not None:
            for stream in (self.proc.stdin, self.proc.stdout):
                # close() can block on a buffer lock held by the reader thread: never wait for it.
                closer = threading.Thread(target=lambda s=stream: _quiet_close(s), daemon=True)
                closer.start()
                closer.join(2)
        try:
            self._stderr_file.close()
        except Exception:
            pass


@pytest.fixture
def worker_factory(tmp_path):
    made: list[Worker] = []

    def make(db_path=None, args=(), block_imports=False, start=True) -> Worker:
        w = Worker(tmp_path, db_path=db_path, args=args, block_imports=block_imports)
        made.append(w)
        if start:
            w.start()
        return w

    yield make
    for w in made:
        w.cleanup()
