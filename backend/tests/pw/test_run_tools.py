import asyncio

import pytest

from backend.pyworker.core.run_tools import RunWorkspaceTools, ToolAccessDenied
from backend.pyworker.mcp_workspace import WorkspaceMcp
from .helpers import Api
from .test_runs import FakeRuntime, wait_status


@pytest.mark.asyncio
async def test_run_workspace_read_is_scoped_and_revoked_on_cancel(make_app, tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "notes.txt").write_text("hello", encoding="utf-8")
    runtime = FakeRuntime()
    api = Api(make_app(runtime_factory=lambda engine: runtime, mode="real"))
    ws = await api("workspaces.create", path=str(root))
    conv = await api("conversations.create", title="c", workspace_id=ws["id"])
    run = await api("runs.start_turn", conversation_id=conv["id"], engine="codex", model_id="m", prompt="p")
    await asyncio.wait_for(runtime.started.wait(), 3)
    scope = {"allow": ["mcp:workspace:workspace_read"]}
    broker = RunWorkspaceTools(api.db, run["id"], scope)
    mcp = WorkspaceMcp(str(root), {"allow": ["mcp:workspace:workspace_read"]}, reader=broker.read)
    mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {}}})
    mcp.handle({"jsonrpc": "2.0", "method": "notifications/initialized"})
    call = {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "workspace_read", "arguments": {"path": "notes.txt"}}}
    assert mcp.handle(call)["result"]["isError"] is False
    scope["allow"].clear()
    assert broker.read("notes.txt")["content"] == "hello"
    denied = RunWorkspaceTools(api.db, run["id"], {})
    with pytest.raises(ToolAccessDenied):
        denied.read("notes.txt")
    with pytest.raises(Exception) as exc:
        broker.read("../notes.txt")
    assert getattr(exc.value, "status", None) == 422
    await api("runs.cancel", run_id=run["id"])
    assert mcp.handle(call)["result"]["isError"] is True
    with pytest.raises(ToolAccessDenied):
        broker.read("notes.txt")


@pytest.mark.asyncio
async def test_workspace_binding_cannot_be_swapped(make_app, tmp_path):
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    runtime = FakeRuntime()
    api = Api(make_app(runtime_factory=lambda engine: runtime, mode="real"))
    ws1 = await api("workspaces.create", path=str(first))
    ws2 = await api("workspaces.create", path=str(second))
    conv = await api("conversations.create", title="c", workspace_id=ws1["id"])
    run = await api("runs.start_turn", conversation_id=conv["id"], engine="codex", model_id="m", prompt="p")
    await asyncio.wait_for(runtime.started.wait(), 3)
    broker = RunWorkspaceTools(api.db, run["id"], {"allow": ["mcp:workspace:*"]})
    api.db.update("conversations", {"workspace_id": ws2["id"]}, "id=?", (conv["id"],))
    with pytest.raises(ToolAccessDenied):
        broker.read("notes.txt")
    await api("runs.cancel", run_id=run["id"])


@pytest.mark.asyncio
async def test_run_without_workspace_and_completed_run_are_rejected(make_app, tmp_path):
    runtime = FakeRuntime()
    api = Api(make_app(runtime_factory=lambda engine: runtime, mode="real"))
    conv = await api("conversations.create", title="no workspace")
    run = await api("runs.start_turn", conversation_id=conv["id"], engine="codex", model_id="m", prompt="p")
    await asyncio.wait_for(runtime.started.wait(), 3)
    with pytest.raises(ToolAccessDenied):
        RunWorkspaceTools(api.db, run["id"], {"allow": ["mcp:workspace:*"]})
    runtime.gate.set()
    await wait_status(api, run["id"], "succeeded")
    with pytest.raises(ToolAccessDenied):
        RunWorkspaceTools(api.db, run["id"], {"allow": ["mcp:workspace:*"]})
