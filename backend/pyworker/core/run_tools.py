"""Run-scoped paged workspace reads shared with the MCP tool session."""
from __future__ import annotations

import copy
import hashlib
import asyncio

from backend.pyworker.core import run_inputs
from backend.pyworker.core.tool_policy import ToolIdentity, allows_tool
from backend.pyworker.db.core import Database


class ToolAccessDenied(Exception):
    pass


class RunWorkspaceTools:
    """One fixed grant for one conversation run; use on the DB event-loop thread."""

    def __init__(self, db: Database, run_id: str, scope: object):
        self._db = db
        self._run_id = run_id
        self._scope = copy.deepcopy(scope)
        run = self._active_run()
        self._conversation_id = run["conversation_id"]
        conversation = self._db.one("SELECT workspace_id FROM conversations WHERE id=?", (self._conversation_id,))
        if not conversation or not conversation["workspace_id"]:
            raise ToolAccessDenied("Run has no workspace")
        self._workspace_id = conversation["workspace_id"]
        workspace = self._db.one("SELECT path FROM workspaces WHERE id=?", (self._workspace_id,))
        if workspace is None:
            raise ToolAccessDenied("Workspace unavailable")
        self._workspace_path = workspace["path"]

    def _active_run(self) -> dict:
        run = self._db.one("SELECT status, conversation_id, kind FROM conversation_runs WHERE id=?", (self._run_id,))
        if not run or run["status"] != "running" or run["kind"] != "conversation" or not run["conversation_id"]:
            raise ToolAccessDenied("Run is not active")
        return run

    def check(self, tool: str) -> None:
        if not allows_tool(self._scope, ToolIdentity("mcp", tool, "workspace")):
            raise ToolAccessDenied("Tool is not allowed")
        run = self._active_run()
        if run["conversation_id"] != self._conversation_id:
            raise ToolAccessDenied("Run scope changed")
        conversation = self._db.one("SELECT workspace_id FROM conversations WHERE id=?", (self._conversation_id,))
        workspace = self._db.one("SELECT path FROM workspaces WHERE id=?", (self._workspace_id,))
        if (not conversation or conversation["workspace_id"] != self._workspace_id
                or not workspace or workspace["path"] != self._workspace_path):
            raise ToolAccessDenied("Workspace scope changed")

    def read(self, path: str, offset: int = 0, limit: int = 6000) -> dict:
        self.check("workspace_read")
        return self._read_file(self._workspace_path, path, offset, limit)

    async def read_async(self, path: str, offset: int = 0, limit: int = 6000) -> dict:
        self.check("workspace_read")
        result = await asyncio.to_thread(self._read_file, self._workspace_path, path, offset, limit)
        self.check("workspace_read")
        return result

    @staticmethod
    def _read_file(root: str, path: str, offset: int, limit: int) -> dict:
        from backend.pyworker.core import workspace_fs as fs
        paths = run_inputs.normalize_paths([path])
        real, relative = fs.resolve_in_workspace(root, paths[0])
        data = fs.read_limited(real, root)
        text = data.decode("utf-8")
        if any((ord(c) < 32 and c not in "\t\n\r\f") or ord(c) == 127 for c in text):
            raise ToolAccessDenied("File is not text")
        end = min(offset + limit, len(text))
        return {"path": relative, "content": text[offset:end], "sha256": hashlib.sha256(data).hexdigest(),
                "offset": offset, "next_offset": end if end < len(text) else None, "total_chars": len(text),
                "untrusted": True}
