# AgentOS — Desktop Application Guide (`DESKTOP.md`)

Desktop packaging for AgentOS uses **Electron** (shell + UI) and **PyInstaller** (frozen FastAPI backend as a sidecar process). No Python or Node.js installation is required on the target machine — the installer ships everything.

---

## 1. Architecture

```
AgentOS-Setup-x.x.x.exe (electron-builder · NSIS)
└── AgentOS.exe  (Electron)
    ├── app.asar
    │   ├── electron/         ← main.cjs · preload.cjs · splash.html
    │   └── dist/             ← built React frontend (vite, base "./")
    └── resources/
        └── agentos-backend.exe   ← frozen FastAPI backend (PyInstaller onefile)

Runtime:
  Electron main ──spawns──▶ agentos-backend.exe --port <free port> --data-dir <userData>
  Electron main ──polls───▶ GET http://127.0.0.1:<port>/api/health  (until ok)
  Renderer (file://) ─────▶ window.__AGENTOS_API__ = "http://127.0.0.1:<port>/api"
  SQLite data ────────────▶ %APPDATA%\AgentOS\agentos.db
```

Key properties:

| Property | Value |
|---|---|
| Renderer load path | `file://…/app.asar/dist/index.html` (packaged) or `http://127.0.0.1:5173` (dev) |
| API base in renderer | `window.__AGENTOS_API__` (exposed by `electron/preload.cjs`); falls back to `/api` in a plain browser |
| Backend port | Dynamic — a free port is picked at each launch (`net` listen on port 0) |
| Database location | `app.getPath('userData')` → `%APPDATA%\AgentOS\agentos.db` (WAL mode) |
| Backend lifecycle | Killed with its process tree (`taskkill /PID <pid> /T /F`) on quit |
| Single instance | Enforced via `app.requestSingleInstanceLock()` |

---

## 2. Development Workflow (source tree)

Prerequisites: `backend\.venv` present with `backend\requirements.txt` installed, `frontend\node_modules` installed.

Two terminals:

```powershell
# Terminal 1 — Vite dev server (frontend)
cd d:\AgentOS\frontend
npm run dev
```

```powershell
# Terminal 2 — Electron shell (spawns the backend automatically via python -m backend.app.cli)
cd d:\AgentOS\frontend
npm run desktop
```

In dev mode Electron loads `http://127.0.0.1:5173`, spawns the backend with the venv Python (`backend\.venv\Scripts\python.exe`), and the renderer talks directly to the dynamic backend port (the Vite proxy on `/api` is bypassed because `window.__AGENTOS_API__` takes precedence).

Plain-browser development (`npm run dev` alone + a manually started backend on port 8000) keeps working unchanged via the `/api` proxy fallback.

---

## 3. Production Build Pipeline

### Step 1 — Build the backend sidecar (PyInstaller)

```powershell
cd d:\AgentOS
powershell -ExecutionPolicy Bypass -File pyinstaller\build-backend.ps1
```

Output (idempotent, cleans previous artifacts):

- `backend\dist\agentos-backend.exe` (onefile, ~19.5 MB)
- copied automatically to `frontend\build-resources\agentos-backend.exe` (consumed by electron-builder `extraResources`)

Verify the frozen backend standalone:

```powershell
powershell -ExecutionPolicy Bypass -File pyinstaller\smoke-test.ps1
```

The smoke test spawns the exe on a temp port + temp data dir, polls `/api/health`, asserts `agentos.db` was created, then kills the process tree. Expected cold start: ~2–3 s.

### Step 2 — Build the frontend

```powershell
cd d:\AgentOS\frontend
npm run build        # tsc -b && vite build → dist/
```

> `vite.config.ts` sets `base: './'` so asset URLs are relative and work from `file://` inside Electron. Do not remove it.

### Step 3 — Package the installer

```powershell
cd d:\AgentOS\frontend
npm run desktop:dist          # npm run build && electron-builder --win --x64
```

Output: `frontend\release\AgentOS-Setup-<version>.exe` (NSIS, `oneClick: false` → the user picks the install directory; per-user by default).

> `electron-builder` **requires** `frontend\build-resources\agentos-backend.exe` to exist (Step 1). Packaging fails without it.

---

## 4. Runtime Contract (do not break)

**Backend CLI** (`backend/app/cli.py`):

| Flag | Description |
|---|---|
| `--port <n>` | Bind port (overrides `PORT` env / `.env`) |
| `--host <h>` | Bind host (default `127.0.0.1`) |
| `--data-dir <path>` | SQLite lives at `<path>\agentos.db` (sets `AGENTOS_DATA_DIR`, overrides `SQLITE_DB_URL`) |

Arguments are applied to `os.environ` **before** `backend.app.config` is imported (the Settings singleton and SQLAlchemy engine are created at import time).

**Health endpoint:** `GET /api/health` → `{"status":"ok","app_name":"AgentOS","version":"..."}` — used by the Electron boot splash and the smoke test.

**Preload bridge:** `electron/preload.cjs` exposes exactly two globals via `contextBridge` (context isolation ON, node integration OFF):

- `window.__AGENTOS_API__` — e.g. `http://127.0.0.1:53421/api` (includes the `/api` suffix)
- `window.__AGENTOS_DESKTOP__` — `true`

Any new frontend network call must go through `frontend/src/api/client.ts` (`API_BASE`), including `EventSource` URLs.

---

## 5. Troubleshooting

| Symptom | Cause / Fix |
|---|---|
| Splash shows "backend failed to start" | Read the last `[backend]` log lines shown on the splash. Typical: port race (relaunch), or antivirus quarantined `agentos-backend.exe` |
| Packaging fails: `file not found: build-resources\agentos-backend.exe` | Run Step 1 (PyInstaller build) first |
| Blank window in packaged app | `base: './'` missing from `vite.config.ts`, or `dist/` not rebuilt before packaging |
| CORS error in renderer console | Origin `null` (file://) must stay in `CORS_ORIGINS` (`backend/app/config.py`) |
| Missions never progress | The AI runtime (`OPENCODE_BASE_URL`, default `http://127.0.0.1:49100`) is external to the desktop package and must be running |
| Windows SmartScreen / AV warning | Unsigned build. Sign the exe / installer with a code-signing certificate before wide distribution |

---

## 6. Known Limitations (MVP)

- The AI runtime (OpenCode on `127.0.0.1:49100`) is **not** bundled — the desktop app expects it running on the same machine for mission execution. Browse/UI/data features work fully offline.
- No code signing, no auto-updater yet (planned: GitHub Releases + `electron-updater`).
- App icon: custom multi-size `frontend/build/icon.ico` (regenerate via any SVG→ICO tool if the brand changes).
- uvicorn runs with `h11` only (no `websockets` dependency installed); REST + SSE are unaffected.
