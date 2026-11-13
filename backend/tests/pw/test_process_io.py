"""Exercise pipe limits with real child processes, including adapter cleanup."""

import asyncio
import subprocess
import sys

import pytest

from backend.pyworker.runtime.process_io import communicate_bounded, RuntimeOutputLimit
from backend.pyworker.runtime.base import ExecuteAgentTask


@pytest.mark.asyncio
async def test_drains_both_pipes_while_writing_stdin():
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-c",
        "import sys; sys.stderr.buffer.write(b'e'*200000); sys.stderr.flush(); "
        "data=sys.stdin.buffer.read(); sys.stdout.buffer.write(data)",
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE)
    out, err = await asyncio.wait_for(communicate_bounded(proc, b"a" * 200000), 10)
    assert out == b"a" * 200000 and err == b"e" * 200000
    assert proc.returncode == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["stdout", "stderr"])
@pytest.mark.parametrize("engine", ["claude", "codex", "agy", "opencode"])
async def test_adapter_kills_on_output_limit(tmp_path, monkeypatch, channel, engine):
    from backend.pyworker.runtime.claude_runtime import ClaudeCodeRuntime
    from backend.pyworker.runtime.codex_runtime import CodexCliRuntime
    from backend.pyworker.runtime.agy_runtime import AgyCliRuntime
    from backend.pyworker.runtime.opencode_runtime import RealOpenCodeRuntime

    script = tmp_path / "flood.py"
    script.write_text(
        "import sys,time\n"
        "if sys.stdin: sys.stdin.buffer.read()\n"
        f"stream=sys.{channel}.buffer\n"
        "while True:\n"
        " stream.write(b'x'*65536); stream.flush()\n", encoding="utf-8")
    classes = {"claude": ClaudeCodeRuntime, "codex": CodexCliRuntime,
               "agy": AgyCliRuntime, "opencode": RealOpenCodeRuntime}
    runtime = classes[engine]()
    if engine != "opencode":
        runtime._cmd = [sys.executable, str(script)]

        async def supported():
            return True

        monkeypatch.setattr(runtime, "_check_flags", supported)
    # Redirect only this adapter's spawn into a real local test child.
    original_spawn = asyncio.create_subprocess_exec
    spawned = []

    async def spawn(*args, **kwargs):
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
        proc = await original_spawn(sys.executable, str(script), **kwargs)
        spawned.append(proc)
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    if engine == "opencode":
        operation = runtime._run_cli_api("GET", "/api/model", timeout=15)
    else:
        operation = runtime.execute_task(
            ExecuteAgentTask(prompt="hello", model_id="model", session_title="flood", timeout=15))
    with pytest.raises(RuntimeOutputLimit):
        await asyncio.wait_for(operation, 20)
    assert len(spawned) == 1 and spawned[0].returncode is not None
    assert not getattr(runtime, "_inflight", {})
