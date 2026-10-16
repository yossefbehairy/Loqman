"""
Database Session and Engine Management with SQLite Pragmas
Enforces WAL mode, foreign keys, and busy timeout on every connection.
"""

from typing import Generator
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import declarative_base, sessionmaker, Session
from backend.app.config import settings

# Create engine with thread-safety for SQLite
connect_args = {"check_same_thread": False} if settings.SQLITE_DB_URL.startswith("sqlite") else {}

engine = create_engine(
    settings.SQLITE_DB_URL,
    connect_args=connect_args,
    echo=settings.DEBUG
)

# Mandatory SQLite Pragmas on Connect
@event.listens_for(Engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    """
    Enforces SQLite foreign keys, WAL journal mode, and busy timeout.
    """
    if "sqlite" in str(dbapi_connection.__class__).lower():
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON;")
        cursor.execute("PRAGMA journal_mode=WAL;")
        cursor.execute("PRAGMA busy_timeout=5000;")
        cursor.execute("PRAGMA synchronous=NORMAL;")
        cursor.close()


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db() -> Generator[Session, None, None]:
    """
    FastAPI dependency yielding a database session per request.
    Ensures session closure and rollback on error.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db(target_engine=None):
    """
    Initializes all tables in the SQLite database.
    """
    eng = target_engine or engine
    Base.metadata.create_all(bind=eng)
    # SQLite create_all does not add columns to tables that already exist.
    if "missions" in inspect(eng).get_table_names():
        columns = {column["name"] for column in inspect(eng).get_columns("missions")}
        if "source_conversation_id" not in columns:
            with eng.begin() as connection:
                connection.execute(text("ALTER TABLE missions ADD COLUMN source_conversation_id VARCHAR(128)"))
                connection.execute(text(
                    "CREATE INDEX IF NOT EXISTS ix_missions_source_conversation_id "
                    "ON missions (source_conversation_id)"
                ))
    if "mission_tasks" in inspect(eng).get_table_names():
        columns = {column["name"] for column in inspect(eng).get_columns("mission_tasks")}
        if "expected_output" not in columns:
            with eng.begin() as connection:
                connection.execute(text("ALTER TABLE mission_tasks ADD COLUMN expected_output TEXT"))
