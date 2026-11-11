"""
Database export API.

POST /api/backup : create a consistent SQLite online backup and stream it
                   as a download. Temp storage is removed after the response.
Protected by the existing x-agentos-token middleware (all /api/ paths).
There is deliberately NO HTTP restore endpoint (see docs/BACKUP-RESTORE.md).
"""

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.engine import Engine
from starlette.background import BackgroundTask

from backend.app.db import session as db_session
from backend.app.db.backup import (
    BackupBusy, BackupError, BackupTooLarge, cleanup_backup, create_backup,
)

router = APIRouter(prefix="/api/backup", tags=["backup"])


def get_backup_engine() -> Engine:
    return db_session.engine


@router.post("")
def download_backup(engine: Engine = Depends(get_backup_engine)):
    try:
        path, name = create_backup(engine)
    except BackupBusy:
        raise HTTPException(status_code=409, detail="A backup is already in progress")
    except BackupTooLarge:
        raise HTTPException(status_code=413, detail="Database is too large to export")
    except BackupError:
        raise HTTPException(status_code=500, detail="Backup failed")
    return FileResponse(
        path,
        media_type="application/vnd.sqlite3",
        filename=name,
        headers={"Cache-Control": "no-store"},
        background=BackgroundTask(cleanup_backup, path),
    )
