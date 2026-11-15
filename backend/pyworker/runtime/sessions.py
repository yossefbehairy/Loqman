"""Current native bindings, independent of conversation and engine identities.

No production caller adopts/resumes these rows in P4. Callers may include an
upsert in their event transaction; this repository never commits on their behalf.
"""

from backend.pyworker.db.core import Database, ts

KEY = ("scope_kind", "scope_id", "engine", "runtime_key")
FIELDS = frozenset({*KEY, "protocol", "namespace", "native_session_id", "generation", "state",
    "model_id", "provider_id", "variant", "agent_id", "cwd", "config_fingerprint", "last_turn_id"})


class SessionRepository:
    def __init__(self, db: Database):
        self.db = db

    def get(self, scope_kind, scope_id, engine, runtime_key="default"):
        return self.db.one("SELECT * FROM runtime_sessions WHERE scope_kind=? AND scope_id=? AND engine=? AND runtime_key=?",
                           (scope_kind, scope_id, engine, runtime_key))

    def put(self, **values):
        if set(values) - FIELDS:
            raise ValueError("Unknown session fields")
        values.setdefault("runtime_key", "default")
        values.setdefault("generation", 1)
        values.setdefault("state", "unverified")
        now = ts()
        values = {**values, "created_at": now, "updated_at": now}
        columns = ",".join(values)
        updates = ",".join(f"{c}=excluded.{c}" for c in values if c not in {*KEY, "created_at"})
        self.db.execute(f"INSERT INTO runtime_sessions ({columns}) VALUES ({','.join('?' for _ in values)}) "
                        f"ON CONFLICT ({','.join(KEY)}) DO UPDATE SET {updates}", tuple(values.values()))
        return self.get(*(values[k] for k in KEY))

    def delete(self, scope_kind, scope_id, engine, runtime_key="default"):
        return self.db.execute("DELETE FROM runtime_sessions WHERE scope_kind=? AND scope_id=? AND engine=? AND runtime_key=?",
                               (scope_kind, scope_id, engine, runtime_key)).rowcount == 1
