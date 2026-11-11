# Python worker (stdlib only) - full backend

`backend/pyworker/` is the complete backend as a persistent Python process speaking JSON Lines on
stdin/stdout. Standard library only (asyncio, subprocess, sqlite3, dataclasses): it replaces
FastAPI / SQLAlchemy / Pydantic / httpx. The legacy `backend/app` still exists and is untouched
until the checklist at the bottom is done.

## Run
```powershell
backend\.venv\Scripts\python.exe -m backend.pyworker --data-dir C:\path\userData      # <dir>\agentos.db (desktop)
backend\.venv\Scripts\python.exe -m backend.pyworker --db C:\path\file.db --runtime-mode mock
```
Flags: `--data-dir` or `--db`, `--runtime-mode real|mock|claude|codex|agy`, `--max-message-bytes`,
`--shutdown-timeout`, `--log-level`. Other settings come from the same env vars / `.env` as before.
Offline restore: `python -m backend.pyworker.restore_cli BACKUP TARGET --yes-app-is-stopped`.

## Protocol `agentos-worker/1` (ours; not MCP, not JSON-RPC)
stdout = protocol only, stderr = logs; one JSON object per line; the inbound size limit is enforced on raw bytes.
- request `{"id","method","params"}` -> `{"type":"response","id","result"}` or
  `{"type":"response","id","error":{"code","message","status"?,"detail"?}}`; `status`/`detail` carry the legacy HTTP
  status (404/409/422...) and `detail` payload.
- first line `{"type":"ready", protocol, runtime_mode, methods, recovery, interrupted_runs}`; a failed start sends
  `{"type":"fatal"}` and exits 2 (client-safe message, no paths).
- streams (replace SSE): `stream.open {topic: run|mission|execution, id, last_event_id?}` -> `{stream_id}`, then
  `{"type":"stream","stream_id","event","id","data"}` lines and a final `{"type":"stream_end","stream_id","reason"}`;
  `stream.close`. Run streams replay from SQLite after `last_event_id` (gap-free resume).
- Differences from HTTP: no token/CORS/ports; the `X-AgentOS-Confirm` header became param `confirm: true` on
  `ceo.approve|deny`; downloads return base64 content (`artifacts.content`) or a temp path (`backup.create`, then `backup.cleanup`).
- RPC end != run end: `runs.start_turn` answers once the run exists; observe via `runs.get`, `runs.events`, streams.

## Methods (79)
| Area | Methods |
|---|---|
| `app` | `app.info` |
| `approvals` | `approvals.capabilities`, `approvals.decide`, `approvals.list` |
| `artifacts` | `artifacts.content`, `artifacts.get`, `artifacts.list`, `artifacts.register` |
| `backup` | `backup.cleanup`, `backup.create` |
| `ceo` | `ceo.actions`, `ceo.approve`, `ceo.ask`, `ceo.ask_progress`, `ceo.deny`, `ceo.history` |
| `chat` | `chat.consult`, `chat.opencode_proxy`, `chat.session.get`, `chat.session.save` |
| `conversations` | `conversations.add_message`, `conversations.create`, `conversations.import_ceo`, `conversations.list`, `conversations.messages` |
| `deliverables` | `deliverables.content`, `deliverables.create`, `deliverables.decide`, `deliverables.get`, `deliverables.list` |
| `engines` | `engines.capabilities`, `engines.check`, `engines.models`, `engines.setup`, `engines.status` |
| `executions` | `executions.cancel`, `executions.get`, `executions.list`, `executions.messages` |
| `health` | `health` |
| `managers` | `managers.consult` |
| `missions` | `missions.cancel`, `missions.create`, `missions.delegate`, `missions.get`, `missions.list`, `missions.runs` |
| `models` | `models.free`, `models.refresh` |
| `profiles` | `profiles.create`, `profiles.delete`, `profiles.get`, `profiles.list`, `profiles.update` |
| `runs` | `runs.cancel`, `runs.events`, `runs.get`, `runs.start_turn` |
| `runtime` | `runtime.health` |
| `shutdown` | `shutdown` |
| `skills` | `skills.list`, `skills.read`, `skills.sources.delete`, `skills.sources.list`, `skills.sources.register` |
| `stream` | `stream.close`, `stream.open` |
| `usage` | `usage.events`, `usage.summary` |
| `workflows` | `workflows.create`, `workflows.delete`, `workflows.get`, `workflows.list`, `workflows.run`, `workflows.update`, `workflows.validate` |
| `workspaces` | `workspaces.create`, `workspaces.get`, `workspaces.list` |

## Data
Opens the existing `agentos.db` (schema v16; DDL verified identical to the legacy SQLAlchemy schema), backs it up
before upgrades, refuses non-AgentOS sqlite files and newer schemas. Unfinished runs become `interrupted` (never
re-run); dangling missions/workflows become `failed`.

## Clients
- **Electron** (`frontend/electron/{worker-client,main,preload}.cjs`): main spawns the worker, waits for `ready`, exposes
  `window.agentos.call/openStream` through IPC (sender + method validated, `shutdown` never exposed); quit is graceful.
- **React** (`frontend/src/api/{transport,client,opencode}.ts`): same function names as the old REST client.
- **Browser dev / Playwright**: `frontend/vite-agentos-bridge.mjs` (serve-only Vite plugin) spawns the same worker.
- **Frozen build**: `pyinstaller/agentos-worker.spec` -> `agentos-backend.exe` (verified: ready in ~1 s, runs/missions/CEO work).

## Deleting the legacy backend - checklist
Done: worker backend, Electron/React migration, Playwright e2e (43/43 via the dev bridge, mock engine), frozen exe smoke
test, and a real Electron launch (isolated `--user-data-dir`, mock engine): bridge present, `shutdown` blocked, errors keep
their status, mission + stream + CEO ask work, clean close.
Still to do:
1. Click through the real app once with your real engine (OpenCode/Claude/Codex): real engines were never run here.
2. `npm run desktop:dist` with the new exe (copy `agentos-backend.exe` built from `agentos-worker.spec` into `frontend/build-resources/`).
3. Then delete: `backend/app/`, `pyinstaller/agentos-backend.spec`, legacy tests (they import `backend.app`),
   `test_schema_compat::test_new_ddl_matches_legacy_*` / `..._written_by_legacy_backend_...`; drop fastapi/uvicorn/
   sqlalchemy/pydantic/httpx/sse-starlette from `backend/requirements.txt`; fix README/CONTRIBUTING mentions.
