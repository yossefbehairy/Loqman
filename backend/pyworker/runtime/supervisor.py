"""Owned stdlib subprocesses, hidden on Windows, with bounded concurrent drains.

Popen does not expose its initial thread handle. Assign immediately after spawn
rather than using private _winapi internals to resume a suspended child. There
is therefore a spawn/assignment descendant-creation race. Once assigned, a
kill-on-close Job Object owns descendants even after their parent exits and
when the worker dies. A failed assignment is reported and uses taskkill instead.
Existing runtime adapters do not use this supervisor until P5-P7.
"""

from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import threading
import time
import uuid

from .driver import CleanupIssue
from .process_io import MAX_PROCESS_OUTPUT_BYTES, RuntimeOutputLimit


class _WindowsJob:
    def __init__(self):
        import ctypes as c
        from ctypes import wintypes as w

        class Limits(c.Structure):
            _fields_ = [("process_time", c.c_int64), ("job_time", c.c_int64), ("flags", w.DWORD),
                ("min_ws", c.c_size_t), ("max_ws", c.c_size_t), ("active", w.DWORD),
                ("affinity", c.c_size_t), ("priority", w.DWORD), ("scheduling", w.DWORD)]

        class Counters(c.Structure):
            _fields_ = [(name, c.c_uint64) for name in ("read_ops", "write_ops", "other_ops", "read", "write", "other")]

        class Extended(c.Structure):
            _fields_ = [("basic", Limits), ("io", Counters), ("process_memory", c.c_size_t),
                ("job_memory", c.c_size_t), ("peak_process", c.c_size_t), ("peak_job", c.c_size_t)]

        class Accounting(c.Structure):
            _fields_ = [(name, c.c_int64) for name in ("user", "kernel", "period_user", "period_kernel")] + [
                (name, w.DWORD) for name in ("faults", "total_processes", "active_processes", "terminated")]

        self.c = c
        self.Accounting = Accounting
        self.k = c.WinDLL("kernel32", use_last_error=True)
        signatures = {
            "CreateJobObjectW": ([c.c_void_p, w.LPCWSTR], w.HANDLE),
            "SetInformationJobObject": ([w.HANDLE, c.c_int, c.c_void_p, w.DWORD], w.BOOL),
            "AssignProcessToJobObject": ([w.HANDLE, w.HANDLE], w.BOOL),
            "TerminateJobObject": ([w.HANDLE, w.UINT], w.BOOL),
            "QueryInformationJobObject": ([w.HANDLE, c.c_int, c.c_void_p, w.DWORD, c.c_void_p], w.BOOL),
            "OpenProcess": ([w.DWORD, w.BOOL, w.DWORD], w.HANDLE),
            "CloseHandle": ([w.HANDLE], w.BOOL),
        }
        for name, (args, result) in signatures.items():
            function = getattr(self.k, name)
            function.argtypes, function.restype = args, result
        self.handle = self.k.CreateJobObjectW(None, None)
        if not self.handle:
            raise c.WinError(c.get_last_error())
        info = Extended()
        info.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.k.SetInformationJobObject(self.handle, 9, c.byref(info), c.sizeof(info)):
            error = c.WinError(c.get_last_error())
            self.close()
            raise error

    def assign(self, pid):
        process = self.k.OpenProcess(0x0100 | 0x0001, False, pid)  # SET_QUOTA | TERMINATE
        if not process:
            raise self.c.WinError(self.c.get_last_error())
        try:
            if not self.k.AssignProcessToJobObject(self.handle, process):
                raise self.c.WinError(self.c.get_last_error())
        finally:
            self.k.CloseHandle(process)

    def close(self):
        if self.handle:
            if not self.k.CloseHandle(self.handle):
                raise self.c.WinError(self.c.get_last_error())
            self.handle = None

    def terminate_and_wait(self):
        if not self.k.TerminateJobObject(self.handle, 1):
            raise self.c.WinError(self.c.get_last_error())
        deadline = time.monotonic() + 5
        while True:
            info = self.Accounting()
            if not self.k.QueryInformationJobObject(self.handle, 1, self.c.byref(info), self.c.sizeof(info), None):
                raise self.c.WinError(self.c.get_last_error())
            if not info.active_processes:
                return
            if time.monotonic() >= deadline:
                raise TimeoutError("Job processes did not exit.")
            time.sleep(0.01)


class SupervisedProcess:
    def __init__(self, proc, job, max_bytes, issues):
        self.proc, self.job = proc, job
        self.generation = uuid.uuid4().hex
        self.issues = issues
        self.max_bytes, self._used = max_bytes, 0
        self._lock = threading.Lock()
        self._overflow = asyncio.Event()
        self._read_stop = threading.Event()
        self._limit_hit = threading.Event()
        self._loop = asyncio.get_running_loop()
        self._drains = [asyncio.create_task(asyncio.to_thread(self._read, stream))
                        for stream in (proc.stdout, proc.stderr)]
        self._closing = None

    @classmethod
    async def start(cls, argv, *, cwd=None, env=None, max_bytes=MAX_PROCESS_OUTPUT_BYTES):
        if not isinstance(argv, (list, tuple)) or not argv or not all(isinstance(a, str) for a in argv):
            raise ValueError("argv must be a nonempty array of strings")
        if max_bytes < 1:
            raise ValueError("max_bytes must be positive")
        job, issues = None, []
        if os.name == "nt":
            try:
                job = _WindowsJob()
            except OSError:
                issues.append("Job Object creation failed; taskkill fallback cannot guarantee worker-crash cleanup.")
        try:
            proc = subprocess.Popen(argv, cwd=cwd, env=env, shell=False,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                start_new_session=os.name != "nt")
        except BaseException:
            if job:
                job.close()
            raise
        if job:
            try:
                job.assign(proc.pid)
            except OSError:
                job.close()
                job = None
                issues.append("Job Object assignment failed; taskkill fallback cannot guarantee worker-crash cleanup.")
        return cls(proc, job, max_bytes, issues)

    @property
    def pid(self):
        return self.proc.pid

    def _read(self, stream):
        chunks = []
        try:
            peek = handle = available = None
            if os.name == "nt":
                import ctypes as c
                from ctypes import wintypes as w
                import msvcrt
                peek = c.WinDLL("kernel32", use_last_error=True).PeekNamedPipe
                peek.argtypes = [w.HANDLE, c.c_void_p, w.DWORD, c.c_void_p, c.POINTER(w.DWORD), c.c_void_p]
                peek.restype = w.BOOL
                handle = msvcrt.get_osfhandle(stream.fileno())
                available = w.DWORD()
            while True:
                count = 65536
                if peek is not None:
                    if not peek(handle, None, 0, None, c.byref(available), None):
                        error = c.get_last_error()
                        if error in (109, 232):  # broken/no-data pipe after writer exit
                            break
                        raise c.WinError(error)
                    if not available.value:
                        if self._read_stop.wait(0.01):
                            break
                        continue
                    count = min(count, available.value)
                chunk = stream.read1(count)
                if not chunk:
                    break
                with self._lock:
                    self._used += len(chunk)
                    retain = self._used <= self.max_bytes
                if retain:
                    chunks.append(chunk)
                else:
                    if not self._limit_hit.is_set():
                        self._limit_hit.set()
                        self._loop.call_soon_threadsafe(self._overflow.set)
            return b"".join(chunks)
        finally:
            stream.close()

    async def _kill(self):
        owned_job = self.job is not None
        if self.job is not None:
            try:
                try:
                    await asyncio.to_thread(self.job.terminate_and_wait)
                finally:
                    self.job.close()
                    self.job = None
            except (OSError, TimeoutError):
                self.issues.append("Job Object termination or closure could not be verified.")
        if os.name == "nt" and self.proc.poll() is None:
            try:
                killer = await asyncio.create_subprocess_exec("taskkill", "/PID", str(self.pid), "/T", "/F",
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NO_WINDOW)
                try:
                    status = await asyncio.wait_for(killer.wait(), 5)
                    if status and self.proc.poll() is None:
                        self.issues.append("taskkill failed to terminate the owned tree.")
                except asyncio.TimeoutError:
                    killer.kill()
                    await killer.wait()
                    self.issues.append("taskkill timed out.")
            except OSError:
                self.issues.append("taskkill could not launch.")
        elif os.name == "nt" and not owned_job:
            self.issues.append("Root already exited without a job; descendant exit cannot be verified.")
        elif os.name != "nt":
            try:
                os.killpg(self.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        if self.proc.poll() is None:
            self.proc.kill()
        try:
            await asyncio.to_thread(self.proc.wait, timeout=5)
        except subprocess.TimeoutExpired:
            self.issues.append("Owned root did not exit after termination.")

    async def _close(self):
        await self._kill()
        self._read_stop.set()
        if self.proc.stdin:
            await asyncio.to_thread(self.proc.stdin.close)
        # Readers drain to EOF after the owned tree exits. Never abandon to_thread readers.
        results = await asyncio.gather(*self._drains, return_exceptions=True)
        if any(isinstance(result, BaseException) for result in results):
            self.issues.append("A process pipe reader failed.")
        return tuple(CleanupIssue(self.generation, self.pid, issue) for issue in self.issues)

    async def close(self):
        if self._closing is None:
            self._closing = asyncio.create_task(self._close())
        cancelled = False
        while not self._closing.done():
            try:
                await asyncio.shield(self._closing)
            except asyncio.CancelledError:
                cancelled = True
        result = await self._closing
        if cancelled:
            raise asyncio.CancelledError
        return result

    async def communicate(self, data: bytes | None = None):
        async def write():
            try:
                if data:
                    await asyncio.to_thread(self.proc.stdin.write, data)
                    await asyncio.to_thread(self.proc.stdin.flush)
            except (BrokenPipeError, OSError, ValueError):
                pass
            finally:
                self.proc.stdin.close()

        writer = asyncio.create_task(write())
        exited = asyncio.create_task(asyncio.to_thread(self.proc.wait))
        overflow = asyncio.create_task(self._overflow.wait())
        try:
            await asyncio.wait((exited, overflow), return_when=asyncio.FIRST_COMPLETED)
            # Also close the job on natural root exit; descendants can still hold pipes.
            await self.close()
            await writer
            if self._overflow.is_set():
                raise RuntimeOutputLimit("Engine output exceeded the allowed byte limit.")
            return tuple(task.result() for task in self._drains)
        finally:
            cleaning = asyncio.create_task(self.close())
            cancelled_during_cleanup = False
            while not cleaning.done():
                try:
                    await asyncio.shield(cleaning)
                except asyncio.CancelledError:
                    cancelled_during_cleanup = True
                    continue
            await cleaning
            overflow.cancel()
            await asyncio.gather(writer, exited, overflow, return_exceptions=True)
            if cancelled_during_cleanup:
                raise asyncio.CancelledError
