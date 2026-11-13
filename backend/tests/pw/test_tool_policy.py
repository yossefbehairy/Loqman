from backend.pyworker.core.tool_policy import ToolIdentity, allows_tool


def test_default_deny_and_explicit_allow():
    tool = ToolIdentity("mcp", "read", "browser")
    assert not allows_tool({}, tool)
    assert allows_tool({"allow": ["mcp:browser:*"]}, tool)
    assert not allows_tool({"allow": ["mcp:other:*"]}, tool)


def test_deny_overrides_allow_and_invalid_scope_fails_closed():
    tool = ToolIdentity("native", "shell")
    assert not allows_tool({"allow": ["native:*"], "deny": ["native:shell"]}, tool)
    assert not allows_tool({"allow": ["native:*", "mcp:BAD:read"]}, tool)
    assert not allows_tool({"allow": ["native:*"] * 257}, tool)
    assert not allows_tool({"allow": ["native:*"], "unknown": []}, tool)


def test_control_chars_and_wildcards_cannot_name_tools():
    assert not allows_tool({"allow": ["native:*"]}, ToolIdentity("native", "bad\nname"))
    assert not allows_tool({"allow": ["native:*"]}, ToolIdentity("native", "*"))
    assert not allows_tool({"allow": ["mcp:browser:*"]}, ToolIdentity("mcp", "read", "browser\n"))
    assert not allows_tool({"allow": ["mcp:browser:*"]}, ToolIdentity("mcp", "read", None))
