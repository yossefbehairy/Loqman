"""Application context shared by every API handler (no globals in handlers)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional, Set

from .appsettings import Settings
from .core.approvals import ApprovalManager
from .core.conversation_runs import ConversationRunManager
from .core.deliverables import DeliverableManager
from .db.core import Database


@dataclass
class Context:
    db: Database
    settings: Settings
    runs: ConversationRunManager
    approvals: ApprovalManager
    deliverables: DeliverableManager
    get_runtime: Callable[[], Any]
    # Factory for explicit engines (profile.engine / engine endpoints).
    engine_factory: Callable[[str], Any]
    # Open event streams (set by the server; None in pure in-process use).
    streams: Optional[Any] = None
    # Fire-and-forget work (mission/workflow runners) so shutdown can drain it.
    background: Set[asyncio.Task] = field(default_factory=set)
    # Live progress registry for synchronous CEO asks.
    ask_progress: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    # Per-app cache of the last engine discovery (see engines.status).
    engine_cache: Dict[str, Any] = field(default_factory=dict)

    def spawn(self, coro, name: str = "background") -> asyncio.Task:
        task = asyncio.get_running_loop().create_task(coro, name=name)
        self.background.add(task)
        task.add_done_callback(self.background.discard)
        return task
