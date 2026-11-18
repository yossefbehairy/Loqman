# Contributing to Loqman

## Setup

```powershell
# Backend
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy ..\.env.example ..\.env   # adjust locally

# Frontend
cd ..\frontend
npm install
```

## Run (dev)

```powershell
# Terminal 1 — frontend (from repo root)
# (no separate backend process: `npm run dev` starts the Python worker through the dev bridge)
cd frontend
npm run dev        # http://127.0.0.1:5173

# Desktop shell (spawns backend automatically)
npm run desktop
```

## Quality gates (must pass before PR)

```powershell
# Backend
backend\.venv\Scripts\pytest.exe backend\tests -v

# Node: all Electron-layer tests
node --test "frontend/electron/*.test.cjs"

# Frontend
cd frontend
npx tsc -b
npm run lint
npm run build
npx playwright test
```

## Rules

- Local-first: no public binds, no wildcard CORS, no secrets in code.
- All frontend calls to the worker go through `call` or `openStream` in `frontend/src/api/transport.ts`.
- Backend input is validated explicitly (`backend/pyworker/validate.py`); OpenCode runtime errors pass through `_redact()` before they are reported.
- Keep tests deterministic: use the mock runtime or fake CLIs, and make no live model calls.
- Small focused PRs with description + test evidence.
