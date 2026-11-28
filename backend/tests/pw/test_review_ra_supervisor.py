import asyncio
import os
import signal
import sys

import pytest

from backend.pyworker.db.core import Database
from backend.pyworker.db.migrate import init_db
from backend.pyworker.runtime.supervisor import SupervisedProcess


def test_interrupted_runtime_session_index_is_repaired():
    db = Database(":memory:")
    init_db(db)
    db.execute("DROP INDEX uq_runtime_sessions_native")
    db.execute("PRAGMA user_version = 17")
    init_db(db)
    assert db.one("SELECT name FROM sqlite_master WHERE type='index' AND name='uq_runtime_sessions_native'")


@pytest.mark.asyncio
async def test_close_retries_failed_job_handle_close():
    class Job:
        calls = 0

        def terminate_and_wait(self):
            pass

        def close(self):
            self.calls += 1
            if self.calls == 1:
                raise OSError("transient close failure")

    proc = await SupervisedProcess.start([sys.executable, "-c", "import time; time.sleep(30)"])
    if proc.job is not None:
        proc.job.close()
    job = Job()
    proc.job = job
    issues = await asyncio.wait_for(proc.close(), 10)
    assert job.calls == 2 and proc.job is None
    assert issues


@pytest.mark.asyncio
async def test_communicate_owns_stdin_close():
    proc = await SupervisedProcess.start([sys.executable, "-c", "import sys; sys.stdin.buffer.read()"])
    original = proc.proc.stdin

    class Stdin:
        closes = 0

        def write(self, data):
            return original.write(data)

        def flush(self):
            return original.flush()

        def close(self):
            self.closes += 1
            return original.close()

    stdin = Stdin()
    proc.proc.stdin = stdin
    await asyncio.wait_for(proc.communicate(b"hello"), 10)
    assert stdin.closes == 1


@pytest.mark.skipif(os.name == "nt", reason="POSIX process groups")
@pytest.mark.asyncio
async def test_close_does_not_wait_for_escaped_pipe_holder(tmp_path):
    marker = tmp_path / "pid"
    child = "import os,time; os.setsid(); time.sleep(30)"
    script = ("import subprocess,sys,pathlib,time; "
              f"p=subprocess.Popen([sys.executable,'-c',{child!r}]); "
              f"pathlib.Path({str(marker)!r}).write_text(str(p.pid)); time.sleep(30)")
    proc = await SupervisedProcess.start([sys.executable, "-c", script])
    pid = None
    try:
        while not marker.exists():
            await asyncio.sleep(0.01)
        pid = int(marker.read_text())
        await asyncio.sleep(0.2)
        await asyncio.wait_for(proc.close(), 3)
        assert all(task.done() for task in proc._drains)
    finally:
        if pid:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
