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
# Terminal 1 — backend (from repo root)
backend\.venv\Scripts\python.exe -m backend.app.cli
# or: uvicorn backend.app.main:app --host 127.0.0.1 --port 8000

# Terminal 2 — frontend
cd frontend
npm run dev        # http://127.0.0.1:5173

# Desktop shell (spawns backend automatically)
npm run desktop
```

## Quality gates (must pass before PR)

```powershell
# Backend
backend\.venv\Scripts\pytest.exe backend\tests -v

# Frontend
cd frontend
npx tsc -b
npm run lint
npm run build
npx playwright test
```

## Rules

- Local-first: no public binds, no wildcard CORS, no secrets in code.
- All frontend network calls go through `src/api/client.ts`.
- Backend input validated with Pydantic; errors redacted near runtime transport.
- Keep tests deterministic (`RUNTIME_MODE=mock` in tests).
- Small focused PRs with description + test evidence.
