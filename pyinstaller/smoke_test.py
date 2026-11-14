"""Exercise the frozen executable using bounded JSON Lines RPCs."""
import json
import ctypes
from ctypes import wintypes
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path


def descendants(pid):
    """Snapshot Windows child PIDs, including the one-file bootloader child."""
    class Entry(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("usage", wintypes.DWORD),
                    ("pid", wintypes.DWORD), ("heap", ctypes.c_size_t),
                    ("module", wintypes.DWORD), ("threads", wintypes.DWORD),
                    ("parent", wintypes.DWORD), ("priority", ctypes.c_long),
                    ("flags", wintypes.DWORD), ("name", wintypes.WCHAR * 260)]
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Entry)]
    kernel.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Entry)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.CreateToolhelp32Snapshot(2, 0)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    pairs = []
    try:
        entry = Entry()
        entry.size = ctypes.sizeof(entry)
        ok = kernel.Process32FirstW(handle, ctypes.byref(entry))
        if not ok:
            raise ctypes.WinError(ctypes.get_last_error())
        while ok:
            pairs.append((entry.pid, entry.parent))
            ok = kernel.Process32NextW(handle, ctypes.byref(entry))
    finally:
        kernel.CloseHandle(handle)
    found = {pid}
    while True:
        extended = found | {child for child, parent in pairs if parent in found}
        if extended == found:
            return found - {pid}
        found = extended


def main(exe, timeout):
    data = Path(tempfile.mkdtemp(prefix="agentos-smoke-"))
    print(f"Data directory (retained): {data}", flush=True)
    env = os.environ.copy()
    # Discovery must not launch any installed engine CLI during this gate.
    env["PATH"] = ""
    lines = queue.Queue()
    errors = []
    proc = None
    stderr_thread = None
    try:
        proc = subprocess.Popen([exe, "--data-dir", str(data), "--runtime-mode", "mock"],
                                cwd=str(data), env=env, stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))

        def read_stdout():
            for line in proc.stdout:
                lines.put(line)
            lines.put(None)

        def read_stderr():
            for chunk in iter(lambda: proc.stderr.read(4096), b""):
                errors.append(chunk)

        threading.Thread(target=read_stdout, daemon=True).start()
        stderr_thread = threading.Thread(target=read_stderr, daemon=True)
        stderr_thread.start()

        def receive():
            try:
                line = lines.get(timeout=timeout)
            except queue.Empty:
                raise RuntimeError("timed out waiting for worker output") from None
            if line is None:
                raise RuntimeError(f"worker closed stdout (exit {proc.poll()})")
            msg = json.loads(line)
            if not isinstance(msg, dict):
                raise RuntimeError(f"invalid protocol frame: {msg!r}")
            return msg

        ready = receive()
        if ready.get("type") != "ready" or ready.get("protocol") != "agentos-worker/1" or ready.get("runtime_mode") != "mock":
            raise RuntimeError(f"unexpected ready frame: {ready!r}")
        children = descendants(proc.pid)
        request_id = 0

        def call(method, params=None):
            nonlocal request_id
            request_id += 1
            request = {"id": request_id, "method": method, "params": params or {}}
            proc.stdin.write((json.dumps(request) + "\n").encode("utf-8"))
            proc.stdin.flush()
            reply = receive()
            if reply.get("type") != "response" or reply.get("id") != request_id or "result" not in reply:
                raise RuntimeError(f"{method} failed: {reply!r}")
            return reply["result"]

        if call("health").get("status") != "ok":
            raise RuntimeError("health is not ok")
        if not call("app.info").get("version"):
            raise RuntimeError("app.info has no version")
        if not call("engines.status").get("engines"):
            raise RuntimeError("engines.status has no engines")
        if "mock" not in call("engines.capabilities"):
            raise RuntimeError("mock capabilities missing")
        if not call("profiles.list").get("profiles"):
            raise RuntimeError("profiles.list is empty")
        conversation = call("conversations.create", {"title": "Release smoke"})["id"]
        run = call("runs.start_turn", {"conversation_id": conversation, "engine": "mock",
                                      "model_id": "deepseek-v4.1-flash-free", "prompt": "hello"})["id"]
        deadline = time.monotonic() + timeout
        while True:
            result = call("runs.get", {"run_id": run})
            if result["status"] == "succeeded":
                if not result.get("assistant_content"):
                    raise RuntimeError("mock run returned no assistant content")
                break
            if result["status"] in ("failed", "cancelled", "interrupted") or time.monotonic() >= deadline:
                raise RuntimeError(f"mock run did not succeed: {result!r}")
            time.sleep(0.1)
        backup = call("backup.create")
        if backup.get("size", 0) <= 0 or not Path(backup["path"]).is_file():
            raise RuntimeError(f"backup was not created: {backup!r}")
        # Exercise cleanup on an empty directory; preserve every generated .db.
        cleanup_dir = Path(tempfile.mkdtemp(prefix="agentos-backup-"))
        cleanup_path = cleanup_dir / "unused.db"
        if not call("backup.cleanup", {"path": str(cleanup_path)}).get("cleaned"):
            raise RuntimeError("backup cleanup failed")
        if cleanup_dir.exists():
            raise RuntimeError("backup cleanup left its temporary directory")
        print(f"Backup retained: {backup['path']}", flush=True)
        call("shutdown")
        proc.stdin.close()
        if proc.wait(timeout=timeout) != 0:
            raise RuntimeError(f"worker exit code {proc.returncode}")
        stderr_thread.join(timeout)
        if stderr_thread.is_alive():
            raise RuntimeError("worker descendant still holds stderr open")
        for child in children:
            # PID reuse is possible, so also require that it still holds a pipe:
            # inherited stderr remaining open is checked above.
            if child in descendants(proc.pid):
                raise RuntimeError(f"worker child {child} still running")
        if not (data / "agentos.db").is_file():
            raise RuntimeError("agentos.db was not created")
        print("SMOKE TEST PASSED", flush=True)
    except Exception:
        if proc and proc.poll() is None:
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
            if proc.poll() is None:
                proc.kill()
            proc.wait(timeout=10)
        if stderr_thread:
            stderr_thread.join(timeout=5)
        print("Worker stderr:\n" + b"".join(errors).decode("utf-8", errors="replace"), file=sys.stderr)
        raise


if __name__ == "__main__":
    main(sys.argv[1], int(sys.argv[2]))
