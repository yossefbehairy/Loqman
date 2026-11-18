"""One-off cleanup of messages duplicated by old "CEO history" imports (audit C14).

Old UI versions copied the CEO exchanges into a conversation titled "CEO history" on every restart.
A duplicate is a message in such a conversation with the same (conversation_id, role, content, created_at)
as an earlier one; the lowest ``seq`` is kept. Nothing outside conversations titled exactly "CEO history"
is touched, and a row a run points at is never deleted. Idempotent: a clean database is a no-op and no
backup is made. Before the first deletion the DB is copied with the SQLite online-backup API.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from contextlib import closing
from datetime import datetime, timezone

from .core import Database

log = logging.getLogger("agentos.db.ceo_history_dedupe")
TITLE = "CEO history"

_DUPLICATES = """
SELECT m.seq FROM conversation_messages m
JOIN conversations c ON c.id = m.conversation_id AND c.title = ?
WHERE EXISTS (
    SELECT 1 FROM conversation_messages e
    WHERE e.conversation_id = m.conversation_id AND e.role = m.role AND e.content = m.content
      AND e.created_at = m.created_at AND e.seq < m.seq)
  AND m.seq NOT IN (SELECT user_message_seq FROM conversation_runs WHERE user_message_seq IS NOT NULL)
  AND m.seq NOT IN (SELECT assistant_message_seq FROM conversation_runs WHERE assistant_message_seq IS NOT NULL)
ORDER BY m.seq
"""


def _backup(db: Database) -> str | None:
    if not db.path or db.path == ":memory:" or not os.path.isfile(db.path):
        return None
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    dest = f"{db.path}.pre-ceo-dedupe-{stamp}.bak"
    with closing(sqlite3.connect(dest)) as dst:
        db.conn.backup(dst)
    return dest


def dedupe_ceo_history(db: Database) -> int:
    """Delete duplicate CEO-history messages; return how many rows were removed."""
    seqs = [r["seq"] for r in db.all(_DUPLICATES, (TITLE,))]
    if not seqs:
        return 0
    _backup(db)
    removed = 0
    with db.tx():
        for i in range(0, len(seqs), 500):
            chunk = seqs[i:i + 500]
            cur = db.execute(
                f"DELETE FROM conversation_messages WHERE seq IN ({','.join('?' * len(chunk))})", chunk)
            removed += cur.rowcount
    log.info("Removed %s duplicate CEO history message(s).", removed)
    return removed
