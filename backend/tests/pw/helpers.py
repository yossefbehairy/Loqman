"""In-process helpers: drive the worker Application directly (no transport)."""

from __future__ import annotations

import pytest

from backend.pyworker.errors import WorkerError


class Api:
    """``await api("run.get", run_id=...)`` -> result dict; ``await api.fail(...)`` -> WorkerError."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, name: str, /, **params):
        return await self.app.handle(name, params)

    async def fail(self, name: str, /, **params) -> WorkerError:
        with pytest.raises(WorkerError) as info:
            await self.app.handle(name, params)
        return info.value

    @property
    def db(self):
        return self.app.ctx.db
