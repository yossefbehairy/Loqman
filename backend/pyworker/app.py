"""Application bootstrap, transport-agnostic.

``Application.create`` opens the database, applies schema upgrades, runs the
startup crash-recovery policy, seeds the default org and wires every manager.
``handle(method, params)`` dispatches to the registered API handlers; nothing
in here knows about stdin/stdout.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Optional

from . import PROTOCOL, WORKER_VERSION
from .appsettings import Settings, configure, settings as shared_settings
from .context import Context
from .core import profiles as profile_repo
from .core.approvals import ApprovalManager
from .core.conversation_runs import ConversationRunManager
from .core.deliverables import DeliverableManager
from .core.lifecycle import run_startup_crash_recovery
from .core import preferences as prefs_mod
from .db.core import Database
from .db.migrate import init_db
from .errors import METHOD_NOT_FOUND, WorkerError
from .runtime import get_runtime as default_get_runtime
from .runtime import ledger

from .api.registry import METHODS
from .api import ceo, convo, missions, org, preferences, streams, system  # noqa: F401  (register handlers)

log = logging.getLogger("agentos.app")


def _default_runtime_getter(runtime: Any, runtime_factory: Optional[Callable[[str], Any]]) -> Callable[[], Any]:
    """The context's ``get_runtime``: an injected runtime always wins; otherwise the default runtime follows the
    saved default engine (real mode) and stays the mock in mock mode."""
    if runtime is not None:
        return lambda: runtime
    cache: dict = {}

    def get() -> Any:
        engine = prefs_mod.default_engine_id()
        if shared_settings.RUNTIME_MODE != "real":
            return default_get_runtime()
        if runtime_factory is not None:
            if engine not in cache:
                cache[engine] = runtime_factory(engine)
            return cache[engine]
        return default_get_runtime(engine)

    return get


class Application:
    def __init__(self, ctx: Context, recovery: dict, interrupted_runs: int):
        self.ctx = ctx
        self.recovery = recovery
        self.interrupted_runs = interrupted_runs

    @classmethod
    def create(
        cls,
        settings: Optional[Settings] = None,
        db_path: Optional[str] = None,
        runtime: Any = None,
        runtime_factory: Optional[Callable[[str], Any]] = None,
        **run_manager_kwargs: Any,
    ) -> "Application":
        """``runtime``/``runtime_factory`` inject test doubles; production uses
        the engines selected by RUNTIME_MODE / profile engine."""
        if settings is not None:
            configure(settings)
        path = db_path or shared_settings.SQLITE_DB_PATH
        db = Database(path)
        try:
            init_db(db)
            from .core.workspace_writes import recover
            recover(db)
            recovery = run_startup_crash_recovery(db)
            seeded = profile_repo.seed_defaults(db)
            if seeded:
                log.info("Seeded %s standard AI employee profile(s).", seeded)
            if runtime_factory is not None:
                engine_factory = run_factory = runtime_factory
            else:
                from .runtime.engine_registry import create_engine_runtime
                engine_factory = run_factory = create_engine_runtime
            runs = ConversationRunManager(db, runtime_factory=run_factory, **run_manager_kwargs)
            interrupted = runs.recover_interrupted()
            ledger.bind(db)
            prefs = prefs_mod.Preferences(prefs_mod.path_for_db(path))
            prefs_mod.bind(prefs)
            ctx = Context(
                db=db, settings=shared_settings, runs=runs, approvals=ApprovalManager(db),
                deliverables=DeliverableManager(db),
                get_runtime=_default_runtime_getter(runtime, runtime_factory),
                engine_factory=engine_factory, preferences=prefs,
            )
        except BaseException:
            db.close()
            raise
        return cls(ctx, recovery, interrupted)

    # -- dispatch -----------------------------------------------------------
    def knows(self, method: str) -> bool:
        return method in METHODS or method == "shutdown"

    @staticmethod
    def method_names() -> list:
        return sorted([*METHODS, "shutdown"])

    async def handle(self, method: str, params: dict) -> Any:
        handler = METHODS.get(method)
        if handler is None:
            raise WorkerError(METHOD_NOT_FOUND, "Unknown method.")
        return await handler(self.ctx, params)

    # -- lifecycle ----------------------------------------------------------
    async def shutdown(self, timeout: float = 5.0) -> int:
        """Stop streams and background work, then persist what was in flight as
        interrupted/failed so the next start has nothing dangling. Returns the
        number of tasks still running at the deadline."""
        ctx = self.ctx
        if ctx.streams is not None:
            await ctx.streams.close_all()
        pending = [t for t in ctx.background if not t.done()]
        for t in pending:
            t.cancel()
        left = 0
        if pending:
            _d, still = await asyncio.wait(pending, timeout=timeout)
            left = len(still)
        try:
            await asyncio.wait_for(ctx.runs.shutdown(), timeout)
        except asyncio.TimeoutError:
            left += 1
            log.warning("run tasks did not stop within %ss; interrupting them in the database", timeout)
        ctx.runs.interrupt_active()
        run_startup_crash_recovery(ctx.db)
        ledger.bind(None)
        prefs_mod.bind(None)
        ctx.db.close()
        return left

    def info(self) -> dict:
        return {"protocol": PROTOCOL, "worker_version": WORKER_VERSION,
                "app": shared_settings.APP_NAME, "app_version": shared_settings.APP_VERSION}
