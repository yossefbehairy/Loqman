import json
import subprocess
import sys

from backend.pyworker.mcp_workspace import WorkspaceMcp


def request(server, method, params=None, request_id=1):
    return server.handle({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}})


def setup(server):
    response = request(server, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}})
    assert response["result"]["protocolVersion"] == "2025-06-18"
    assert server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None


def test_policy_enforced_at_discovery_and_execution(tmp_path):
    (tmp_path / "hello.txt").write_text("hello", encoding="utf-8")
    server = WorkspaceMcp(str(tmp_path), {})
    assert "error" in request(server, "tools/list")
    setup(server)
    assert request(server, "tools/list")["result"]["tools"] == []
    assert "error" in request(server, "tools/call", {"name": "workspace_read", "arguments": {"path": "hello.txt"}})
    server.scope = {"allow": ["mcp:workspace:workspace_read"]}
    assert len(request(server, "tools/list")["result"]["tools"]) == 1
    result = request(server, "tools/call", {"name": "workspace_read", "arguments": {"path": "hello.txt"}})["result"]
    assert json.loads(result["content"][0]["text"]) == {"path": "hello.txt", "content": "hello", "untrusted": True}
    server.scope = {"allow": ["mcp:workspace:*"], "deny": ["mcp:workspace:workspace_read"]}
    assert "error" in request(server, "tools/call", {"name": "workspace_read", "arguments": {"path": "hello.txt"}})


def test_read_denies_traversal_binary_oversize_and_invalid_arguments(tmp_path):
    (tmp_path / "binary").write_bytes(b"\x00")
    (tmp_path / "large").write_bytes(b"a" * (24 * 1024 + 1))
    server = WorkspaceMcp(str(tmp_path), {"allow": ["mcp:workspace:*"]})
    setup(server)
    for path in ("../secret", "binary", "large"):
        result = request(server, "tools/call", {"name": "workspace_read", "arguments": {"path": path}})
        assert result["result"]["isError"] is True
    assert "error" in request(server, "tools/call", {"name": "workspace_read", "arguments": {"path": "binary", "extra": 1}})


def test_real_stdio_handshake_and_read(tmp_path):
    (tmp_path / "a.txt").write_text("test", encoding="utf-8")
    frames = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "workspace_read", "arguments": {"path": "a.txt"}}},
    ]
    proc = subprocess.run([sys.executable, "-m", "backend.pyworker.mcp_workspace", "--workspace", str(tmp_path), "--allow-read"],
                          input="".join(json.dumps(x) + "\n" for x in frames), text=True, capture_output=True, timeout=10)
    assert proc.returncode == 0, proc.stderr
    messages = [json.loads(line) for line in proc.stdout.splitlines()]
    assert [m["id"] for m in messages] == [1, 2]
    assert json.loads(messages[1]["result"]["content"][0]["text"])["content"] == "test"
