# Python worker (stdlib only) - full backend

`backend/pyworker/` is the complete backend as a persistent Python process speaking JSON Lines on
stdin/stdout. Standard library only (asyncio, subprocess, sqlite3, dataclasses): it replaces
FastAPI / SQLAlchemy / Pydantic / httpx. The legacy `backend/app` has been removed.

## Run
```powershell
backend\.venv\Scripts\python.exe -m backend.pyworker --data-dir C:\path\userData      # <dir>\agentos.db (desktop)
backend\.venv\Scripts\python.exe -m backend.pyworker --db C:\path\file.db --runtime-mode mock
```
Flags: `--data-dir` or `--db`, `--runtime-mode real|mock` (`RUNTIME_MODE=claude|codex|agy` was removed: choose the default provider in Settings; a leftover value exits with a clear message), `--max-message-bytes`,
`--shutdown-timeout`, `--log-level`. Other settings come from the same env vars / `.env` as before.
Offline restore: `python -m backend.pyworker.restore_cli BACKUP TARGET --yes-app-is-stopped`.

## Protocol `agentos-worker/1` (ours; not MCP, not JSON-RPC)
stdout = protocol only, stderr = logs; one JSON object per line; the inbound size limit is enforced on raw bytes.
- request `{"id","method","params"}` -> `{"type":"response","id","result"}` or
  `{"type":"response","id","error":{"code","message","status"?,"detail"?}}`; `status`/`detail` carry the legacy HTTP
  status (404/409/422...) and `detail` payload.
- first line `{"type":"ready", protocol, runtime_mode, methods, recovery, interrupted_runs}`; a failed start sends
  `{"type":"fatal"}` and exits 2 (client-safe message, no paths).
- streams (replace SSE): `stream.open {topic: run|mission, id, last_event_id?}` -> `{stream_id}`, then
  `{"type":"stream","stream_id","event","id","data"}` lines and a final `{"type":"stream_end","stream_id","reason"}`;
  `stream.close`. Run streams replay from SQLite after `last_event_id` (gap-free resume).
  Mission streams send the current mission/task snapshot, then live events, and close automatically on
  terminal state. Only run streams accept a positive cursor. Reopening a mission starts a fresh snapshot
  with null event IDs; clients replace mission state.
  The Electron and development bridges register stream ownership synchronously on the open response,
  before processing subsequent event lines.
- Differences from HTTP: no token/CORS/ports; the `X-AgentOS-Confirm` header became param `confirm: true` on
  `ceo.approve|deny`; downloads return base64 content (`artifacts.content`) or a temp path (`backup.create`, then `backup.cleanup`).
- RPC end != run end: `runs.start_turn` answers once the run exists; observe via `runs.get`, `runs.events`, streams.

## Run-scoped tools

`runs.start_turn` accepts `tool_mode` with `none` (default), `read`, `write`,
`browser`, `computer`, or `full`. Tool modes require the Claude adapter and a
conversation with a registered workspace. Configuration is frozen on the run.
Claude receives the stdio `agentos_workspace` server. Its authenticated loopback
broker revokes access when the run ends. Native tools are limited to discovery.

- `workspace_read` returns text pages, SHA-256, and `next_offset`. Completion
  checks that sources were fully read and their hashes still match.
- `workspace_write` proposes a new UTF-8 report in the workspace root.
  `runs.write_proposals` returns exact content and hash for review.
  `approvals.decide` releases the waiting tool. Publication refuses overwrites,
  reopens the report for verification and journals recovery after interruption.
- Browser tools provide open, snapshot, click, fill and wait. They use an installed
  Chromium/Chrome/Edge with a fresh temporary profile.
  Snapshots support text/element offsets and next-page cursors. Screenshots return JPEG images.
- `computer.windows` lists visible Windows windows. Pass a selected handle as
  `computer_window` in computer/full mode to expose screenshot, focus, click and
  text input. The grant pins the window's process identity.
- `runs.tool_control {run_id, owner: user|agent}` controls browser/computer ownership.
  Browser element refs are invalidated on transfer.
- `runs.steer {run_id, correction}` cancels the old attempt and starts a corrected
  turn with the original task and correction. Repeating the same correction
  returns the same new run. Pending approvals from the old attempt expire.

Tool calls have count limits and repeated failure detection. Evidence and approval
events are persisted. CLI stdout/stderr share an 8 MiB limit per process.
The worker uses stdlib only. Chromium and model CLIs are external programs.
`conversations.create` accepts `profile_id`. Instructions and output schemas are
frozen at turn start. Schemas support types, properties, required, enums, items,
additional properties and length/numeric bounds.

## Standalone workspace MCP

`python -m backend.pyworker.mcp_workspace --workspace <absolute-directory> --allow-read`
starts a separate, standard-library-only MCP stdio server. It negotiates protocol
version `2025-06-18` and offers one read-only UTF-8 text tool, `workspace_read`.
Without `--allow-read` no tool is exposed; the grant lasts for that process.
Inputs are bounded and the existing workspace containment and opened-handle
checks apply. File contents are untrusted data. This is a process-level grant,
not OS sandboxing or a per-run approval, and files accessible through hard links
are not distinguished by their origin. This standalone command retains a process
grant. Run-scoped sessions use the authenticated broker above. MCP tools follow
[the protocol specification](https://modelcontextprotocol.io/specification/2025-06-18/basic/lifecycle).

`core.run_tools.RunWorkspaceTools` pins the conversation and workspace and denies
reads once the run ends or the binding changes. File I/O runs off the event-loop
thread and the binding is checked again before returning the result.

## Methods
| Area | Methods |
|---|---|
| `approvals` | `approvals.capabilities`, `approvals.decide`, `approvals.list` |
| `artifacts` | `artifacts.content`, `artifacts.get`, `artifacts.list`, `artifacts.register` |
| `backup` | `backup.cleanup`, `backup.create` |
| `ceo` | `ceo.actions`, `ceo.approve`, `ceo.ask`, `ceo.ask_progress`, `ceo.deny` |
| `conversations` | `conversations.add_message`, `conversations.bind_profile`, `conversations.create`, `conversations.import_ceo`, `conversations.list`, `conversations.messages` |
| `computer` | `computer.windows` |
| `deliverables` | `deliverables.content`, `deliverables.create`, `deliverables.decide`, `deliverables.get`, `deliverables.list`, `deliverables.list_all` |
| `engines` | `engines.capabilities`, `engines.check`, `engines.models`, `engines.open_setup`, `engines.setup`, `engines.status`, `engines.test` |
| `health` | `health` |
| `missions` | `missions.cancel`, `missions.create`, `missions.delegate`, `missions.get`, `missions.list`, `missions.runs` |
| `models` | `models.free` |
| `profiles` | `profiles.create`, `profiles.delete`, `profiles.get`, `profiles.list`, `profiles.update` |
| `runs` | `runs.cancel`, `runs.events`, `runs.get`, `runs.list`, `runs.start_turn`, `runs.write_proposals`, `runs.tool_control`, `runs.steer` |
| `shutdown` | `shutdown` |
| `skills` | `skills.list`, `skills.read`, `skills.sources.delete`, `skills.sources.list`, `skills.sources.register` |
| `stream` | `stream.close`, `stream.open` |
| `usage` | `usage.events`, `usage.summary` |
| `workspaces` | `workspaces.create`, `workspaces.get`, `workspaces.list` |

Engine notes: all four engines are defined once in `runtime/providers.py`. `engines.status` takes optional `force` (discovery is cached 30 s per app) and adds `state` (`ready`/`needs_login`/`unverified`/`not_installed`/`error`), `title`, `vendor`, `checked_at`. `engines.setup` adds `state`, `install`, `login`, `docs_url`. `engines.test` runs one fixed-prompt generation (60 s limit; it accepts no prompt). `engines.open_setup` (`step`: `install`/`login`) opens a Windows terminal with the catalog command. `usage.summary` also returns `by_engine`.

- `ceo.ask`: mock mode rejects explicit real engines.
- `ceo.actions`: hides an active ask's agent and team proposals until auto-application finishes.
- `missions.create`: captures the default engine identity for the mission run; interrupted missions fail on restart.
- `missions.delegate`: mock mode rejects profiles with real engines.
- `runs.start_turn`: mock mode accepts only the mock engine.
- `engines.test`: mock mode blocks generation; otherwise an omitted model selects a low-cost preference or the last catalog entry.
- `approvals.capabilities`: engine IDs come from the provider registry.

## Data
Opens the existing `agentos.db` and upgrades to schema v17, backs it up
before upgrades, refuses non-AgentOS sqlite files and newer schemas. Unfinished runs become `interrupted` (never
re-run); dangling missions become `failed`. The tables of the removed workflow engine and OpenCode session mapping (`workflows`, `workflow_executions`, `node_executions`, `execution_events`, `opencode_conversations`) are kept for existing databases and no longer written; backup and restore carry them.

## Clients
- **Electron** (`frontend/electron/{worker-client,main,preload}.cjs`): main spawns the worker, waits for `ready`, exposes
  `window.agentos.call/openStream` through IPC (sender + method validated, `shutdown` never exposed); quit is graceful.
- **React** (`frontend/src/api/transport.ts`): `call` and `openStream`. Typed wrappers for the methods live in `frontend/src/rusebot/data/worker.ts`.
- **Browser dev / Playwright**: `frontend/vite-agentos-bridge.mjs` (serve-only Vite plugin) spawns the same worker.
- **Frozen build**: `pyinstaller/agentos-worker.spec` -> `agentos-backend.exe`. An earlier build reached `ready` in about 1 s; this was not re-measured for this update.

## Status
The legacy FastAPI backend (`backend/app`), its tests, its PyInstaller spec and the framework dependencies are removed.
`backend/requirements.txt` only lists pytest and pytest-asyncio. Live Claude checks
read a generated file, compare CSV inputs and publish an approved Arabic report,
and extract a generated code through the isolated browser. Native Windows tests
cover test-window capture, revoked control and stale handles. Input compatibility
across Windows applications, other engines' MCP adapters and a fresh Windows
account remain unverified.

Test counts at the time of writing (static counts of test definitions, not run results): 304 test functions in `backend/tests` and 11 Node tests in `frontend/electron`. Earlier revisions of this section recorded a live Claude acceptance run and a frozen-executable run; those were not repeated for this update.
