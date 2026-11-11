"""Isolated tests: own FastAPI app + own SQLite file; does not import backend.app.main."""
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from backend.app.api.routes import deliverables as routes
from backend.app.core.deliverables import (
    MAX_DELIVERABLE_BYTES, MAX_DRAFTS_PER_RUN, DeliverableManager, DraftConflict, sha256_text,
)
from backend.app.db import models
from backend.app.db.models import (
    Approval, Conversation, ConversationMessage, ConversationRun, DeliverableDraft, Workspace,
)
from backend.app.db.session import Base

REPLY = "# Report\n\nExact assistant output — ünïcode ✓\n"


@pytest.fixture
def env(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 't.db'}",
                           connect_args={"check_same_thread": False, "timeout": 30})

    @event.listens_for(engine, "connect")
    def _fk(conn, _):
        conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    manager = DeliverableManager(Session)
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[routes.get_deliverable_manager] = lambda: manager
    yield TestClient(app), Session, manager, tmp_path
    engine.dispose()


def mk_run(Session, reply=REPLY, status="succeeded", with_reply=True, workspace=True):
    with Session() as s:
        ws_id = None
        if workspace:
            ws = Workspace(name="w", path=f"/tmp/ws-{datetime.now().timestamp()}")
            s.add(ws)
            s.flush()
            ws_id = ws.id
        conv = Conversation(title="c", workspace_id=ws_id)
        s.add(conv)
        s.flush()
        user = ConversationMessage(conversation_id=conv.id, role="user", content="make a report")
        s.add(user)
        s.flush()
        seq = None
        if with_reply:
            a = ConversationMessage(conversation_id=conv.id, role="assistant", content=reply)
            s.add(a)
            s.flush()
            seq = a.seq
        run = ConversationRun(
            conversation_id=conv.id, engine="mock", model_id="m1", variant="v", status=status,
            user_message_seq=user.seq, assistant_message_seq=seq,
            input_files_json=json.dumps([{"path": "a.txt", "text": "SECRET-BODY", "sha256": "ab" * 32, "chars": 11}]),
            created_at=datetime.now(timezone.utc), finished_at=datetime.now(timezone.utc))
        s.add(run)
        s.commit()
        return run.id, conv.id, ws_id, seq


def url(run_id, draft_id=None, tail=""):
    base = f"/api/conversation-runs/{run_id}/deliverables"
    return base + (f"/{draft_id}" if draft_id else "") + tail


def create(client, run_id, **body):
    r = client.post(url(run_id), json=body)
    assert r.status_code == 201, r.text
    return r.json()


def decide(client, run_id, d, decision="approve", sha=None, **extra):
    return client.post(url(run_id, d["id"], "/decision"),
                       json={"decision": decision, "content_sha256": sha or d["content_sha256"], **extra})


# ---- creation / provenance -------------------------------------------------

def test_draft_uses_actual_assistant_reply_and_factual_provenance(env):
    client, Session, _, _ = env
    run_id, conv_id, ws_id, seq = mk_run(Session)
    d = create(client, run_id)
    assert d["status"] == "pending_review"
    assert d["content_sha256"] == sha256_text(REPLY)
    assert d["size_bytes"] == len(REPLY.encode("utf-8"))
    assert d["written_to_workspace"] is False and d["downloadable"] is False
    assert "content" not in d or d["content"] is None  # list/create omit text
    p = d["provenance"]
    assert (p["run_id"], p["conversation_id"], p["workspace_id"]) == (run_id, conv_id, ws_id)
    assert (p["assistant_message_seq"], p["engine"], p["model_id"], p["variant"]) == (seq, "mock", "m1", "v")
    assert p["model_called_during_draft_creation"] is False
    # Input files appear as metadata only: never their text.
    assert p["input_files"][0]["sha256"] == "ab" * 32
    assert "SECRET-BODY" not in json.dumps(p)
    full = client.get(url(run_id, d["id"])).json()
    assert full["content"] == REPLY


def test_client_cannot_supply_content_or_unknown_fields(env):
    client, Session, *_ = env
    run_id, *_ = mk_run(Session)
    r = client.post(url(run_id), json={"content": "forged"})
    assert r.status_code == 422
    with Session() as s:
        assert s.query(DeliverableDraft).count() == 0


@pytest.mark.parametrize("status", ["queued", "running", "failed", "cancelled", "interrupted"])
def test_only_succeeded_runs_produce_drafts(env, status):
    client, Session, *_ = env
    run_id, *_ = mk_run(Session, status=status, with_reply=False)
    assert client.post(url(run_id), json={}).status_code == 409


def test_missing_run_missing_reply_and_empty_reply(env):
    client, Session, *_ = env
    assert client.post(url("nope"), json={}).status_code == 404
    assert client.get(url("nope")).status_code == 404
    run_id, *_ = mk_run(Session, with_reply=False)
    assert client.post(url(run_id), json={}).status_code == 409
    run_id, *_ = mk_run(Session, reply="   \n")
    assert client.post(url(run_id), json={}).status_code == 422


def test_size_bound_is_in_bytes(env):
    client, Session, *_ = env
    run_id, *_ = mk_run(Session, reply="é" * (MAX_DELIVERABLE_BYTES // 2 + 1))  # 2 bytes each
    assert client.post(url(run_id), json={}).status_code == 413
    run_id, *_ = mk_run(Session, reply="é" * (MAX_DELIVERABLE_BYTES // 2))
    assert client.post(url(run_id), json={}).status_code == 201


def test_create_is_idempotent_and_draft_count_bounded(env):
    client, Session, *_ = env
    run_id, *_ = mk_run(Session)
    a, b = create(client, run_id), create(client, run_id)
    assert a["id"] == b["id"]
    for i in range(MAX_DRAFTS_PER_RUN - 1):
        create(client, run_id, filename=f"f{i}.md")
    assert client.post(url(run_id), json={"filename": "overflow.md"}).status_code == 429


@pytest.mark.parametrize("requested,expected_prefix", [
    ("../../etc/passwd", "deliverable-"), ("C:\\Windows\\evil.exe", "deliverable-"),
    ("notes.md", "notes.md"), ("a/b/notes.txt", "notes.txt"), ("we ird\"name.md", "we_ird_name.md"),
    (".md", "deliverable-"),
])
def test_filename_is_sanitized_basename(env, requested, expected_prefix):
    client, Session, *_ = env
    run_id, *_ = mk_run(Session)
    name = create(client, run_id, filename=requested)["filename"]
    assert name.startswith(expected_prefix)
    assert "/" not in name and "\\" not in name and '"' not in name


def test_concurrent_creates_yield_one_draft(env):
    _, Session, manager, _ = env
    run_id, *_ = mk_run(Session)
    with ThreadPoolExecutor(8) as pool:
        ids = {r["id"] for r in pool.map(lambda _: manager.create(run_id), range(8))}
    assert len(ids) == 1
    with Session() as s:
        assert s.query(DeliverableDraft).count() == 1


# ---- approval / rejection / replay ----------------------------------------

def test_approve_then_download_exact_bytes(env):
    client, Session, *_ = env
    run_id, *_ = mk_run(Session)
    d = create(client, run_id, filename="r.md")
    assert client.get(url(run_id, d["id"], "/content")).status_code == 409  # not yet approved
    r = decide(client, run_id, d, note="ok")
    assert r.status_code == 200 and r.json()["status"] == "approved"
    assert r.json()["downloadable"] is True and r.json()["written_to_workspace"] is False
    dl = client.get(url(run_id, d["id"], "/content"))
    assert dl.status_code == 200
    assert dl.content == REPLY.encode("utf-8")
    assert dl.headers["x-content-sha256"] == d["content_sha256"]
    assert dl.headers["content-disposition"] == 'attachment; filename="r.md"'
    assert dl.headers["x-content-type-options"] == "nosniff"
    assert dl.headers["content-type"].startswith("text/markdown")


def test_reject_blocks_download_and_cannot_be_flipped(env):
    client, Session, *_ = env
    run_id, *_ = mk_run(Session)
    d = create(client, run_id)
    assert decide(client, run_id, d, "reject").json()["status"] == "rejected"
    assert client.get(url(run_id, d["id"], "/content")).status_code == 409
    assert decide(client, run_id, d, "approve").status_code == 409  # replay / flip
    assert client.get(url(run_id, d["id"])).json()["status"] == "rejected"
    # Re-requesting identical content does not reopen a rejected draft.
    again = create(client, run_id)
    assert again["id"] == d["id"] and again["status"] == "rejected"


def test_duplicate_decision_is_conflict_and_changes_nothing(env):
    client, Session, *_ = env
    run_id, *_ = mk_run(Session)
    d = create(client, run_id)
    first = decide(client, run_id, d).json()
    assert decide(client, run_id, d).status_code == 409
    assert decide(client, run_id, d, "reject").status_code == 409
    again = client.get(url(run_id, d["id"])).json()
    assert again["status"] == "approved" and again["decided_at"] == first["decided_at"]


def test_hash_mismatch_refused_and_draft_stays_pending(env):
    client, Session, *_ = env
    run_id, *_ = mk_run(Session)
    d = create(client, run_id)
    assert decide(client, run_id, d, sha="0" * 64).status_code == 409
    assert decide(client, run_id, d, sha="xyz").status_code == 422
    assert client.get(url(run_id, d["id"])).json()["status"] == "pending_review"
    assert decide(client, run_id, d, sha=d["content_sha256"].upper()).status_code == 200


def test_concurrent_decisions_exactly_one_wins(env):
    _, Session, manager, _ = env
    run_id, *_ = mk_run(Session)
    d = manager.create(run_id)

    def attempt(i):
        try:
            manager.decide(run_id, d["id"], "approve" if i % 2 else "reject", d["content_sha256"])
            return "won"
        except DraftConflict as exc:
            return exc.reason

    with ThreadPoolExecutor(10) as pool:
        results = list(pool.map(attempt, range(10)))
    assert results.count("won") == 1
    assert results.count("already_decided") == 9
    with Session() as s:
        row = s.get(DeliverableDraft, d["id"])
        assert row.status in ("approved", "rejected") and row.decided_at is not None


# ---- access / scoping ------------------------------------------------------

def test_draft_is_scoped_to_its_run(env):
    client, Session, *_ = env
    run_a, *_ = mk_run(Session)
    run_b, *_ = mk_run(Session)
    d = create(client, run_a)
    assert client.get(url(run_b, d["id"])).status_code == 404
    assert decide(client, run_b, d).status_code == 404
    assert client.get(url(run_b, d["id"], "/content")).status_code == 404
    assert client.get(url(run_b)).json() == []
    assert client.get(url(run_a, d["id"])).json()["status"] == "pending_review"
    assert client.get(url(run_a, "missing")).status_code == 404


def test_not_an_in_flight_approval_and_survives_terminal_run(env):
    """Reviewing happens after the run ended; must not use or create Approval rows."""
    client, Session, *_ = env
    run_id, *_ = mk_run(Session)
    d = create(client, run_id)
    assert decide(client, run_id, d).status_code == 200
    with Session() as s:
        assert s.query(Approval).count() == 0


def test_tampered_stored_content_is_not_served(env):
    client, Session, *_ = env
    run_id, *_ = mk_run(Session)
    d = create(client, run_id)
    decide(client, run_id, d)
    with Session() as s:
        s.get(DeliverableDraft, d["id"]).content = "tampered"
        s.commit()
    assert client.get(url(run_id, d["id"], "/content")).status_code == 500


def test_snapshot_survives_later_message_change(env):
    client, Session, *_ = env
    run_id, _, _, seq = mk_run(Session)
    d = create(client, run_id)
    with Session() as s:
        s.get(ConversationMessage, seq).content = "changed later"
        s.commit()
    decide(client, run_id, d)
    assert client.get(url(run_id, d["id"], "/content")).content == REPLY.encode("utf-8")


def test_never_writes_to_filesystem(env, monkeypatch):
    client, Session, _, tmp_path = env
    import builtins
    opened = []
    real_open = builtins.open

    def spy(file, mode="r", *a, **k):
        if any(c in mode for c in "wax+"):
            opened.append(str(file))
        return real_open(file, mode, *a, **k)

    run_id, *_ = mk_run(Session)
    before = {p for p in tmp_path.rglob("*") if p.name != "t.db" and not p.name.startswith("t.db-")}
    monkeypatch.setattr(builtins, "open", spy)
    d = create(client, run_id)
    decide(client, run_id, d)
    client.get(url(run_id, d["id"], "/content"))
    monkeypatch.undo()
    assert opened == []
    after = {p for p in tmp_path.rglob("*") if p.name != "t.db" and not p.name.startswith("t.db-")}
    assert after == before


def test_deleting_run_cascades_drafts(env):
    client, Session, *_ = env
    run_id, *_ = mk_run(Session)
    create(client, run_id)
    with Session() as s:
        s.delete(s.get(ConversationRun, run_id))
        s.commit()
        assert s.query(DeliverableDraft).count() == 0
