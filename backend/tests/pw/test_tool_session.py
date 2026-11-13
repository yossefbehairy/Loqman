import asyncio
import json
import os

import pytest

from backend.pyworker.runtime.base import AgentResult
from backend.pyworker.core import workspace_writes
from .helpers import Api
from .test_runs import wait_status


class McpRuntime:
    def __init__(self, steps):
        self.steps = steps
        self.servers = None
        self.results = []

    async def execute_task(self, task):
        self.servers = task.mcp_servers
        server = self.servers["agentos_workspace"]
        proc = await asyncio.create_subprocess_exec(
            server["command"], *server["args"], env={**os.environ, **server["env"]},
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE)
        try:
            async def rpc(method, params=None, notify=False):
                message = {"jsonrpc": "2.0", "method": method, "params": params or {}}
                if not notify:
                    message["id"] = 1
                proc.stdin.write(json.dumps(message).encode() + b"\n")
                await proc.stdin.drain()
                if not notify:
                    return json.loads(await asyncio.wait_for(proc.stdout.readline(), 10))

            await rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "fixture", "version": "1"}})
            await rpc("notifications/initialized", notify=True)
            self.catalog = (await rpc("tools/list"))["result"]["tools"]
            for name, args in self.steps:
                result = (await rpc("tools/call", {"name": name, "arguments": args}))["result"]
                self.results.append(result)
            return AgentResult(text="Finished", model_id=task.model_id)
        finally:
            proc.stdin.close()
            if proc.returncode is None:
                proc.kill()
            await proc.communicate()

    async def abort_task(self, key):
        pass


async def start(make_app, tmp_path, steps, mode="read"):
    runtime = McpRuntime(steps)
    api = Api(make_app(runtime_factory=lambda _: runtime, mode="real"))
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "input.txt").write_text("unique evidence", encoding="utf-8")
    ws = await api("workspaces.create", path=str(root))
    conv = await api("conversations.create", title="Task", workspace_id=ws["id"])
    run = await api("runs.start_turn", conversation_id=conv["id"], engine="claude-code",
                    model_id="model", prompt="Read the input and create a report", tool_mode=mode)
    return api, runtime, root, run


@pytest.mark.asyncio
async def test_read_crosses_process_boundary_and_revokes_endpoint(make_app, tmp_path):
    api, runtime, _, run = await start(make_app, tmp_path, [("workspace_read", {"path": "input.txt"})])
    await wait_status(api, run["id"], "succeeded")
    assert json.loads(runtime.results[0]["content"][0]["text"])["content"] == "unique evidence"
    assert [t["name"] for t in runtime.catalog] == ["workspace_read"]
    env = runtime.servers["agentos_workspace"]["env"]
    with pytest.raises(OSError):
        await asyncio.open_connection("127.0.0.1", int(env["AGENTOS_MCP_PORT"]))
    events = api.app.ctx.runs.list_events(run["id"])
    assert any(e["type"] == "tool.evidence" and e["data"]["complete"] for e in events)


@pytest.mark.asyncio
@pytest.mark.parametrize("decision", ["approve", "deny", "cancel"])
async def test_write_waits_for_exact_proposal_and_honors_decision(make_app, tmp_path, decision):
    steps = [("workspace_read", {"path": "input.txt"}),
             ("workspace_write", {"path": "report.txt", "content": "Verified report"})]
    api, runtime, root, run = await start(make_app, tmp_path, steps, "write")
    for _ in range(150):
        approvals = (await api("approvals.list", run_id=run["id"]))["approvals"]
        if approvals:
            break
        await asyncio.sleep(.02)
    assert approvals
    proposals = (await api("runs.write_proposals", run_id=run["id"]))["proposals"]
    assert proposals[0]["content"] == "Verified report"
    assert not (root / "report.txt").exists()
    if decision == "cancel":
        await api("runs.cancel", run_id=run["id"])
        await wait_status(api, run["id"], "cancelled")
    else:
        await api("approvals.decide", run_id=run["id"], approval_id=approvals[0]["id"], decision=decision)
        await wait_status(api, run["id"], "succeeded" if decision == "approve" else "failed")
    assert (root / "report.txt").exists() == (decision == "approve")
    if decision == "approve":
        proposal = api.db.one("SELECT * FROM workspace_writes WHERE run_id=?", (run["id"],))
        before = (root / "report.txt").stat().st_mtime_ns
        assert workspace_writes.apply(api.db, proposal)["verified"]
        assert (root / "report.txt").stat().st_mtime_ns == before


@pytest.mark.asyncio
async def test_unread_pages_fail_completion(make_app, tmp_path):
    api, _, root, run = await start(make_app, tmp_path, [("workspace_read", {"path": "input.txt", "limit": 2})])
    await wait_status(api, run["id"], "failed")
    assert not (root / "report.txt").exists()


@pytest.mark.asyncio
async def test_no_tool_calls_fail_completion(make_app, tmp_path):
    api, _, _, run = await start(make_app, tmp_path, [])
    await wait_status(api, run["id"], "failed")
