"""
Pytest Test Fixtures and Configuration for AgentOS Backend
Uses in-memory SQLite with foreign key enforcement and FastAPI TestClient.
"""

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

from backend.app.db.session import Base, get_db
from backend.app.main import app
from backend.app.runtime import set_runtime
from backend.app.runtime.mock_runtime import MockRuntime

TEST_DATABASE_URL = "sqlite:///:memory:"

test_engine = create_engine(
    TEST_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool
)

# Enforce foreign keys in in-memory SQLite
@event.listens_for(test_engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON;")
    cursor.close()

TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)


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
def client(db_session):
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
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
