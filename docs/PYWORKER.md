# Python worker (stdlib only) - full backend

`backend/pyworker/` is the complete backend as a persistent Python process speaking JSON Lines on
stdin/stdout. Standard library only (asyncio, subprocess, sqlite3, dataclasses): it replaces
FastAPI / SQLAlchemy / Pydantic / httpx. The legacy `backend/app` has been removed.

## P4 turn boundary (schema 18)

`runtime/driver.py` defines the immutable turn/session/policy/event/failure types
and `TurnDriver` protocol. `ExecuteTaskDriver` schedules existing `AgentRuntime`
calls, emits ordered admission, authoritative assistant/reasoning items, known
turn usage and one terminal. It never simulates streaming or advertises native
resume. Interruption flags the turn before awaiting the original adapter's
`abort_task(session_title)`, then cancels and joins execution; late results cannot
settle success. Terminal tombstones are bounded to 256 entries per wrapper.

`collect_turn` uses that same event callback for persistence and collection. It
replaces provisional item buffers with authoritative snapshots, selects final
assistant text by terminal item IDs, keeps reasoning separate, replaces turn
usage snapshots, and bounds characters, items and event count. Cancellation and
external timeouts shield interruption and scope cleanup before propagating.
`run_agent_task` now calls the wrapper and collector; its ledger remains the only
usage writer. The existing conversation, mission and CEO callers therefore use
the new boundary while retaining full prompts, adapter arguments, validation,
output truncation and application cancellation guards. Compatibility calls use
an 8 Mi-character collection ceiling (matching the existing bounded subprocess
budget); direct `StartTurn` calls default to 20,000 characters. Legacy adapter
timeouts and caller deadlines retain ownership of timeout policy.

`runtime/sessions.py` supplies transactional upsert/get/delete of current native
bindings. Production code does not adopt these bindings in P4. In particular,
`opencode_conversations` is retained without backfill or new writes.

Both `runs.events` and `stream.open` accept `event_format: "legacy" |
"normalised-v1"`, defaulting to `legacy`. Runtime rows store
`{run_id,status:"running",event:<DriverEvent>}` with `type="runtime"` and a
deduplicated `driver_event_id`. Legacy projection suppresses those rows; its
existing lifecycle payload construction and pipe envelope are unchanged.
SQLite IDs remain replay cursors and can now contain gaps for legacy clients.
Projection advances the internal cursor even across entirely suppressed pages.
Opted-in clients receive the enclosed event name and envelope plus all lifecycle
events. Driver completion is staged until the lifecycle transaction, which writes
the runtime terminal before the lifecycle terminal and atomically writes the
assistant message on success. Cancellation is persisted first; subsequent driver
completion is only a cleanup acknowledgement. Existing bounded subscribers,
replay and restart interruption remain in place.

`runtime/supervisor.py` is a shared launcher for P5-P7; existing adapters have not
been switched. It launches argv arrays with `shell=False`, hides Windows windows,
drains both pipes concurrently with a shared byte ceiling, and awaits exit and
reader joins. A ctypes Job Object uses `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`;
explicit cleanup terminates the job and polls its active-process count to zero
before closing the handle, with a five-second deadline.
**Assignment is immediately after `Popen`, not suspended launch**: stdlib `Popen`
does not expose the initial thread handle, so a descendant can escape in that
short interval. Once assigned, descendants belong to the job even after root
exit or worker death. Job setup failures return ownership issues and use bounded
`taskkill /T /F` fallback while the root is identifiable. Fallback cannot promise
worker-crash cleanup. Windows pipe readers use `PeekNamedPipe` before bounded
reads so cleanup can join them even if an unowned writer retains a pipe.

The existing migration recognizer accepts the new additive columns before
writing anything, takes its normal online `.pre-v18-*.bak` before upgrades, and
creates missing columns/indexes idempotently. Fresh and upgraded databases use
the following exact new DDL (migration indexes use `IF NOT EXISTS`):

```sql
CREATE TABLE runtime_sessions (
    scope_kind TEXT NOT NULL CHECK (scope_kind IN ('conversation','ceo','mission_attempt','workflow')),
    scope_id TEXT NOT NULL,
    engine TEXT NOT NULL,
    runtime_key TEXT NOT NULL,
    protocol TEXT NOT NULL,
    namespace TEXT NOT NULL,
    native_session_id TEXT NOT NULL,
    generation INTEGER NOT NULL DEFAULT 1,
    state TEXT NOT NULL DEFAULT 'unverified' CHECK (state IN ('unverified','ready','invalid')),
    model_id TEXT NOT NULL,
    provider_id TEXT,
    variant TEXT,
    agent_id TEXT,
    cwd TEXT,
    config_fingerprint TEXT,
    last_turn_id TEXT,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    PRIMARY KEY (scope_kind, scope_id, engine, runtime_key)
);
CREATE UNIQUE INDEX uq_runtime_sessions_native ON runtime_sessions
    (engine, runtime_key, namespace, protocol, native_session_id);
ALTER TABLE conversation_runs ADD COLUMN runtime_key TEXT NOT NULL DEFAULT 'default';
ALTER TABLE conversation_runs ADD COLUMN driver_protocol TEXT;
ALTER TABLE conversation_runs ADD COLUMN native_session_id TEXT;
ALTER TABLE conversation_runs ADD COLUMN native_turn_id TEXT;
ALTER TABLE conversation_runs ADD COLUMN session_generation INTEGER;
ALTER TABLE conversation_run_events ADD COLUMN driver_event_id TEXT;
CREATE UNIQUE INDEX uq_run_driver_event ON conversation_run_events
    (run_id, driver_event_id) WHERE driver_event_id IS NOT NULL;
ALTER TABLE approvals ADD COLUMN origin TEXT NOT NULL DEFAULT 'workspace';
ALTER TABLE approvals ADD COLUMN runtime_request_id TEXT;
ALTER TABLE approvals ADD COLUMN request_type TEXT;
ALTER TABLE approvals ADD COLUMN request_json TEXT;
ALTER TABLE approvals ADD COLUMN response_json TEXT;
ALTER TABLE approvals ADD COLUMN delivery_status TEXT;
CREATE UNIQUE INDEX uq_approval_runtime_request ON approvals
    (run_id, runtime_request_id) WHERE runtime_request_id IS NOT NULL;
PRAGMA user_version = 18;
```

P4 tests are in `backend/tests/pw/test_driver.py`, `test_driver_persistence.py`,
and `test_supervisor.py`; `fixtures/schema_v17.py` preserves the committed v17
schema independently of the current definitions. `test_schema_compat.py` now
expects version 18. Supervisor tests launch only `sys.executable -c` programs;
Windows job, grandchild, worker-death and taskkill tests are Windows-only.

## P5 Claude stream-json driver (feature flag)

`runtime/claude_driver.py` is a native `TurnDriver` for engine `claude-code`. It is ON by default since P8
(the chat UI renders its stream); set `AGENTOS_CLAUDE_DRIVER=wrapper` (also off/0/false) in the worker's environment
(before it starts) to route Claude conversation and mission runs through the wrapper instead. Routing lives only in `engine_registry.create_turn_driver`; when off, `ExecuteTaskDriver`
wraps `ClaudeCodeRuntime` exactly as before. CEO/Loqman calls still use the wrapper in P5.

Launch (one supervised process per turn, prompt written as one stream-json user frame on stdin):

```
claude -p --input-format stream-json --output-format stream-json --verbose --include-partial-messages
       --model <id> --tools "" --strict-mcp-config --setting-sources project --disable-slash-commands
       [--permission-prompts none] (--session-id <uuid> | --resume <id> | --no-session-persistence)
```

With scoped tools: `--tools ToolSearch --mcp-config <per-turn temp file> --allowedTools mcp__agentos_workspace__*`
(identical policy to the legacy adapter). Required flags are probed from `claude --help`; a missing one refuses to
start (`unsupported_capability`, nothing launched). Permission requests are never approved: any `can_use_tool`
control request is answered with deny.

Mapping: `system/init` -> `session.started`; `text_delta`/`thinking_delta` -> `content.delta`; assistant text ->
`item.completed` (text before a tool call is `phase=commentary`, the text after the last tool is the final reply);
`tool_use`/`tool_result`/`tool_progress` -> `item.started/updated/completed` (bounded, key-redacted previews);
`result` -> one `usage` event then the single `turn.completed`. Nested (`parent_tool_use_id`) frames, replayed user
frames and task-notification results are ignored.

Sessions: only conversation scopes persist. First turn uses `--session-id` (binding written to `runtime_sessions`
before the prompt is submitted); later turns use `--resume` from a stable AgentOS-owned cwd (Claude stores sessions per
cwd) with only the new message (`resume_prompt`, no replayed history). A session is resumed only when its
`last_turn_id` is the conversation's latest succeeded run. If Claude refuses the resume before initialising (no
`system/init` plus an explicit missing-session message) the row is marked `invalid` and ONE fresh session starts from
the full history prompt with a `session_rebuilt` notice (generation + 1). Timeouts, EOF or any error after submission
are never replayed. CEO and mission scopes get `--no-session-persistence` and a throwaway cwd.

R-C hardening: "before submission" is proven only when the process exited before the user frame was written (a short
`resume_gate_s` wait on resume turns observes a fast refusal; no write goes to a dead process). Once the frame was
written and flushed a missing-session refusal fails the turn (`session_missing`), marks the session `invalid` and the
NEXT turn starts fresh; the prompt is never sent twice. The refusal pattern matches only "no conversation found" or
"session ... not found / does not exist". The stdout hand-off queue is bounded (`MAX_QUEUE_ITEMS`); overflow fails the
turn with `output_limit` like the byte cap. Every stdin line (user frame, control responses) is written and flushed
under one lock. Every `control_request` is answered: `can_use_tool` is denied, any other subtype gets an error
response, whatever the id type. A CLI that does not advertise `--permission-prompts` is refused before launch
(`unsupported_capability`). `ANTHROPIC_API_KEY`/`ANTHROPIC_AUTH_TOKEN` reach the child only when the runtime was built
with `allow_api_key_env=True` (the registry passes the legacy runtime's setting through); no other secret is added.

Child environment (S5): explicit allowlist (`claude_child_env`: PATH, SYSTEMROOT, WINDIR, COMSPEC, PATHEXT, TEMP/TMP,
USERPROFILE, HOME*, APPDATA, LOCALAPPDATA, PROGRAMDATA, PROGRAMFILES*, OS, NUMBER_OF_PROCESSORS,
PROCESSOR_ARCHITECTURE, LANG/LC_*, CLAUDE_CONFIG_DIR, CLAUDE_CODE_GIT_BASH_PATH, certificate paths, proxies without
embedded credentials). No API keys or tokens. The legacy `ClaudeCodeRuntime` uses the same allowlist and now adds
`--setting-sources project` when the CLI advertises it. `--bare` is not used (it disables OAuth login) and
`--safe-mode` is not used (it would disable the scoped MCP server).

Interrupt: the stream-json input has no established graceful interrupt, so `interrupt` kills the owned tree (Job
Object) and settles one cancelled terminal; `exit_grace_s` bounds the wait for a clean exit after `result`
(a CLI that ignores stdin close is killed). Tests: `backend/tests/pw/test_claude_driver.py` (fake CLI only).

### Codex app-server driver (P6)

`runtime/codex_driver.py` is a native `TurnDriver` for engine `codex` over `codex app-server` (stdio JSON-RPC,
experimental protocol, schema checked with `codex app-server generate-json-schema`, codex-cli 0.167.0). It is OFF by
default: set `AGENTOS_CODEX_DRIVER=appserver` in the worker's environment to route Codex runs through it (anything else
keeps the `codex exec` adapter). One owned process per turn: `initialize`, `initialized`, `thread/start` or
`thread/resume`, `turn/start`, then notifications until `turn/completed`. Permission level is unchanged: `-c
approval_policy="never"`, `sandbox` `read-only` and `sandboxPolicy` `readOnly` on thread and turn, every approval or
question request from the server is denied with a `permission_denied` notice. Same environment allowlist
(`child_env`, `CODEX_HOME` kept, `OPENAI_API_KEY`/`CODEX_API_KEY` only with `allow_api_key_env`), same model id and
effort validation (`ultra` refused). Conversation scopes persist the thread id in `runtime_sessions` (protocol
`codex-app-server`) and send only the incremental prompt on resume when the stored thread saw the latest successful
turn; a refused `thread/resume` (missing thread, before submission) is rebuilt once from the full prompt with a
`session_rebuilt` notice. CEO/mission scopes use an ephemeral thread. Interrupt sends `turn/interrupt`, then kills the
tree after `interrupt_grace_s`. Prompts over 24000 chars are refused (the legacy adapter truncated). Tests:
`backend/tests/pw/test_codex_driver.py` (fake app-server only).

### Antigravity ACP driver (P7a)

`runtime/acp_transport.py` is the shared, engine-agnostic ACP JSON-RPC connection (newline-delimited framing with bounded
lines/stream/queue, request/response correlation with a bounded pending table, server requests, `session/update`
routing by `sessionId`, replay (`_meta.isReplay`) dropped, non-JSON stdout lines tolerated as bounded diagnostics, one
stdin write lock). `runtime/antigravity_driver.py` is the `TurnDriver` for engine `antigravity` on top of it. OFF by
default: set `AGENTOS_ANTIGRAVITY_DRIVER=acp` to route Antigravity runs through it (anything else keeps
`agy_acp_runtime`). Launch, plan-mode requirement, model verification, deny-all permission policy (every
`session/request_permission` answered `cancelled` with a `permission_denied` notice; NEW: a tool whose permission was
denied that still reports `completed` fails the turn), environment allowlist and `GEMINI_API_KEY`/`GOOGLE_API_KEY`
opt-in are the legacy adapter's: `agy acp --stdio --sandbox --include-default-customizations=false`. Resume is probed
from `initialize.agentCapabilities` (`sessionCapabilities.resume` -> `session/resume`, else `loadSession` ->
`session/load`, else none): conversation scopes persist the session id in `runtime_sessions` (protocol
`acp-antigravity`) and send only the incremental prompt on a successful resume; unsupported or explicitly missing
sessions start a new session in the same process with the full prompt and a notice, never after the prompt was written.
CEO/mission/workflow scopes stay isolated. Interrupt sends the `session/cancel` notification, then kills the tree after
`interrupt_grace_s`. Prompts over 200000 chars are refused (the legacy adapter truncated). Tests:
`backend/tests/pw/test_acp_transport.py`, `backend/tests/pw/test_antigravity_driver.py` (fake ACP agent only).

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
