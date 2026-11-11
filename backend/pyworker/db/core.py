"""sqlite3 data-access core shared by every domain module.

* One connection, used only on the event-loop thread. Transactions never span
  an ``await`` (enforced by convention: domain code does its DB work in plain
  synchronous blocks), so no two coroutines can interleave inside one.
* Timestamps keep the legacy SQLAlchemy format (naive UTC, space separated,
  6-digit microseconds) so existing agentos.db files stay readable/writable.
"""

from __future__ import annotations

import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterable, Iterator, Optional, Sequence

_TS_FMT = "%Y-%m-%d %H:%M:%S.%f"


def new_id() -> str:
    return str(uuid.uuid4())


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def ts(value: Optional[datetime] = None) -> str:
    """Storage format for a datetime (aware values are converted to UTC)."""
    if value is None:
        value = utcnow()
    elif value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value.strftime(_TS_FMT)


def parse_ts(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    for fmt in (_TS_FMT, "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


def iso(value: Optional[str]) -> Optional[str]:
    """API rendering of a stored timestamp (== pydantic's naive isoformat())."""
    dt = parse_ts(value)
    return dt.isoformat() if dt else None


def iso_utc(value: Optional[str]) -> Optional[str]:
    """isoformat() of the stored instant as an aware UTC datetime."""
    dt = parse_ts(value)
    return dt.replace(tzinfo=timezone.utc).isoformat() if dt else None


class Database:
    def __init__(self, path: str):
        self.path = path
        self.conn = sqlite3.connect(path, isolation_level=None, timeout=5.0, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._depth = 0
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute("PRAGMA busy_timeout=5000")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        # journal_mode=WAL rewrites the file header, so it is only enabled (by
        # init_db) after the file was verified to be an AgentOS database.

    def enable_wal(self) -> None:
        self.conn.execute("PRAGMA journal_mode=WAL")

    def close(self) -> None:
        try:
            self.conn.close()
        except sqlite3.Error:
            pass

    # -- statements -----------------------------------------------------
    def execute(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Cursor:
        return self.conn.execute(sql, params)

    def one(self, sql: str, params: Sequence[Any] = ()) -> Optional[dict]:
        row = self.conn.execute(sql, params).fetchone()
        return dict(row) if row is not None else None

    def all(self, sql: str, params: Sequence[Any] = ()) -> list[dict]:
        return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

    def scalar(self, sql: str, params: Sequence[Any] = ()) -> Any:
        row = self.conn.execute(sql, params).fetchone()
        return row[0] if row is not None else None

    def insert(self, table: str, values: dict) -> None:
        cols = ", ".join(values)
        marks = ", ".join("?" for _ in values)
        self.conn.execute(f"INSERT INTO {table} ({cols}) VALUES ({marks})", tuple(values.values()))

    def update(self, table: str, values: dict, where: str, params: Sequence[Any] = ()) -> int:
        if not values:
            return 0
        sets = ", ".join(f"{k}=?" for k in values)
        cur = self.conn.execute(
            f"UPDATE {table} SET {sets} WHERE {where}", (*values.values(), *params)
        )
        return cur.rowcount

    # -- transactions ---------------------------------------------------
    @contextmanager
    def tx(self) -> Iterator["Database"]:
        """BEGIN IMMEDIATE ... COMMIT; nested use becomes a savepoint."""
        if self._depth == 0:
            self.conn.execute("BEGIN IMMEDIATE")
            self._depth = 1
            try:
                yield self
            except BaseException:
                self._depth = 0
                self.conn.execute("ROLLBACK")
                raise
            else:
                self._depth = 0
                self.conn.execute("COMMIT")
        else:
            name = f"sp{self._depth}"
            self._depth += 1
            self.conn.execute(f"SAVEPOINT {name}")
            try:
                yield self
            except BaseException:
                self._depth -= 1
                self.conn.execute(f"ROLLBACK TO {name}")
                self.conn.execute(f"RELEASE {name}")
                raise
            else:
                self._depth -= 1
                self.conn.execute(f"RELEASE {name}")


def in_clause(values: Iterable[Any]) -> tuple[str, tuple]:
    vals = tuple(values)
    return ",".join("?" for _ in vals) or "NULL", vals
