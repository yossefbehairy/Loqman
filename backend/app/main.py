"""
AgentOS Backend Main Application (FastAPI)
Configures lifespan events (DB initialization, Startup Crash Recovery, Default Profiles)
and mounts all REST endpoints.
"""

import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from backend.app.config import settings
from backend.app.db.session import init_db, SessionLocal
from backend.app.db.repository import AgentProfileRepository
from backend.app.core.lifecycle import run_startup_crash_recovery
from backend.app.api.routes import health, models, agent_profiles, workflows, executions, chat, missions, ceo

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("agentos")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Lifespan context manager:
    1. Initializes SQLite tables.
    2. Runs Startup Crash Recovery Policy.
    3. Seeds default 10 AI employee profiles.
    """
    logger.info("Initializing AgentOS database...")
    init_db()

    # Session for startup operations
    db = SessionLocal()
    try:
        # Run Crash Recovery Policy
        recovery_stats = run_startup_crash_recovery(db)
        logger.info(f"Startup Crash Recovery executed: {recovery_stats}")

        # Seed standard AI employee profiles
        profile_repo = AgentProfileRepository(db)
        seeded_count = profile_repo.seed_defaults()
        if seeded_count > 0:
            logger.info(f"Seeded {seeded_count} standard AI employee profile(s).")
    finally:
        db.close()

    yield

    logger.info("AgentOS backend shutting down cleanly.")


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="AgentOS: Visual Local-First Multi-Agent Workflow Engine API",
    lifespan=lifespan
)

# Configure CORS for local development
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register API Routers
app.include_router(health.router)
app.include_router(models.router)
app.include_router(agent_profiles.router)
app.include_router(workflows.router)
app.include_router(executions.router)
app.include_router(chat.router)
app.include_router(missions.router)
app.include_router(ceo.router)


@app.get("/", tags=["root"])
def root():
    return {
        "app": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "status": "online",
        "docs_url": "/docs"
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "backend.app.main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.DEBUG
    )
