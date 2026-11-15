# Windows release procedure

E4 remains owner-blocked: provide a Windows code-signing certificate/private-key service and protected signing credentials for the app and NSIS installer, with timestamping. Before adding an updater, provide a trusted update publishing endpoint and configure signed-update verification. No signing credentials or updater are configured by this security fix.

Run these steps from a Windows checkout after concurrent backend and frontend edits are finished. Do not package a worker built from an earlier source state.

1. Install Python 3.13, Node.js and npm, and the tooling needed by electron-builder for Windows. The repository uses Node 22 in CI. From the repository root, prepare the backend environment and install frontend dependencies:

   ```powershell
   python -m venv backend\.venv
   backend\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt pyinstaller
   Push-Location frontend
   npm ci
   Pop-Location
   ```

   PyInstaller is a build dependency and is not listed in backend requirements. Exact PyInstaller version and signing credentials are not pinned or configured in this repository.

2. Set the same product version in `frontend/package.json` and `backend/pyworker/appsettings.py` (`APP_VERSION`). Update both root version entries in `frontend/package-lock.json` consistently, for example with `npm version <version> --no-git-tag-version` in `frontend`, and review the diff. `backend/pyworker/__init__.py` also has `WORKER_VERSION` (currently 0.1.0): it is a separate worker protocol/version identifier, not the product version; decide whether it needs changing for the worker release. Other protocol-specific version constants are separate from the product version.

3. From the repository root, build and smoke-test the worker:

   ```powershell
   powershell -ExecutionPolicy Bypass -File pyinstaller\build-backend.ps1
   powershell -ExecutionPolicy Bypass -File pyinstaller\smoke-test.ps1
   ```

   The build copies `backend\dist\agentos-backend.exe` to `frontend\build-resources\agentos-backend.exe`. The smoke test uses a fresh temporary data directory and mock mode; it prints and retains its database and backup paths. It exercises cleanup using an empty temporary directory so no database is deleted. A passing gate does not establish that cleanup deletes a populated backup.

4. With no intervening worker source changes, build the installer:

   ```powershell
   Push-Location frontend
   npm run desktop:dist
   Pop-Location
   ```

   The configured output is `frontend\release\AgentOS-Setup-<version>.exe` (Windows x64 NSIS). Check that the packaged worker is the just-tested build. Build and smoke-test again if worker source changes.

5. On a clean Windows user profile, install and launch the installer. Confirm first-run startup, create a conversation and exercise the intended configured engine, quit and relaunch, and confirm persistence. Check that the data folder is `%APPDATA%\Loqman` and contains `agentos.db`; Electron sets its name to `Loqman`. Test an upgrade over a previous installed version with an existing database, confirming data survives and works after migration. Uninstall and inspect the installed files and user data; expected uninstall data retention is not established by the repository configuration, so record the observed behavior. Test without development tools installed. Record Windows version, installer version and results.

6. The checked-in electron-builder configuration has no certificate or signing identity, and the PyInstaller spec has no signing identity; `DESKTOP.md` states code signing is not configured. Whether external environment credentials or release infrastructure sign artifacts is unknown. Verify the signature and distribution policy before publishing. The repository does not establish a publishing destination or automated release process.

7. Keep the previous installer and a verified pre-upgrade backup outside the installation directory. For rollback, stop AgentOS, retain a copy of current data, and follow the offline restore procedure in `docs/BACKUP-RESTORE.md` if a pre-upgrade database must be restored. An older worker rejects a database with a newer schema, so do not assume the previous installer can use the upgraded database. Reinstall the prior version and verify its data and operation. Distribution rollback procedures are unknown.
