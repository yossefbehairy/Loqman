import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app.api.routes.ceo import _decide
from backend.app.api.routes import ceo
from backend.app.db.models import AgentProfile, PendingAction
from backend.app.db.session import Base


def test_concurrent_approval_applies_action_only_once(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'atomic.db'}", connect_args={"check_same_thread": False, "timeout": 10})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False)
    with factory() as db:
        action = PendingAction(kind="new_profile", status="pending", payload_json=json.dumps({
            "role_type": "reviewer", "name": "Reviewer", "instructions": "Review"}))
        db.add(action)
        db.commit()
        action_id = action.id
    barrier = Barrier(2)

    def approve():
        with factory() as db:
            barrier.wait()
            try:
                return _decide(action_id, True, None, db, "true").status
            except HTTPException as exc:
                return exc.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _: approve(), range(2)))
    with factory() as db:
        assert sorted(outcomes, key=str) == sorted(["approved", 409], key=str)
        assert db.query(AgentProfile).count() == 1
    engine.dispose()


def test_failed_team_action_rolls_back_partial_writes(db_session, monkeypatch):
    action = PendingAction(kind="new_team", status="pending", payload_json="{}")
    db_session.add(action)
    db_session.commit()

    def partial_then_fail(db, payload):
        db.add(AgentProfile(id="partial", name="Partial", role_type="reviewer", instructions="review"))
        db.flush()
        raise RuntimeError("failure with sensitive data")

    monkeypatch.setattr(ceo, "_apply_new_team", partial_then_fail)
    result = _decide(action.id, True, None, db_session, "true")
    assert result.status == "failed"
    assert result.decision_note == "Action failed."
    assert db_session.get(AgentProfile, "partial") is None
