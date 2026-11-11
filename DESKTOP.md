# AgentOS — Desktop Application Guide (`DESKTOP.md`)

Desktop packaging for AgentOS uses **Electron** (shell + UI) and **PyInstaller** (frozen stdlib-only Python worker as a sidecar process). No Python or Node.js installation is required on the target machine — the installer ships everything.

---

## 1. Architecture

```
AgentOS-Setup-x.x.x.exe (electron-builder · NSIS)
└── AgentOS.exe  (Electron)
    ├── app.asar
    │   ├── electron/         ← main.cjs · preload.cjs · splash.html
    │   └── dist/             ← built React frontend (vite, base "./")
    └── resources/
        └── agentos-backend.exe   ← frozen Python worker (PyInstaller onefile)

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
| API in renderer | `window.agentos.call(method, params)` / `openStream(...)` (exposed by `electron/preload.cjs`); a plain browser uses the dev-only Vite bridge |
| Backend transport | stdin/stdout pipes to the worker — no HTTP server, no ports, no token, no CORS |
| Database location | `app.getPath('userData')` → `%APPDATA%\AgentOS\agentos.db` (WAL mode) |
| Backend lifecycle | Stopped gracefully on quit (`shutdown`, in-flight runs saved as interrupted); killed with its process tree if it lingers |
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

With `AGENTOS_DEV_SERVER_URL` set, Electron loads the Vite server. Otherwise `npm run desktop` builds and loads the local `dist` files. Electron spawns the worker with the venv Python (`python -m backend.pyworker --data-dir <userData>`) and talks to it over stdin/stdout.

For plain-browser development just run `npm run dev`: a serve-only Vite plugin (`frontend/vite-agentos-bridge.mjs`) spawns the same worker (`RUNTIME_MODE` from the environment, default mock; data in `.agentos-dev-data`).

---

## 3. Production Build Pipeline

### Step 1 — Build the backend sidecar (PyInstaller)

```powershell
cd d:\AgentOS
powershell -ExecutionPolicy Bypass -File pyinstaller\build-backend.ps1
```

Output (idempotent, cleans previous artifacts):

- `backend\dist\agentos-backend.exe` (onefile, ~11 MB)
- copied automatically to `frontend\build-resources\agentos-backend.exe` (consumed by electron-builder `extraResources`)

Verify the frozen backend standalone:

```powershell
powershell -ExecutionPolicy Bypass -File pyinstaller\smoke-test.ps1
```

The smoke test spawns the exe with a temp data dir and the mock engine, waits for the `ready` line, sends `health`, asserts `agentos.db` was created, then sends `shutdown` and checks a clean exit. Expected cold start: ~1 s.

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

**Backend CLI** (`python -m backend.pyworker` / `agentos-backend.exe`):

| Flag | Description |
|---|---|
| `--data-dir <path>` | SQLite lives at `<path>gentos.db` |
| `--db <file>` | Explicit database file (alternative to `--data-dir`) |
| `--runtime-mode real\|mock\|claude\|codex\|agy` | Overrides `RUNTIME_MODE` |

**Protocol:** JSON Lines on stdin/stdout (`agentos-worker/1`, see `docs/PYWORKER.md`). The first stdout line is `ready`; startup failures send a `fatal` line and exit with code 2. Logs go to stderr only.

**Preload bridge:** `electron/preload.cjs` exposes exactly two globals via `contextBridge` (context isolation ON, node integration OFF):

- `window.agentos` — `call(method, params)` and `openStream(topic, id, lastEventId, onMessage)`
- `window.__AGENTOS_DESKTOP__` — `true`

Any new frontend backend call must go through `frontend/src/api/client.ts` (`call` from `transport.ts`); event streams use `openStream`.

---

## 5. Troubleshooting

| Symptom | Cause / Fix |
|---|---|
| Splash shows "backend failed to start" | Read the last `[backend]` log lines shown on the splash. Typical: antivirus quarantined `agentos-backend.exe`, or a database newer than this version |
| Packaging fails: `file not found: build-resources\agentos-backend.exe` | Run Step 1 (PyInstaller build) first |
| Blank window in packaged app | `base: './'` missing from `vite.config.ts`, or `dist/` not rebuilt before packaging |
| Missions never progress | The AI runtime (`OPENCODE_BASE_URL`, default `http://127.0.0.1:49100`) is external to the desktop package and must be running |
| Windows SmartScreen / AV warning | Unsigned build. Sign the exe / installer with a code-signing certificate before wide distribution |

---

## 6. Known Limitations (MVP)

- AI CLIs are **not** bundled — the user installs and signs in to each engine. OpenCode uses `127.0.0.1:49100`; Claude Code, Codex and Antigravity use their installed CLIs. Browse/UI/data features work offline, and `mock` is available for deterministic demos.
- No code signing, no auto-updater yet (planned: GitHub Releases + `electron-updater`).
- App icon: custom multi-size `frontend/build/icon.ico` (regenerate via any SVG→ICO tool if the brand changes).
- The worker is single-process and does its (small) SQLite writes on the event loop; engine calls run as subprocesses, so the loop stays responsive.


