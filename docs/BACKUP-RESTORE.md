# Backup and restore (SQLite)

## Export from the desktop app

The desktop backup action opens a native Save dialog. Choose a destination for the SQLite snapshot. Electron copies the worker-created file and requests cleanup even if you cancel. The snapshot includes committed WAL data.

- The file contains all DB contents, including conversations and missions. It may contain secrets entered by users or recorded by older versions. Treat it as sensitive.

## Restore (offline CLI only, never HTTP)
1. Stop AgentOS completely (backend sidecar and anything else using the DB).
2. Run from the repo root with the project venv:

       backend\.venv\Scripts\python.exe -m backend.pyworker.restore_cli BACKUP.db C:\path\to\agentos.db --yes-app-is-stopped

3. The tool validates the backup (`integrity_check`, `foreign_key_check`, `user_version` 1..current, all tables present for current
   version), then takes an exclusive lock on the target (refusing if SQLite reports a conflicting lock),
   writes `agentos.db.pre-restore-<timestamp>.bak` next to the target, and copies the backup into the target inside that lock.
4. Older-schema backups are upgraded by the normal startup `init_db` (which makes its own `.pre-v*.bak`).

Limits: the target must already exist; a backup with a different page size than a WAL target is refused by SQLite (target left
unchanged). The in-use check relies on SQLite's exclusive WAL lock and is a refusal guard, not protection against starting the app
mid-restore. Delete stale `-wal`/`-shm` files manually only if the app is stopped and you know why.
