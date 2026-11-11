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
  Electron main ──spawns──▶ agentos-backend.exe --data-dir <userData>   (Python worker, stdlib only)
  Electron main ◀──ready──  first stdout line; JSON Lines over stdin/stdout (docs/PYWORKER.md)
  Renderer (file://) ─────▶ window.agentos.call(method, params) / openStream(...)  (preload → IPC → main → worker)
  SQLite data ────────────▶ %APPDATA%\AgentOS\agentos.db
```

Key properties:

| Property | Value |
|---|---|
| Renderer load path | `file://…/app.asar/dist/index.html` (packaged) or `http://127.0.0.1:5173` (dev) |
| API base in renderer | `window.__AGENTOS_API__` (exposed by `electron/preload.cjs`); falls back to `/api` in a plain browser |
| Backend transport | stdin/stdout pipes to the worker — no HTTP server, no ports, no token, no CORS |
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
# Terminal 2 — Electron shell using Vite (spawns the backend automatically)
cd d:\AgentOS\frontend
$env:AGENTOS_DEV_SERVER_URL = 'http://127.0.0.1:5173'
npm run desktop
```

With `AGENTOS_DEV_SERVER_URL` set, Electron loads the Vite server. Otherwise `npm run desktop` builds and loads the local `dist` files. Electron spawns the backend with the venv Python, creates a random local token, and sends authenticated requests to the dynamic backend port.

For plain-browser development, set `AGENTOS_INSECURE_DEV=1` in the backend terminal before starting the backend on port 8000. This explicitly disables local-token authentication for that development process.

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

- AI CLIs are **not** bundled — the user installs and signs in to each engine. OpenCode uses `127.0.0.1:49100`; Claude Code, Codex and Antigravity use their installed CLIs. Browse/UI/data features work offline, and `mock` is available for deterministic demos.
- No code signing, no auto-updater yet (planned: GitHub Releases + `electron-updater`).
- App icon: custom multi-size `frontend/build/icon.ico` (regenerate via any SVG→ICO tool if the brand changes).
- uvicorn runs with `h11` only (no `websockets` dependency installed); REST + SSE are unaffected.


> **Update:** the sections above that mention ports, `/api/health`, tokens, CORS, uvicorn and `backend/app/cli.py`
> describe the legacy FastAPI backend. The desktop app now talks to the Python worker (`backend/pyworker`,
> see `docs/PYWORKER.md`). Plain-browser dev uses the Vite dev bridge (`frontend/vite-agentos-bridge.mjs`, serve-only).
> Frozen build spec: `pyinstaller/agentos-worker.spec`.
