"""C14: duplicates left in the "CEO history" conversation by old repeated imports are removed safely."""

from backend.pyworker.db.ceo_history_dedupe import dedupe_ceo_history
from backend.pyworker.db.core import Database
from backend.pyworker.db.migrate import init_db

T1, T2 = "2026-01-01 10:00:00", "2026-01-01 11:00:00"


def make(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    init_db(db)
    return db


def conv(db, cid, title):
    db.insert("conversations", {"id": cid, "title": title, "created_at": T1, "updated_at": T1})


def msg(db, cid, role, content, at, key=None):
    db.insert("conversation_messages", {"conversation_id": cid, "client_message_id": key,
                                        "role": role, "content": content, "created_at": at})


def contents(db, cid):
    return [(r["role"], r["content"], r["created_at"]) for r in db.all(
        "SELECT * FROM conversation_messages WHERE conversation_id=? ORDER BY seq", (cid,))]


def backups(tmp_path):
    return list(tmp_path.glob("t.db.pre-ceo-dedupe-*.bak"))


def test_duplicates_removed_distinct_kept_earliest_wins(tmp_path):
    db = make(tmp_path)
    conv(db, "h", "CEO history")
    for n in range(3):  # three restarts, each re-imported the same exchange
        msg(db, "h", "user", "hello", T1, f"ceo:a{n}")
        msg(db, "h", "assistant", "hi", T1, f"ceo:b{n}")
    msg(db, "h", "user", "hello", T2, "ceo:late")      # same content, different time: kept
    msg(db, "h", "assistant", "hello", T1, "ceo:role")  # same content, different role: kept
    first = db.scalar("SELECT MIN(seq) FROM conversation_messages WHERE content='hello' AND created_at=?", (T1,))
    assert dedupe_ceo_history(db) == 4
    assert contents(db, "h") == [("user", "hello", T1), ("assistant", "hi", T1),
                                 ("user", "hello", T2), ("assistant", "hello", T1)]
    assert db.scalar("SELECT MIN(seq) FROM conversation_messages WHERE content='hello' AND created_at=?",
                     (T1,)) == first
    assert len(backups(tmp_path)) == 1


def test_other_conversations_untouched(tmp_path):
    db = make(tmp_path)
    conv(db, "h", "CEO history")
    conv(db, "o", "Other")
    for cid in ("h", "o"):
        msg(db, cid, "user", "same", T1, f"{cid}1")
        msg(db, cid, "user", "same", T1, f"{cid}2")
    assert dedupe_ceo_history(db) == 1
    assert len(contents(db, "h")) == 1
    assert len(contents(db, "o")) == 2


def test_idempotent_second_run_and_no_extra_backup(tmp_path):
    db = make(tmp_path)
    conv(db, "h", "CEO history")
    msg(db, "h", "user", "x", T1, "a")
    msg(db, "h", "user", "x", T1, "b")
    assert dedupe_ceo_history(db) == 1
    assert dedupe_ceo_history(db) == 0
    assert len(contents(db, "h")) == 1
    assert len(backups(tmp_path)) == 1


def test_backup_holds_pre_delete_state(tmp_path):
    import sqlite3
    db = make(tmp_path)
    conv(db, "h", "CEO history")
    msg(db, "h", "user", "x", T1, "a")
    msg(db, "h", "user", "x", T1, "b")
    dedupe_ceo_history(db)
    with sqlite3.connect(backups(tmp_path)[0]) as con:
        assert con.execute("SELECT COUNT(*) FROM conversation_messages").fetchone()[0] == 2


def test_noop_without_ceo_history_or_duplicates(tmp_path):
    db = make(tmp_path)
    assert dedupe_ceo_history(db) == 0
    conv(db, "o", "Other")
    msg(db, "o", "user", "x", T1, "a")
    msg(db, "o", "user", "x", T1, "b")
    assert dedupe_ceo_history(db) == 0
    assert len(contents(db, "o")) == 2
    conv(db, "h", "CEO history")
    msg(db, "h", "user", "x", T1, "a")
    assert dedupe_ceo_history(db) == 0
    assert backups(tmp_path) == []
