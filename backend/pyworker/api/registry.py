"""Method registry: ``@method("run.status")`` registers ``async def h(ctx, params)``."""

from __future__ import annotations

from typing import Awaitable, Callable, Dict

Handler = Callable[..., Awaitable[dict]]
METHODS: Dict[str, Handler] = {}


def method(name: str):
    def deco(fn: Handler) -> Handler:
        if name in METHODS:
            raise RuntimeError(f"duplicate method {name}")
        METHODS[name] = fn
        return fn
    return deco
