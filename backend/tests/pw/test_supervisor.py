"""Only local sys.executable children; never engine executables or model calls."""

import asyncio
import ctypes
from pathlib import Path
import subprocess
import sys

import pytest

from backend.pyworker.runtime.process_io import RuntimeOutputLimit
from backend.pyworker.runtime.supervisor import SupervisedProcess


@pytest.mark.asyncio
async def test_supervisor_drains_both_pipes_and_stdin_without_shell():
    proc = await SupervisedProcess.start([sys.executable, "-c",
        "import sys; sys.stderr.buffer.write(b'e'*200000); sys.stderr.flush(); "
        "data=sys.stdin.buffer.read(); sys.stdout.buffer.write(data)"])
    out, err = await asyncio.wait_for(proc.communicate(b"literal & | > $()" * 20000), 10)
    assert out == b"literal & | > $()" * 20000 and err == b"e" * 200000
    assert proc.proc.returncode == 0 and all(t.done() for t in proc._drains)
    assert await proc.close() == await proc.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["stdout", "stderr"])
async def test_supervisor_bounds_bytes_and_awaits_exit(channel):
    proc = await SupervisedProcess.start([sys.executable, "-c", "import sys\n"
        f"while True: sys.{channel}.buffer.write(b'x'*65536); sys.{channel}.flush()"], max_bytes=100000)
    with pytest.raises(RuntimeOutputLimit):
        await asyncio.wait_for(proc.communicate(), 10)
    assert proc.proc.returncode is not None and all(t.done() for t in proc._drains)
    assert sum(len(t.result()) for t in proc._drains) <= 100000


@pytest.mark.asyncio
async def test_supervisor_cancellation_joins_readers_and_process():
    proc = await SupervisedProcess.start([sys.executable, "-c", "import time; time.sleep(30)"])
    operation = asyncio.create_task(proc.communicate())
    await asyncio.sleep(0.03)
    operation.cancel()
    with pytest.raises(asyncio.CancelledError):
        await operation
    assert proc.proc.returncode is not None and all(t.done() for t in proc._drains)


@pytest.mark.asyncio
async def test_supervisor_rejects_shell_strings_and_missing_executable():
    with pytest.raises(ValueError):
        await SupervisedProcess.start("echo hello")
    with pytest.raises(OSError):
        await SupervisedProcess.start(["agentos-nonexistent-test-executable-4f3903"])


async def wait_file(path):
    async def wait():
        while not path.exists() or not path.read_text():
            await asyncio.sleep(0.01)
        return int(path.read_text())
    return await asyncio.wait_for(wait(), 10)


def process_handle(pid):
    from ctypes import wintypes as w
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
    kernel.OpenProcess.restype = w.HANDLE
    kernel.WaitForSingleObject.argtypes = [w.HANDLE, w.DWORD]
    kernel.WaitForSingleObject.restype = w.DWORD
    kernel.CloseHandle.argtypes = [w.HANDLE]
    kernel.CloseHandle.restype = w.BOOL
    handle = kernel.OpenProcess(0x100000, False, pid)
    assert handle, ctypes.get_last_error()
    return kernel, handle


def tree_script(marker):
    return "import subprocess,sys,time,pathlib; " \
        "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)']); " \
        f"pathlib.Path({str(marker)!r}).write_text(str(p.pid)); time.sleep(30)"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows Job Object ownership")
@pytest.mark.asyncio
async def test_windows_job_close_kills_grandchild_and_awaits_root(tmp_path):
    marker = tmp_path / "grandchild.pid"
    proc = await SupervisedProcess.start([sys.executable, "-c", tree_script(marker)])
    kernel, handle = None, None
    try:
        assert proc.job is not None, proc.issues
        kernel, handle = process_handle(await wait_file(marker))
        assert await asyncio.wait_for(proc.close(), 10) == ()
        assert await asyncio.to_thread(kernel.WaitForSingleObject, handle, 5000) == 0
        assert proc.proc.returncode is not None and all(t.done() for t in proc._drains)
    finally:
        await proc.close()
        if handle:
            kernel.CloseHandle(handle)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows taskkill fallback")
@pytest.mark.asyncio
async def test_windows_taskkill_fallback_kills_tree_and_reports_lost_crash_ownership(tmp_path, monkeypatch):
    from backend.pyworker.runtime import supervisor

    def unavailable():
        raise OSError("job unavailable")

    monkeypatch.setattr(supervisor, "_WindowsJob", unavailable)
    marker = tmp_path / "fallback.pid"
    proc = await SupervisedProcess.start([sys.executable, "-c", tree_script(marker)])
    kernel, handle = None, None
    try:
        kernel, handle = process_handle(await wait_file(marker))
        issues = await asyncio.wait_for(proc.close(), 10)
        assert issues and "fallback" in issues[0].message
        assert await asyncio.to_thread(kernel.WaitForSingleObject, handle, 5000) == 0
        assert proc.proc.returncode is not None
    finally:
        await proc.close()
        if handle:
            kernel.CloseHandle(handle)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows job handle closes on worker death")
@pytest.mark.asyncio
async def test_windows_worker_death_closes_job_and_kills_descendants(tmp_path):
    child_marker = tmp_path / "child.pid"
    grand_marker = tmp_path / "grandchild.pid"
    script = (
        "import asyncio,sys,pathlib\n"
        "from backend.pyworker.runtime.supervisor import SupervisedProcess\n"
        "async def main():\n"
        f" p=await SupervisedProcess.start([sys.executable,'-c',{tree_script(grand_marker)!r}])\n"
        " assert p.job is not None, p.issues\n"
        f" pathlib.Path({str(child_marker)!r}).write_text(str(p.pid))\n"
        " await asyncio.sleep(30)\n"
        "asyncio.run(main())\n"
    )
    worker = subprocess.Popen([sys.executable, "-c", script], cwd=str(Path(__file__).resolve().parents[3]),
        creationflags=subprocess.CREATE_NO_WINDOW, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    handles = []
    try:
        for marker in (child_marker, grand_marker):
            handles.append(process_handle(await wait_file(marker)))
        worker.kill()
        await asyncio.to_thread(worker.wait, timeout=5)
        for kernel, handle in handles:
            assert await asyncio.to_thread(kernel.WaitForSingleObject, handle, 5000) == 0
    finally:
        if worker.poll() is None:
            worker.kill()
        await asyncio.to_thread(worker.wait, timeout=5)
        for kernel, handle in handles:
            kernel.CloseHandle(handle)
