"""Fail-closed tool selection for future engine and MCP integrations.

Selector identity and deny precedence are adapted from OpenMausBot's
shared/tool-scope.ts (Apache-2.0, Copyright 2026 Milind Soni and contributors).
Unlike that source, missing allow rules deny access by default. This module is
not an enforcement boundary until every engine call passes through it.
"""

import re
from dataclasses import dataclass

_SERVER = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


@dataclass(frozen=True)
class ToolIdentity:
    kind: str
    name: str
    server: str = ""


def _name(value: object) -> bool:
    return (isinstance(value, str) and bool(value.strip()) and len(value) <= 1024
            and not _CONTROL.search(value) and "*" not in value)


def _selector(value: object) -> ToolIdentity:
    if not isinstance(value, str) or len(value) > 1024 or _CONTROL.search(value):
        raise ValueError("invalid tool selector")
    parts = value.split(":", 2)
    if len(parts) == 2 and parts[0] == "native" and (parts[1] == "*" or _name(parts[1])):
        return ToolIdentity("native", parts[1])
    if (len(parts) == 3 and parts[0] == "mcp" and _SERVER.fullmatch(parts[1])
            and (parts[2] == "*" or _name(parts[2]))):
        return ToolIdentity("mcp", parts[2], parts[1])
    raise ValueError("invalid tool selector")


def allows_tool(scope: object, tool: ToolIdentity) -> bool:
    """Reject malformed policies and identities, with deny taking precedence."""
    if (not isinstance(tool, ToolIdentity) or tool.kind not in ("native", "mcp")
            or not _name(tool.name) or (tool.kind == "native" and tool.server)
            or (tool.kind == "mcp" and (not isinstance(tool.server, str) or not _SERVER.fullmatch(tool.server)))):
        return False
    if not isinstance(scope, dict) or set(scope) - {"allow", "deny"}:
        return False
    parsed = {}
    try:
        for key in ("allow", "deny"):
            values = scope.get(key, [])
            if not isinstance(values, list) or len(values) > 256:
                return False
            parsed[key] = [_selector(value) for value in values]
    except (ValueError, TypeError):
        return False

    def matches(selector: ToolIdentity) -> bool:
        return (selector.kind == tool.kind and selector.server == tool.server
                and selector.name in ("*", tool.name))

    return any(map(matches, parsed["allow"])) and not any(map(matches, parsed["deny"]))
