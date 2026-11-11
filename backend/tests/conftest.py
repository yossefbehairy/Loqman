"""
Pytest Test Fixtures and Configuration for AgentOS Backend
Uses isolated temp-file SQLite with foreign key enforcement and FastAPI TestClient.
"""

import atexit
import os
import shutil
import tempfile

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker
from fastapi.testclient import TestClient

from backend.app.db.session import Base, get_db
from backend.app.main import app
from backend.app.runtime import set_runtime
from backend.app.runtime.mock_runtime import MockRuntime

# Isolated temp-file SQLite, NOT a shared in-memory StaticPool connection.
# The usage ledger writes from worker threads (asyncio.to_thread) while the
# event loop uses other sessions; sharing one DBAPI connection across threads
# caused random SystemError/InterfaceError and lost updates (flaky mission
# tests). Separate connections mirror production (file DB + WAL + busy timeout).
_TEST_DB_DIR = tempfile.mkdtemp(prefix="agentos_test_db_")
TEST_DATABASE_URL = "sqlite:///" + os.path.join(_TEST_DB_DIR, "test.db").replace("\\", "/")

test_engine = create_engine(
    TEST_DATABASE_URL,
    connect_args={"check_same_thread": False, "timeout": 30},
)


def _cleanup_test_database():
    test_engine.dispose()
    shutil.rmtree(_TEST_DB_DIR, ignore_errors=True)


atexit.register(_cleanup_test_database)

@event.listens_for(test_engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON;")
    cursor.execute("PRAGMA journal_mode=WAL;")
    cursor.execute("PRAGMA busy_timeout=30000;")
    cursor.close()

TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)


@pytest.fixture(autouse=True)
def _usage_ledger_test_database(monkeypatch):
    """
    Routes the usage ledger's own short-lived session factory to the isolated
    test database, so usage events recorded during any test never touch the
    real development DB and are always assertable.
    """
    from backend.app.runtime import ledger
    monkeypatch.setenv("AGENTOS_INSECURE_DEV", "1")
    monkeypatch.setattr(ledger, "session_factory", TestingSessionLocal)


@pytest.fixture
def isolate_app_lifespan(monkeypatch):
    """Keep TestClient startup/shutdown on the same temporary DB as its routes."""
    def isolate(session_factory):
        from backend.app import main
        from backend.app.api.routes import conversation_runs
        from backend.app.core.conversation_runs import ConversationRunManager

        # The fixture that owns the session factory already created the schema.
        # Never let TestClient startup migrate the developer's configured DB.
        monkeypatch.setattr(main, "init_db", lambda: None)
        monkeypatch.setattr(main, "SessionLocal", session_factory)
        monkeypatch.setattr(
            conversation_runs,
            "_manager",
            ConversationRunManager(session_factory=session_factory),
        )

    return isolate


@pytest.fixture(scope="function")
def db_session():
    """
    Creates fresh schema for every test function and rolls back.
    """
    Base.metadata.create_all(bind=test_engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=test_engine)


@pytest.fixture(scope="function")
def client(db_session, monkeypatch, isolate_app_lifespan):
    """
    FastAPI TestClient with overridden DB dependency and MockRuntime.
    """
    from backend.app.db.repository import AgentProfileRepository
    repo = AgentProfileRepository(db_session)
    repo.seed_defaults()

    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    # Force MockRuntime during API tests
    mock_rt = MockRuntime()
    set_runtime(mock_rt)

    from backend.app.api.routes.workflows import get_session_factory
    from backend.app.api.routes.missions import get_mission_session_factory
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_session_factory] = lambda: TestingSessionLocal
    app.dependency_overrides[get_mission_session_factory] = lambda: TestingSessionLocal
    isolate_app_lifespan(TestingSessionLocal)
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
