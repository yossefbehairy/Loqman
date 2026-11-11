# Backup and restore (SQLite)

## Export (HTTP)
`POST /api/backup` (requires the usual `x-agentos-token` header when the sidecar token is set) returns a single
consistent `.db` file made with SQLite's online backup API (includes committed WAL data, no `-wal`/`-shm`).
- Written to a private temp dir, integrity-checked, streamed, then deleted after the response (or on error).
- Bounded: 1 GiB max (413), one backup at a time (409). Errors are generic; no paths in responses.
- The file contains all DB contents, including conversations and missions. It may contain secrets entered by users or recorded by older versions. Treat it as sensitive.

## Restore (offline CLI only, never HTTP)
1. Stop AgentOS completely (backend sidecar and anything else using the DB).
2. Run from the repo root with the project venv:

       backend\.venv\Scripts\python.exe -m backend.app.db.restore_cli BACKUP.db C:\path\to\agentos.db --yes-app-is-stopped

3. The tool validates the backup (`integrity_check`, `foreign_key_check`, `user_version` 1..current, all tables present for current
   version), then takes an exclusive lock on the target (refusing if SQLite reports a conflicting lock),
   writes `agentos.db.pre-restore-<timestamp>.bak` next to the target, and copies the backup into the target inside that lock.
4. Older-schema backups are upgraded by the normal startup `init_db` (which makes its own `.pre-v*.bak`).

Limits: the target must already exist; a backup with a different page size than a WAL target is refused by SQLite (target left
unchanged). The in-use check relies on SQLite's exclusive WAL lock and is a refusal guard, not protection against starting the app
mid-restore. Delete stale `-wal`/`-shm` files manually only if the app is stopped and you know why.
