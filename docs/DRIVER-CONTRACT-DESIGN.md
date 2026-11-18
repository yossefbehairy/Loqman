# Design: turn-and-event drivers for AgentOS

**Status:** proposal based on read-only inspection. No files changed; no engine CLIs, tests, or model calls executed.

References use **A** for `D:\AgentOS` and **R** for `D:\rusebot`; linked paths point to the inspected files and line numbers. “Proposed” identifies new behaviour rather than an existing protocol guarantee.

The replacement should preserve AgentOS’s durable run manager and introduce drivers underneath it. Today, `AgentRuntime` provides whole-result execution and abort, while `AgentResult` already carries text, reasoning, usage, duration, and a session ID. RuseBot separates turn admission, interruption, approval responses, events, and shutdown. Those are the useful boundaries to adopt. [A: base.py:67–88](D:/AgentOS/backend/pyworker/runtime/base.py:67), [R: contracts.ts:154–197](D:/rusebot/server/contracts.ts:154)

## 1. Python driver Protocol

### Ownership and identity

Keep these identities distinct:

| Identity | Owner | Meaning |
|---|---|---|
| `conversation_id` | AgentOS | User-visible conversation, where applicable. |
| `scope_kind`, `scope_id` | AgentOS | Stable context owner: conversation, CEO thread, or isolated mission attempt. |
| `run_id` / logical `turn_id` | AgentOS | One authorised execution; use the run UUID as the logical turn ID. |
| `native_session_id` | Engine | Claude session, Codex thread, or ACP session. |
| `native_turn_id` | Engine | Present where the protocol supplies one, particularly Codex. |
| `process_generation` | Driver | Identifies one launch; rejects callbacks from retired processes. |

AgentOS currently uses `session_title` as an in-flight cancellation key, including `conv-run-{run_id}`. It must cease doubling as durable conversation identity. [A: conversation_runs.py:162–164](D:/AgentOS/backend/pyworker/core/conversation_runs.py:162)

### Proposed public types

The following is interface notation only. `Protocol`, `Literal`, `Callable`, `Awaitable`, and dataclass helpers are standard-library imports. Reuse AgentOS’s existing `ModelCatalogEntry` and `RuntimeHealth` structures for compatibility. [A: base.py:9–50](D:/AgentOS/backend/pyworker/runtime/base.py:9)

```python
@dataclass(frozen=True, slots=True)
class SessionRef:
    native_session_id: str
    protocol: Literal["claude-stream-json", "codex-app-server",
                      "acp-load", "acp-resume", "opencode-legacy"]
    namespace: str
    generation: int

@dataclass(frozen=True, slots=True)
class ToolPolicy:
    mode: Literal["none", "scoped", "native"]
    approval_mode: Literal["deny", "ask"]
    allowed_tools: tuple[str, ...] = ()
    mcp_servers: dict[str, object] = field(default_factory=dict)

@dataclass(frozen=True, slots=True)
class StartTurn:
    turn_id: str
    run_id: str
    scope_kind: Literal["conversation", "ceo", "mission_attempt", "workflow"]
    scope_id: str
    prompt: str
    model_id: str
    cwd: str
    tool_policy: ToolPolicy
    conversation_id: str | None = None
    provider_id: str | None = None
    agent_id: str | None = None
    variant: str | None = None
    effort: str | None = None
    system: str | None = None
    session: SessionRef | None = None
    session_reset: bool = False
    timeout_s: float = 90.0
    max_output_chars: int = 20000

@dataclass(frozen=True, slots=True)
class TurnRef:
    turn_id: str
    run_id: str

@dataclass(frozen=True, slots=True)
class ApprovalAnswer:
    behavior: Literal["allow", "deny", "answer"]
    message: str | None = None
    answers: dict[str, tuple[str, ...]] = field(default_factory=dict)
    remember_for_session: bool = False

@dataclass(frozen=True, slots=True)
class DriverCapabilities:
    streaming_text: bool
    tool_events: bool
    permission_requests: bool
    question_requests: bool
    native_resume: Literal["none", "load", "resume", "cli-resume"]
    model_switch: Literal["in-session", "relaunch", "unsupported"]
    scoped_mcp: bool
    effort_levels: tuple[str, ...] = ()
    model_variants: bool = False

@dataclass(frozen=True, slots=True)
class DriverFailure:
    category: str
    message: str
    retryable: bool
    phase: Literal["launch", "handshake", "resume", "prompt", "running", "cleanup"]
    acceptance: Literal["not-submitted", "submitted", "output-seen", "unknown"]
    native_code: str | int | None = None
    retry_after_s: float | None = None

@dataclass(frozen=True, slots=True)
class DriverEvent:
    version: int
    event_id: str
    turn_id: str
    run_id: str
    seq: int
    engine: str
    runtime_key: str
    created_at: str
    type: str
    data: dict[str, object]
    native_session_id: str | None = None
    native_turn_id: str | None = None
    item_id: str | None = None
    request_id: str | None = None

@dataclass(frozen=True, slots=True)
class CleanupIssue:
    process_generation: str
    pid: int | None
    message: str

class TurnDriver(Protocol):
    engine: str
    runtime_key: str
    capabilities: DriverCapabilities

    async def start_turn(
        self, turn: StartTurn,
        emit: Callable[[DriverEvent], Awaitable[None]],
    ) -> TurnRef: ...

    async def interrupt(
        self, turn_id: str,
    ) -> Literal["requested", "already-terminal", "unknown"]: ...

    async def respond(
        self, turn_id: str, request_id: str, answer: ApprovalAnswer,
    ) -> Literal["allowed-once", "allowed-session", "rejected",
                 "answered", "unavailable"]: ...

    async def close(
        self, scope_kind: str, scope_id: str,
    ) -> tuple[CleanupIssue, ...]: ...

    async def stop_all(self) -> tuple[CleanupIssue, ...]: ...

    async def check_health(self) -> RuntimeHealth: ...

    async def list_available_models(
        self, free_only: bool = True, *, refresh: bool = False,
    ) -> list[ModelCatalogEntry]: ...
```

Do not expose native JSON-RPC IDs through `respond()`. Maintain an internal mapping from the AgentOS request UUID to the native request ID, session, logical turn, and process generation.

The response outcomes follow RuseBot’s useful distinction between an answer that was delivered and a request that is no longer available. The additional `allowed-session` outcome avoids reporting remembered grants as one-shot grants. [R: contracts.ts:85](D:/rusebot/server/contracts.ts:85), [R: contracts.ts:185–193](D:/rusebot/server/contracts.ts:185)

### Required guarantees

These are proposed AgentOS guarantees, stronger and more uniform than the individual engines:

1. **Admission is separate from native acceptance.** Validate arguments, acquire the per-scope lock, register cancellation, and schedule the turn before returning `TurnRef`. `turn.started` means the driver accepted responsibility; it does not prove that a model received the prompt.
2. **No event-subscription race.** The sink is supplied to `start_turn`, and IDs are caller-assigned. Events may arrive before that method returns.
3. **One active turn per native session.** Reject concurrent admission as `busy`; queueing belongs to the run manager. AgentOS already enforces one active conversation run with a partial unique index. [A: schema.py:97](D:/AgentOS/backend/pyworker/db/schema.py:97)
4. **Ordered delivery.** Assign contiguous `seq` values from 1 per logical turn. Await sink acceptance in order. All item, request, usage, and error events precede the terminal event.
5. **Exactly one terminal event per admitted turn:** `turn.completed`. Errors are descriptive events, not additional terminals. Duplicate finish frames, EOF, timeout, and cancellation race through one settlement gate.
6. **Crash qualification.** Exactly-once delivery cannot span an abrupt worker death. The durable manager reconciles unfinished runs to `interrupted`, as it already does, without re-executing them. [A: conversation_runs.py:265–279](D:/AgentOS/backend/pyworker/core/conversation_runs.py:265)
7. **Idempotent interrupt.** Repeated calls join the same stop operation. Register a stop flag before spawning; cancellation during handshake must prevent later prompt submission.
8. **Terminal immutability.** Ignore late output from the settled turn or retired generation. Retain AgentOS’s conditional status updates so late completion cannot overwrite cancellation. [A: conversation_runs.py:484–507](D:/AgentOS/backend/pyworker/core/conversation_runs.py:484), [A: conversation_runs.py:603–619](D:/AgentOS/backend/pyworker/core/conversation_runs.py:603)
9. **Requests settle once.** Validate answer kind and options; remove the pending request atomically. On cancellation, close, or expiry, deny/cancel unresolved native requests before terminal delivery.
10. **No hidden replay.** Once prompt submission is uncertain, do not automatically resend a potentially side-effecting turn.
11. **Bounded resources.** Bound protocol lines, diagnostics, pending RPCs, event staging, and accumulated text. Overflow produces an explicit failure; never silently discard assistant text.
12. **Health/catalogue operations never submit prompts.** Prefer cached metadata and passive executable/configuration checks. Any refresh that launches a protocol process must be explicit, bounded, and separately cleaned up.

### Windows process-tree ownership

Use one shared stdlib process supervisor, not separate cleanup implementations in every driver.

- Launch argv arrays without interpolating prompt text into a shell.
- Hide windows; drain stdout and stderr concurrently.
- Prefer a Windows Job Object with kill-on-close, implemented through `ctypes`.
- For a strict child-tree guarantee, the Windows launcher must create the child suspended, assign it to the Job Object, then resume it. Attaching after ordinary spawn leaves a descendant-creation race.
- Integrate native pipe reads with asyncio through supervised readers; a stdlib `asyncio.to_thread` bridge is acceptable initially if handle closure reliably joins the readers.
- Interrupt first through the engine protocol where available, then terminate the owned job after a bounded grace period.
- Use `taskkill /PID <pid> /T /F` as a fallback while the owned root is still identifiable. Await confirmed exit; do not report successful cleanup from command submission alone.
- `close()` and `stop_all()` await cleanup and return any unresolved ownership failures. They do not delete native session history.

Existing AgentOS adapters already use `taskkill /T /F`, and RuseBot checks process exit around the same operation. Job Objects are a proposed strengthening for worker crashes and surviving descendants. [A: claude_runtime.py:189](D:/AgentOS/backend/pyworker/runtime/claude_runtime.py:189), [A: codex_runtime.py:220](D:/AgentOS/backend/pyworker/runtime/codex_runtime.py:220), [R: procs.ts:230–256](D:/rusebot/server/procs.ts:230)

## 2. Minimal normalised events and compatibility mapping

### Event envelope

Use the `DriverEvent` envelope above. `seq` is driver ordering; SQLite’s event-row `id` remains the public replay cursor. They serve different purposes.

RuseBot supplies event, provider, thread, turn, item, and request identities plus timestamps; AgentOS persists an autoincrement row ID, run ID, event type, JSON payload, and timestamp. [R: runtime-events.ts:21–35](D:/rusebot/shared/runtime-events.ts:21), [A: schema.py:23](D:/AgentOS/backend/pyworker/db/schema.py:23)

### Proposed vocabulary

Fields below are in `data` unless present in the envelope.

| Event | Required fields and semantics | RuseBot source |
|---|---|---|
| `turn.started` | Requested model, policy summary. Logical admission only. | `turn.started`. [R:47](D:/rusebot/shared/runtime-events.ts:47) |
| `session.started` | `session_id`, `protocol`, `namespace`, `generation`, effective `model_id`, `resumed`, `rebuilt`. Emit on successful binding, including resume. | `session.started`, including rebuilt flag. [R:39–44](D:/rusebot/shared/runtime-events.ts:39) |
| `content.delta` | `stream_kind`: `assistant_text` or `reasoning_text`; `delta`; stable `item_id`; optional `phase`. Append-only within the item. | Same text channels. [R:117](D:/rusebot/shared/runtime-events.ts:117) |
| `item.started` | `item_type=tool`, `title`, `status`; optional bounded input preview and command summary. | Tool/reasoning item start. [R:98–107](D:/rusebot/shared/runtime-events.ts:98) |
| `item.updated` | Tool `status`; optional input/output preview or progress text. Updates replace named fields; they are not appended as assistant text. | Item update and live progress. [R:108–111](D:/rusebot/shared/runtime-events.ts:108) |
| `item.completed` | Tool: `status`, `ok`, bounded output preview. Assistant text: complete authoritative `text`, optional `phase=commentary/final/unknown`. | Tool and assistant text completion. [R:112–113](D:/rusebot/shared/runtime-events.ts:112) |
| `request.opened` | `request_type`, tool, summary, expiry, supported decisions; optional structured questions/options and `allow_session`. | Permission/question request. [R:119–153](D:/rusebot/shared/runtime-events.ts:119) |
| `request.resolved` | `behavior`, `source`, `outcome`; optional answer summary. `outcome` distinguishes delivery from expiry/unavailability. | Behaviour and resolution source. [R:155–162](D:/rusebot/shared/runtime-events.ts:155) |
| `usage` | `scope=call/turn/session/context`, `measurement_id`, `final`, nullable input/output/cache-read/cache-write/reasoning counts, context size, cost. A snapshot replaces the previous measurement. | Token snapshots and final turn usage. [R:62–65](D:/rusebot/shared/runtime-events.ts:62), [R:165–172](D:/rusebot/shared/runtime-events.ts:165) |
| `runtime.error` | Serialised `DriverFailure`; optional bounded diagnostic reference. Never itself ends a turn. | Runtime error. [R:174–175](D:/rusebot/shared/runtime-events.ts:174) |
| `runtime.notice` | Stable notice code and safe message; nonfatal compatibility/resume information. | Same purpose. [R:176–178](D:/rusebot/shared/runtime-events.ts:176) |
| `turn.completed` | `status=succeeded/failed/cancelled/interrupted`, `stop_reason`, `duration_ms`, `final_item_ids`, effective model/variant, final usage if known, optional failure. | Completion with success, reason, usage. [R:56–66](D:/rusebot/shared/runtime-events.ts:56) |

Additional rules:

- Drivers synthesise stable text-item IDs when the engine does not supply them.
- A completed assistant item replaces its provisional delta buffer; never append its complete text again.
- Drivers identify the final reply through `final_item_ids`. Tool previews and reasoning are excluded.
- Unknown usage remains null. A context-window reading is not turn consumption.
- A missing tool start can be synthesised before its completion, marked `synthetic=true` in the payload.
- Remaining running tools settle as cancelled/unknown before turn termination.
- Full native arguments and command/cwd data stay in the approval context; renderer events carry bounded, redacted previews. RuseBot expressly separates private command information from public previews. [R: runtime-events.ts:101–106](D:/rusebot/shared/runtime-events.ts:101), [R: runtime-events.ts:123–129](D:/rusebot/shared/runtime-events.ts:123)

**Not required initially:** `session.exited`, model-variant events, a reasoning-item lifecycle, retry events, wait-resource events, or a separate cap terminal. Process lifetime is diagnostic state; model variants come through the catalogue; budget exhaustion is a failed terminal with a specific reason. RuseBot has those broader events, but they are unnecessary for the requested local driver boundary. [R: runtime-events.ts:45–95](D:/rusebot/shared/runtime-events.ts:45)

### Persisting events without breaking the existing UI

**Do not directly forward new event names to the current renderer.** Its `isTerminal()` returns true for everything except `queued` and `running`; `useChat` immediately finishes a stream on such an event. [A: worker.ts:322–324](D:/AgentOS/frontend/src/rusebot/data/worker.ts:322), [A: useChat.ts:247–251](D:/AgentOS/frontend/src/rusebot/state/useChat.ts:247)

Use the same `conversation_run_events` table with two row forms:

| Row `type` | `data_json` | Delivery |
|---|---|---|
| Existing lifecycle names | Existing `{run_id,status,...}` shape | All clients. |
| `runtime` | `{run_id,status:"running",event:<DriverEvent>}` | Only clients requesting normalised events. |

`runtime` fits the existing declared `VARCHAR(16)` without widening it. The complete event name lives inside JSON. Existing lifecycle payload construction must remain separate from the new runtime-envelope writer. [A: schema.py:23](D:/AgentOS/backend/pyworker/db/schema.py:23), [A: conversation_runs.py:171–177](D:/AgentOS/backend/pyworker/core/conversation_runs.py:171)

Proposed projections:

| Driver event | Durable effects | Legacy stream |
|---|---|---|
| `turn.started` | Runtime row; preserve manager’s existing `running` transition. | `running` once. |
| `session.started` | Runtime row and session upsert in one transaction. | None. |
| Text/tool/usage/notice | Runtime row. | None. |
| `request.opened` | Runtime row; linked approval record where supported. | None; approvals remain available through their API. |
| `request.resolved` | Runtime row and approval-state reconciliation. | None. |
| `runtime.error` | Runtime row; no immediate status transition. | None. |
| `turn.completed` | Runtime row plus conditional lifecycle transition; persist final assistant message on success. | `succeeded`, `failed`, `cancelled`, or `interrupted`. |

Write the runtime terminal row **before** the legacy lifecycle terminal row in the same transaction. The existing event iterator ends when it encounters a lifecycle terminal; preserving that order lets normalised clients receive the complete turn first. [A: conversation_runs.py:228–258](D:/AgentOS/backend/pyworker/core/conversation_runs.py:228)

For cancellation, preserve today’s “persist cancellation first” behaviour: the manager durably settles the logical turn and publishes its lifecycle event, then interrupts the driver. A later driver terminal becomes a cleanup acknowledgement and is not persisted as a second terminal. [A: conversation_runs.py:484–507](D:/AgentOS/backend/pyworker/core/conversation_runs.py:484)

Extend stream opening with an optional `event_format="legacy"|"normalised-v1"`, defaulting to legacy:

- Legacy: suppress `runtime` rows.
- Normalised: emit their enclosed event name and envelope.
- Both: retain SQLite row IDs and existing lifecycle events.
- Apply the same filtering to `runs.events`, not just live streams.
- Filtering must advance the internal replay cursor across suppressed rows.

Keep the existing pipe shape:

```json
{"type":"stream","stream_id":"s","event":"content.delta","id":42,"data":{"version":1,"turn_id":"r","seq":4,"type":"content.delta","data":{"stream_kind":"assistant_text","delta":"Hello"}}}
```

AgentOS already forwards persisted event names/data/IDs into this envelope and ends with `stream_end`; its replay path reads SQLite order rather than trusting queue order. [A: streams.py:79–92](D:/AgentOS/backend/pyworker/streams.py:79), [A: conversation_runs.py:250–259](D:/AgentOS/backend/pyworker/core/conversation_runs.py:250)

Retain bounded subscriber queues and reconnect/replay on overflow. Browser backpressure must not stop protocol readers. [A: conversation_runs.py:61–76](D:/AgentOS/backend/pyworker/core/conversation_runs.py:61)

### Approval compatibility

Existing approvals are run-bound records with summaries, scopes, expiry, and conditional decisions. The current API accepts only approve/deny and does not call an engine driver. [A: approvals.py:72–86](D:/AgentOS/backend/pyworker/core/approvals.py:72), [A: approvals.py:113–128](D:/AgentOS/backend/pyworker/core/approvals.py:113), [A: convo.py:565–571](D:/AgentOS/backend/pyworker/api/convo.py:565)

Therefore:

- Preserve workspace-publication approvals and their current consumer.
- Add an explicit `origin=runtime` for native requests.
- Only runtime-origin decisions dispatch to `driver.respond()`.
- Add question-answer API/UI support before advertising `question_requests=True`.
- Until then, native questions receive an explicit unavailable/cancelled response; never render them as misleading Allow/Deny cards.
- An approved database row is not proof that the native answer was delivered. Record delivery outcome separately.
- Restart expires pending native requests; persisted request IDs cannot recreate an in-memory RPC waiter.

## 3. Native sessions and schema

### Engine session semantics

| Engine | Identity and continuation |
|---|---|
| Claude | `system/init.session_id`; launch an existing conversation with `--resume <id>`. New sessions can be assigned `--session-id <uuid>`. RuseBot can reuse a compatible idle stream-json process. [R: claude.ts:1224–1225](D:/rusebot/server/drivers/claude.ts:1224), [R: claude.ts:1360–1386](D:/rusebot/server/drivers/claude.ts:1360), [R: claude.ts:1114–1138](D:/rusebot/server/drivers/claude.ts:1114) |
| Codex | `thread/start` returns `thread.id`; `thread/resume` takes `threadId`. `turn/start` separately returns `turn.id`. [R: codex.ts:774–787](D:/rusebot/server/drivers/codex.ts:774), [R: codex.ts:1410–1431](D:/rusebot/server/drivers/codex.ts:1410) |
| OpenCode ACP | `session/new` returns `sessionId`; default continuation method is `session/load` with `sessionId`, cwd, and MCP servers. [R: core.ts:1240–1250](D:/rusebot/server/drivers/acp/core.ts:1240), [R: core.ts:1284–1292](D:/rusebot/server/drivers/acp/core.ts:1284) |
| Antigravity ACP | RuseBot explicitly configures `session/resume`, not `session/load`. Treat this as a dialect capability. [R: antigravity.ts:58–63](D:/rusebot/server/drivers/antigravity.ts:58) |

The current AgentOS Claude and Codex adapters intentionally disable persistence; the ACP adapter discards its session ID when returning. The compatibility wrapper must not claim they can resume. [A: claude_runtime.py:257–258](D:/AgentOS/backend/pyworker/runtime/claude_runtime.py:257), [A: codex_runtime.py:325–331](D:/AgentOS/backend/pyworker/runtime/codex_runtime.py:325), [A: agy_acp_runtime.py:634](D:/AgentOS/backend/pyworker/runtime/agy_acp_runtime.py:634)

### Proposed schema version 18

The inspected schema constant is 17. Implement this through the existing migration mechanism, updating its recognised additive columns as well as fresh-database definitions. Existing databases are backed up when their version advances. [A: schema.py:3](D:/AgentOS/backend/pyworker/db/schema.py:3), [A: migrate.py:103–128](D:/AgentOS/backend/pyworker/db/migrate.py:103)

**New table: `runtime_sessions`**

| Column | Exact declaration / meaning |
|---|---|
| `scope_kind` | `TEXT NOT NULL`; constrained to conversation, ceo, mission_attempt, workflow. |
| `scope_id` | `TEXT NOT NULL`; application identity, never a native ID. |
| `engine` | `TEXT NOT NULL`; existing AgentOS engine identifier. |
| `runtime_key` | `TEXT NOT NULL`; configured local runtime identity, initially `default`. |
| `protocol` | `TEXT NOT NULL`; dialect from `SessionRef`. |
| `namespace` | `TEXT NOT NULL`; stable local engine-storage/configuration namespace. |
| `native_session_id` | `TEXT NOT NULL`. |
| `generation` | `INTEGER NOT NULL DEFAULT 1`. |
| `state` | `TEXT NOT NULL DEFAULT 'unverified'`; constrained to unverified, ready, invalid. |
| `model_id` | `TEXT NOT NULL`. |
| `provider_id` | `TEXT`; upstream model provider, distinct from engine. |
| `variant` | `TEXT`. |
| `agent_id` | `TEXT`. |
| `cwd` | `TEXT`; nullable only for imported legacy records. |
| `config_fingerprint` | `TEXT`; hash of effective launch/policy contract, excluding secrets. |
| `last_turn_id` | `TEXT`. |
| `created_at` | `DATETIME NOT NULL`. |
| `updated_at` | `DATETIME NOT NULL`. |

Constraints:

- Primary key: `(scope_kind, scope_id, engine, runtime_key)`.
- Unique index: `(engine, runtime_key, namespace, protocol, native_session_id)`.
- No foreign key on `scope_id`: CEO and mission scopes are not rows in `conversations`. Explicit deletion/reset logic owns their lifecycle.
- One current session per key. Historical bindings remain recoverable from run events.

**Add to `conversation_runs`:**

| Column | Declaration |
|---|---|
| `runtime_key` | `TEXT NOT NULL DEFAULT 'default'` |
| `driver_protocol` | `TEXT` |
| `native_session_id` | `TEXT` |
| `native_turn_id` | `TEXT` |
| `session_generation` | `INTEGER` |

These snapshot the exact binding used by a run; the current session row may later change.

**Add to `conversation_run_events`:**

- `driver_event_id TEXT`, nullable.
- Partial unique index on `(run_id, driver_event_id)` where `driver_event_id IS NOT NULL`.

This makes persistence retries idempotent while preserving existing lifecycle rows and replay IDs.

**Add to `approvals`:**

| Column | Declaration |
|---|---|
| `origin` | `TEXT NOT NULL DEFAULT 'workspace'` |
| `runtime_request_id` | `TEXT` |
| `request_type` | `TEXT` |
| `request_json` | `TEXT` |
| `response_json` | `TEXT` |
| `delivery_status` | `TEXT` |

Add a partial unique index on `(run_id, runtime_request_id)` for non-null request IDs. `request_json` stores the normalised question/decision schema, not unredacted native arguments.

### Existing `opencode_conversations`

Keep it during migration. It currently has a conversation primary key and globally unique session ID, with model/provider/agent metadata; `chat.session.get/save` directly use it. [A: schema.py:9](D:/AgentOS/backend/pyworker/db/schema.py:9), [A: schema.py:56–58](D:/AgentOS/backend/pyworker/db/schema.py:56), [A: chat.py:120–149](D:/AgentOS/backend/pyworker/api/chat.py:120)

Backfill each row into `runtime_sessions` as:

- Scope: `conversation`, using the existing conversation ID.
- Engine/runtime: `opencode` / `default`.
- Protocol: `opencode-legacy`.
- Namespace: the configured legacy OpenCode storage namespace.
- Existing session/model/provider/agent/timestamps copied.
- `state=unverified`, `generation=1`; cwd/fingerprint initially null.

**Do not relabel those IDs as ACP sessions.** The current OpenCode runtime uses its legacy API and stable-title lookup; source inspection does not establish that its IDs are loadable by `opencode acp`. [A: opencode_runtime.py:377–402](D:/AgentOS/backend/pyworker/runtime/opencode_runtime.py:377), [A: opencode_runtime.py:566–589](D:/AgentOS/backend/pyworker/runtime/opencode_runtime.py:566)

Route legacy `chat.session.*` through a compatibility repository. During coexistence, preserve legacy writes; after its callers migrate, stop dual-writing. Preserve its agent-change restrictions until deliberately replaced. [A: chat.py:128–148](D:/AgentOS/backend/pyworker/api/chat.py:128)

### Resume policy

Persist successful native binding immediately, before prompt submission where possible. Store effective model, cwd, protocol, namespace, generation, and policy fingerprint.

On resume failure:

- Explicit missing-session refusal before prompt submission may offer a new session seeded from a clearly labelled transcript.
- Authentication, malformed parameters, or unsupported protocol are not “session missing”.
- Timeout/EOF after submission means execution is uncertain: fail/interruption, retain partial output, no automatic replay.
- Rebuild increments generation and emits `session.started(rebuilt=true)` plus a notice.

RuseBot restricts Codex recovery using missing/archived-session checks and submission/output state; ACP distinguishes authentication and invalid-parameter errors during load. [R: codex.ts:1432–1444](D:/rusebot/server/drivers/codex.ts:1432), [R: core.ts:1254–1282](D:/rusebot/server/drivers/acp/core.ts:1254)

## 4. Per-engine wire protocols and fake CLI requirements

These are the inspected implementations’ dialects, not promises about every installed CLI version.

### 4.1 Claude stream-json

**Process arguments**

RuseBot’s base launch is:

`claude -p --output-format stream-json --input-format stream-json --verbose --include-partial-messages --permission-mode <mode>`

It maps approval policies to `default`, `acceptEdits`, `auto`, or `bypassPermissions`; adds model/effort when selected; and appends either `--resume <id>` or `--session-id <uuid>`. [R: claude.ts:928–956](D:/rusebot/server/drivers/claude.ts:928), [R: claude.ts:989–992](D:/rusebot/server/drivers/claude.ts:989), [R: claude.ts:1224–1225](D:/rusebot/server/drivers/claude.ts:1224)

For AgentOS, preserve current tool restrictions while changing transport. Its existing launch uses `--tools ""`, `--strict-mcp-config`, and scoped MCP configuration; persistence is the flag being deliberately changed. [A: claude_runtime.py:257–275](D:/AgentOS/backend/pyworker/runtime/claude_runtime.py:257)

**Handshake and prompt**

There is no Codex-style initialise RPC in this path. Write a newline-delimited user frame to stdin, keep stdin open, and consume the `system/init` response containing session and model information. RuseBot’s text-only input shape is: [R: claude.ts:688–708](D:/rusebot/server/drivers/claude.ts:688), [R: claude.ts:1351–1386](D:/rusebot/server/drivers/claude.ts:1351)

```json
{"type":"user","message":{"role":"user","content":"Explain this project."}}
```

**Output mapping**

| Native message | Normalised result |
|---|---|
| `system`, `subtype:"init"`, `session_id`, `model` | `session.started`. |
| `stream_event.event.type:"content_block_delta"` with `delta.type:"text_delta"` / `text` | Assistant `content.delta`. |
| Same with `thinking_delta` / `thinking` | Reasoning `content.delta`. |
| `assistant.message.content[]` text | Complete assistant item; fallback delta only if not already streamed. |
| `assistant.message.content[]` `tool_use` with `id,name,input` | Tool start. |
| `user.message.content[]` `tool_result` with `tool_use_id,is_error,content` | Tool completion. |
| `tool_progress` | Tool update. |
| `result` with `is_error`, stop fields, usage | Turn settlement. |

These mappings are implemented together in RuseBot. [R: claude.ts:1395–1445](D:/rusebot/server/drivers/claude.ts:1395), [R: claude.ts:1462–1485](D:/rusebot/server/drivers/claude.ts:1462), [R: claude.ts:1489–1523](D:/rusebot/server/drivers/claude.ts:1489)

**Approval transport: important distinction**

RuseBot does **not** use stdin `control_request/control_response` here. It launches a permission MCP server and sets:

`--permission-prompt-tool mcp__ogb__approve`

The MCP server forwards requests to a local broker. [R: claude.ts:1057–1070](D:/rusebot/server/drivers/claude.ts:1057)

Broker messages use these shapes:

```json
{"t":"ask","id":"ask-1","tool":"Bash","input":{"command":"git status"}}
{"t":"answer","id":"ask-1","behavior":"deny","message":"Denied","source":"user"}
```

The broker correlates IDs and emits resolution after answering. The MCP result content contains a JSON string such as `{"behavior":"allow","updatedInput":{...}}`, or a deny result with a message. [R: claude.ts:439–470](D:/rusebot/server/drivers/claude.ts:439), [R: permission-proxy.ts:153–183](D:/rusebot/server/permission-proxy.ts:153)

Proposed Python implementation: a small stdlib MCP stdio helper, using authenticated loopback IPC to the worker. Its process and broker connections belong to the turn supervisor. Do not depend on RuseBot’s Node helper. Session-remembered grants remain disabled until explicitly supported and tested.

**Interrupt**

RuseBot closes the session and kills its process tree; this implementation does not establish a graceful Claude interrupt message. AgentOS should initially do the same, then resume the durable session on a later turn. [R: claude.ts:1118–1123](D:/rusebot/server/drivers/claude.ts:1118), [R: claude.ts:1928](D:/rusebot/server/drivers/claude.ts:1928)

**Pitfalls to preserve**

- Ignore nested stream events with `parent_tool_use_id`. [R:1395–1396](D:/rusebot/server/drivers/claude.ts:1395)
- Avoid duplicating streamed text when complete assistant messages arrive. [R:1430–1433](D:/rusebot/server/drivers/claude.ts:1430)
- Ignore replayed user messages as tool results. [R:1462–1465](D:/rusebot/server/drivers/claude.ts:1462)
- Ignore task-notification results as turn terminals. [R:1483–1484](D:/rusebot/server/drivers/claude.ts:1483)
- Treat API-error assistant messages as errors, not successful model replies. [R:1408–1428](D:/rusebot/server/drivers/claude.ts:1408)
- Reject duplicate broker request IDs; deny on broker loss. [R:454–458](D:/rusebot/server/drivers/claude.ts:454), [R: permission-proxy.ts:22–29](D:/rusebot/server/permission-proxy.ts:22)
- Gate optional flags by CLI support rather than assuming them. [R:971–987](D:/rusebot/server/drivers/claude.ts:971)
- Do not copy steering/queued-message completion logic without implementing steering: RuseBot may defer `result` while queued continuations remain. [R:1525–1534](D:/rusebot/server/drivers/claude.ts:1525)

**Fake CLI must emulate:** argv validation; multiple newline-delimited user turns; init/resume success and refusal; fragmented text/thinking frames; assistant snapshots; tool-use/results; API-error assistant frames; result/EOF races; and cancellation-resistant child processes. Approval tests must also exercise the fake MCP endpoint and broker disconnect, not invent a stdin approval protocol.

### 4.2 Codex app-server

**Process arguments**

The local non-injected baseline is `codex app-server`, plus RuseBot’s tool-surface configuration:

- `-c features.browser_use=false`
- `-c features.browser_use_external=false`
- `-c features.computer_use=false`
- `-c 'plugins={ "browser@openai-bundled" = { enabled = false }, "computer-use@openai-bundled" = { enabled = false }, "unified-computer-use@openai-bundled" = { enabled = false } }'`

Local-provider injection adds configuration only when a recognised injected model is selected. CLI-specified `-c/--config` overrides are moved after the subcommand. [R: codex.ts:627–629](D:/rusebot/server/drivers/codex.ts:627), [R: codex-tool-surface.ts:1–7](D:/rusebot/server/drivers/codex-tool-surface.ts:1), [R: local-inject.ts:107–124](D:/rusebot/server/drivers/local-inject.ts:107), [R: codex-process.ts:79–108](D:/rusebot/server/drivers/codex-process.ts:79)

AgentOS should use an explicit configuration profile; do not copy RuseBot’s `workspace-write` policy implicitly. Its current Codex execution is read-only and ephemeral. [A: codex_runtime.py:325–331](D:/AgentOS/backend/pyworker/runtime/codex_runtime.py:325)

**Handshake**

Newline-delimited JSON-RPC:

```json
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"clientInfo":{"name":"agentos","title":"AgentOS","version":"0"},"capabilities":{"experimentalApi":true}}}
{"jsonrpc":"2.0","method":"initialized","params":{}}
```

RuseBot then reads effective configuration before selecting approval parameters. [R: codex.ts:1332–1340](D:/rusebot/server/drivers/codex.ts:1332)

**Session and turn**

```json
{"jsonrpc":"2.0","id":2,"method":"thread/start","params":{"cwd":"D:\\project","model":"MODEL","ephemeral":false}}
{"jsonrpc":"2.0","id":3,"method":"turn/start","params":{"threadId":"native-thread","input":[{"type":"text","text":"Explain this project."}]}}
```

Thread parameters additionally include developer instructions, optional model provider, and approval/sandbox settings. Resume uses `thread/resume` with `threadId`; turn effort is sent on `turn/start`. [R: codex.ts:1410–1417](D:/rusebot/server/drivers/codex.ts:1410), [R: codex.ts:1448–1457](D:/rusebot/server/drivers/codex.ts:1448), [R: codex.ts:1488–1501](D:/rusebot/server/drivers/codex.ts:1488)

RuseBot’s Ask-like settings use `approvalPolicy:"on-request"`, reviewer `"user"`, thread sandbox `"workspace-write"`, and turn sandbox policy `{type:"workspaceWrite"}`. This illustrates separate thread/turn shapes; it is not the proposed AgentOS default. [R: codex.ts:288–298](D:/rusebot/server/drivers/codex.ts:288)

**Notifications and completion**

| Method | Relevant fields / mapping |
|---|---|
| `item/agentMessage/delta` | `delta`; assistant text. |
| `item/reasoning/textDelta`, `item/reasoning/summaryTextDelta` | `delta`; reasoning. |
| `item/started` | `item.id`, type and command/tool/change fields. |
| `item/completed` | Agent text snapshot or tool result/status/output. |
| `thread/tokenUsage/updated` | `tokenUsage.total`, `last`, context window. |
| `turn/completed` | `turn.id`, `turn.status`, optional `turn.error`. |
| `error` | Error details; `willRetry:true` means the engine is still retrying. |

[R: codex.ts:1086–1117](D:/rusebot/server/drivers/codex.ts:1086), [R: codex.ts:1120–1185](D:/rusebot/server/drivers/codex.ts:1120), [R: codex.ts:1189–1204](D:/rusebot/server/drivers/codex.ts:1189), [R: codex.ts:1220–1242](D:/rusebot/server/drivers/codex.ts:1220)

**Approvals and questions**

Native requests carry their own JSON-RPC `id`.

- Current command/file approval methods: `item/commandExecution/requestApproval`, `item/fileChange/requestApproval`.
- Legacy methods: `execCommandApproval`, `applyPatchApproval`.
- Additional permissions: `item/permissions/requestApproval`.
- Questions: `item/tool/requestUserInput`.
- MCP elicitation has a separate response shape.

[R: codex.ts:885–898](D:/rusebot/server/drivers/codex.ts:885)

Examples:

```json
{"jsonrpc":"2.0","id":90,"result":{"decision":"accept"}}
{"jsonrpc":"2.0","id":91,"result":{"decision":"decline"}}
{"jsonrpc":"2.0","id":92,"result":{"answers":{"question-id":{"answers":["Selected answer"]}}}}
```

Legacy approval decisions are `"approved"`/`"denied"`. Additional-permission results contain `permissions` and `scope:"turn"`; MCP acceptance uses an action/content shape. Do not answer every request with the command-approval shape. [R: codex.ts:946–953](D:/rusebot/server/drivers/codex.ts:946), [R: codex-questions.ts:43–49](D:/rusebot/server/drivers/codex-questions.ts:43)

Unsupported requests receive JSON-RPC `-32601`; malformed question requests receive `-32602`. [R: codex.ts:899–922](D:/rusebot/server/drivers/codex.ts:899)

**Interrupt**

```json
{"jsonrpc":"2.0","id":4,"method":"turn/interrupt","params":{"threadId":"native-thread","turnId":"native-turn"}}
```

RuseBot sends it once, waits briefly for settlement, then escalates to process termination. [R: codex.ts:804–825](D:/rusebot/server/drivers/codex.ts:804)

**Pitfalls to preserve**

- Buffer early notifications until the `turn/start` reply supplies a native turn ID. [R:782–792](D:/rusebot/server/drivers/codex.ts:782)
- Filter notifications by native turn ID. [R:1086–1088](D:/rusebot/server/drivers/codex.ts:1086)
- Treat text snapshots as completion, not duplicate deltas. [R:1149–1156](D:/rusebot/server/drivers/codex.ts:1149)
- Compute turn usage from session-total baselines; do not sum successive totals. [R:1189–1204](D:/rusebot/server/drivers/codex.ts:1189)
- Distinguish server requests from replies even when both have IDs. [R:1273–1283](D:/rusebot/server/drivers/codex.ts:1273)
- EOF before `turn/completed` is a failure, with bounded recent diagnostics. [R:1317–1324](D:/rusebot/server/drivers/codex.ts:1317)
- Archived threads may require `thread/unarchive` before resume. [R:1418–1419](D:/rusebot/server/drivers/codex.ts:1418)
- Startup RPCs have longer deadlines than ordinary RPCs. [R: codex-process.ts:62–65](D:/rusebot/server/drivers/codex-process.ts:62)
- Bound concurrent process launches independently of active turns. [R: codex-process.ts:6–23](D:/rusebot/server/drivers/codex-process.ts:6)

**Fake CLI must emulate:** initialise/initialised; config read; start/resume/unarchive; distinct thread/turn IDs; notifications before start reply; stale-turn notifications; current and legacy approval requests; question replies; usage baselines; retrying errors; interrupted/failed/completed terminals; missing-session refusal; RPC timeout; and exit before completion.

### 4.3 ACP: OpenCode and Antigravity dialects

**Process arguments differ**

| Runtime | Inspected launch |
|---|---|
| RuseBot OpenCode | `opencode acp`. [R: opencode-go.ts:702–718](D:/rusebot/server/drivers/acp/opencode-go.ts:702) |
| RuseBot Antigravity | Resolved ACP runtime executable; no extra arguments on Windows, `--uid=` on Linux. [R: antigravity.ts:68–76](D:/rusebot/server/drivers/antigravity.ts:68), [R: antigravity-acp.ts:205–208](D:/rusebot/server/drivers/antigravity-acp.ts:205) |
| Current AgentOS Antigravity | `agy acp --stdio --sandbox --include-default-customizations=false`; requires plan mode. [A: agy_acp_runtime.py:80–82](D:/AgentOS/backend/pyworker/runtime/agy_acp_runtime.py:80) |

Do not treat those Antigravity executables as interchangeable. Initially preserve AgentOS’s existing launcher and restrictions; enable a separate managed-runtime dialect only after compatibility tests establish its behaviour.

**Handshake**

```json
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":1,"clientInfo":{"name":"agentos","version":"0"},"clientCapabilities":{"fs":{"readTextFile":false,"writeTextFile":false},"terminal":false}}}
```

RuseBot advertises client filesystem support selectively and terminal support as false. It may then send `authenticate` using an advertised method. [R: core.ts:1182–1209](D:/rusebot/server/drivers/acp/core.ts:1182)

AgentOS should initially advertise both filesystem capabilities as false, matching its current ACP implementation. Do not claim capabilities without implementing their server-request handlers. [A: agy_acp_runtime.py:381–393](D:/AgentOS/backend/pyworker/runtime/agy_acp_runtime.py:381)

**Session and prompt**

```json
{"jsonrpc":"2.0","id":2,"method":"session/new","params":{"cwd":"D:\\project","mcpServers":[]}}
{"jsonrpc":"2.0","id":3,"method":"session/load","params":{"sessionId":"s1","cwd":"D:\\project","mcpServers":[]}}
{"jsonrpc":"2.0","id":4,"method":"session/prompt","params":{"sessionId":"s1","prompt":[{"type":"text","text":"Explain this project."}]}}
```

Use `session/resume` instead of `session/load` for the Antigravity dialect configured by RuseBot. Successful session creation must return a usable `sessionId`. [R: core.ts:1240–1292](D:/rusebot/server/drivers/acp/core.ts:1240), [R: core.ts:1440–1445](D:/rusebot/server/drivers/acp/core.ts:1440)

OpenCode and RuseBot Antigravity select models through `session/set_config_option` using `configId:"model"` and verify the effective model. Current AgentOS also handles `session/set_model` and model config options. [R: opencode-go.ts:718](D:/rusebot/server/drivers/acp/opencode-go.ts:718), [R: antigravity.ts:59](D:/rusebot/server/drivers/antigravity.ts:59), [R: core.ts:1336–1346](D:/rusebot/server/drivers/acp/core.ts:1336), [A: agy_acp_runtime.py:426–445](D:/AgentOS/backend/pyworker/runtime/agy_acp_runtime.py:426)

**Updates**

```json
{"jsonrpc":"2.0","method":"session/update","params":{"sessionId":"s1","update":{"sessionUpdate":"agent_message_chunk","content":{"type":"text","text":"Hello"}}}}
```

| `update.sessionUpdate` | Mapping |
|---|---|
| `agent_message_chunk` | Assistant text delta. |
| `agent_thought_chunk` | Reasoning delta. |
| `tool_call` | Tool start using `toolCallId`, title, raw input, status. |
| `tool_call_update` | Tool update/completion; completed/failed statuses end it. |
| `usage_update` | Context/usage snapshot where its meaning is known. |
| `config_option_update` | Refresh effective model/variant state internally. |

[R: core.ts:815–830](D:/rusebot/server/drivers/acp/core.ts:815), [R: core.ts:833–892](D:/rusebot/server/drivers/acp/core.ts:833)

**Permissions**

The method is exactly **`session/request_permission`**, not bare `request_permission`. Its params contain `toolCall` and options with native `optionId`, `kind`, and `name`. Select an offered option; never invent its ID. [R: core.ts:690–706](D:/rusebot/server/drivers/acp/core.ts:690)

```json
{"jsonrpc":"2.0","id":90,"result":{"outcome":{"outcome":"selected","optionId":"allow-once-native-id"}}}
{"jsonrpc":"2.0","id":91,"result":{"outcome":{"outcome":"cancelled"}}}
```

Prefer `allow_once`/`reject_once`; use `allow_always` only for an explicit supported session grant. Missing choices must cancel rather than guess. [R: core.ts:695–717](D:/rusebot/server/drivers/acp/core.ts:695), [R: core.ts:755–787](D:/rusebot/server/drivers/acp/core.ts:755)

ACP questions are dialect-specific. RuseBot also recognises some permission requests whose tool-call IDs begin with `interaction_`; this is not a universal ACP question contract. [R: core.ts:709–710](D:/rusebot/server/drivers/acp/core.ts:709)

**Finish and interrupt**

The reply to `session/prompt` ends the turn:

- `stopReason:"end_turn"` → success.
- `"cancelled"` → cancelled.
- Other reasons → explicit failure/reason handling.

RuseBot sometimes reports cancellation with `ok:true`; AgentOS must normalise it to `status:"cancelled"`. [R: core.ts:1494–1508](D:/rusebot/server/drivers/acp/core.ts:1494)

Interrupt is a notification with no request ID:

```json
{"jsonrpc":"2.0","method":"session/cancel","params":{"sessionId":"s1"}}
```

RuseBot waits five seconds before closing an unresponsive session. [R: core.ts:1150–1159](D:/rusebot/server/drivers/acp/core.ts:1150)

**Pitfalls to preserve**

- Ignore `_meta.isReplay` and pre-prompt transcript updates. [R:815–825](D:/rusebot/server/drivers/acp/core.ts:815)
- Route by session ID; initially avoid shared-process multiplexing to simplify ownership. RuseBot’s OpenCode enables it explicitly. [R: host.ts:248–251](D:/rusebot/server/drivers/acp/host.ts:248), [R: opencode-go.ts:715–716](D:/rusebot/server/drivers/acp/opencode-go.ts:715)
- Handle tool calls that arrive already completed, and missing tool IDs. [R:866–885](D:/rusebot/server/drivers/acp/core.ts:866)
- Resolve outstanding requests and flush assistant text before terminal emission. [R:540–563](D:/rusebot/server/drivers/acp/core.ts:540)
- A successful empty turn may still be unusable; distinguish no reply from a completed tool-only turn. [R:554–560](D:/rusebot/server/drivers/acp/core.ts:554)
- A quiet watchdog must account for active tools and pending approvals. RuseBot diagnoses idle prompts rather than relying solely on one ordinary RPC timeout. [R:1438–1450](D:/rusebot/server/drivers/acp/core.ts:1438)
- Preserve nested RPC error code/data and bounded details. [R: rpc-error.ts:3–20](D:/rusebot/server/drivers/acp/rpc-error.ts:3)
- Antigravity startup output may contain diagnostics outside JSON; bounded line parsing is required. [R: antigravity-acp.ts:23–24](D:/rusebot/server/drivers/antigravity-acp.ts:23), [R: antigravity-acp.ts:237–255](D:/rusebot/server/drivers/antigravity-acp.ts:237)

**Fake CLI must emulate:** version/capability negotiation; model/mode selection; new/load/resume independently; replay during load; interleaved updates and RPC replies; tool calls with partial/missing IDs; permission options and missing-choice cases; prompt stop reasons; cancel notification with delayed or absent completion; nested errors; diagnostic noise; and process-tree cleanup.

## 5. Error normalisation

Normalise structured errors first, then bounded text heuristics. Retain category, phase, native code, and submission certainty separately. A transient error is not automatically safe to replay.

AgentOS already distinguishes offline, authentication, rate limit, unavailable model, timeout, and cancellation, but maps missing CLI into offline and has no dedicated protocol category. [A: errors.py:9–45](D:/AgentOS/backend/pyworker/runtime/errors.py:9)

| Category | Signals and treatment |
|---|---|
| `auth` | Claude API-error assistant frames with authentication classification; Codex RPC/turn errors or 401/unauthorised text; ACP authentication refusal or known dialect auth codes. No automatic retry until environment changes. [R: claude-auth-recovery.ts:12–14](D:/rusebot/server/drivers/claude-auth-recovery.ts:12), [R: codex.ts:1513–1518](D:/rusebot/server/drivers/codex.ts:1513), [R: opencode-go.ts:766–780](D:/rusebot/server/drivers/acp/opencode-go.ts:766) |
| `quota` | Explicit usage exhaustion, quota, or resource exhaustion. Preserve reset time when supplied. Keep subtype distinct from short-term rate limiting. Claude uses limit events/API-error fields; ACP errors may carry dialect codes. [R: claude.ts:1477–1490](D:/rusebot/server/drivers/claude.ts:1477), [R: claude-limits.ts:22–28](D:/rusebot/server/drivers/claude-limits.ts:22), [A: agy_acp_runtime.py:138–139](D:/AgentOS/backend/pyworker/runtime/agy_acp_runtime.py:138) |
| `rate_limit` | HTTP 429 or explicit rate-limit signal without longer quota exhaustion. Retryable classification; replay still requires safe submission state. [R: engine-errors.ts:56–63](D:/rusebot/server/drivers/engine-errors.ts:56) |
| `model_unavailable` | Explicit unknown/unavailable model or rejected model selection. Do not classify arbitrary “not found” as a model error; it may mean a missing session or executable. ACP verifies selected models. [R: core.ts:1336–1346](D:/rusebot/server/drivers/acp/core.ts:1336), [A: agy_acp_runtime.py:140–142](D:/AgentOS/backend/pyworker/runtime/agy_acp_runtime.py:140) |
| `timeout` | Locally expired launch/handshake/RPC/turn deadline. Record phase; interrupt and clean up. A prompt timeout is execution-uncertain. Codex uses method-specific deadlines. [R: codex.ts:763–769](D:/rusebot/server/drivers/codex.ts:763), [R: codex-process.ts:62–65](D:/rusebot/server/drivers/codex-process.ts:62) |
| `cli_missing` | Executable resolution failure or `FileNotFoundError` before launch. No native message exists. Distinguish access-denied/spawn failures. Current AgentOS checks missing executables explicitly. [A: codex_runtime.py:301–302](D:/AgentOS/backend/pyworker/runtime/codex_runtime.py:301) |
| `cancelled` | AgentOS stop flag or native cancellation terminal. Never infer successful user cancellation solely from an unrelated process exit. ACP and Codex have explicit cancellation mechanisms. [R: core.ts:1150–1159](D:/rusebot/server/drivers/acp/core.ts:1150), [R: codex.ts:814–819](D:/rusebot/server/drivers/codex.ts:814) |
| `protocol_error` | Invalid required IDs/shapes, unsupported negotiated version, contradictory terminal, framing overflow, or premature EOF without a more specific cause. Codex requires a native turn ID; AgentOS ACP validates protocol version/session ID. [R: codex.ts:782–785](D:/rusebot/server/drivers/codex.ts:782), [A: agy_acp_runtime.py:391–400](D:/AgentOS/backend/pyworker/runtime/agy_acp_runtime.py:391) |
| `upstream_unavailable` | Explicit overload/5xx; keep distinct from malformed wire data. [R: engine-errors.ts:59–63](D:/rusebot/server/drivers/engine-errors.ts:59) |
| `request_refused` | Invalid tool schema, tool count, safety refusal, or other explicit rejection. Preserve subtype instead of calling every refusal a protocol error. [R: engine-errors.ts:5–11](D:/rusebot/server/drivers/engine-errors.ts:5), [R: engine-errors.ts:61–63](D:/rusebot/server/drivers/engine-errors.ts:61) |
| `unsupported_capability` / `output_limit` | Preserve existing AgentOS concepts for policy/feature mismatch and resource limits. [A: errors.py:48–55](D:/AgentOS/backend/pyworker/runtime/errors.py:48) |

OpenCode-specific codes such as `AUTH_REQUIRED`, `QUOTA_EXCEEDED`, or `-32603` with `data.errorName:"APIError"` belong in its dialect classifier, not in the generic ACP transport. Likewise, do not assume `-32000` means authentication for every ACP engine. [R: opencode-go.ts:766–780](D:/rusebot/server/drivers/acp/opencode-go.ts:766)

RuseBot’s common error formatter extracts nested provider messages, removes wrappers, bounds text, and redacts secrets. Adopt that discipline while retaining AgentOS’s safe public error messages. [R: engine-errors.ts:31–53](D:/rusebot/server/drivers/engine-errors.ts:31), [A: errors.py:48–55](D:/AgentOS/backend/pyworker/runtime/errors.py:48)

## 6. Migration in small, working steps

### Step 1 — Add the contract and legacy wrapper

Introduce `ExecuteTaskDriver`, wrapping each existing runtime:

- `start_turn` schedules existing `execute_task`.
- Emit `turn.started`.
- On success, emit one completed assistant item, usage if known, and one terminal.
- Forward reasoning separately when available.
- `interrupt` calls `abort_task` with the original `session_title` key, then cancels/joins the wrapper task.
- `respond` returns unavailable.
- Capabilities report no native streaming or resumability unless actually implemented.
- Preserve health/catalogue methods.

Do not split completed text into fake “live” chunks. Existing `AgentResult` already supplies the necessary compatibility fields. [A: base.py:67–88](D:/AgentOS/backend/pyworker/runtime/base.py:67)

Use the mock’s existing delayed/late-result behaviour to verify cancellation arbitration; it intentionally supports returning after cancellation. [A: mock_runtime.py:143–154](D:/AgentOS/backend/pyworker/runtime/mock_runtime.py:143)

### Step 2 — Add persistence and legacy stream projection

Apply schema v18, runtime-envelope persistence, session repository, event deduplication, and opt-in stream format.

Keep default streams status-only. Preserve final assistant insertion and status transition atomically. Startup recovery continues marking interrupted runs without replay. [A: conversation_runs.py:603–619](D:/AgentOS/backend/pyworker/core/conversation_runs.py:603), [A: conversation_runs.py:265–279](D:/AgentOS/backend/pyworker/core/conversation_runs.py:265)

### Step 3 — Add one full-text collector

Keep a compatibility facade returning `AgentResult`:

```python
async def collect_turn(
    driver: TurnDriver, turn: StartTurn,
    emit: Callable[[DriverEvent], Awaitable[None]] | None = None,
) -> AgentResult: ...
```

Collector requirements:

- Consume the same ordered stream as persistence; do not create competing queue consumers.
- Maintain per-item provisional buffers and authoritative completed text.
- Return text identified by terminal `final_item_ids`, excluding commentary, reasoning, tool output, and synthetic errors.
- Raise a normalised exception for non-success terminals.
- Enforce output limits while accumulating, not after unbounded buffering.
- On caller cancellation/timeout, shield interruption and cleanup, then propagate cancellation.
- Preserve model, variant, session, duration, and final known usage.

Keep usage recording at one ownership point. Today `run_agent_task` wraps execution and records exactly one result/failure; recording both event snapshots and the returned result would double-count. [A: ledger.py:99–113](D:/AgentOS/backend/pyworker/runtime/ledger.py:99)

### Step 4 — Enable Claude streaming first

Replace the legacy Claude wrapper behind an engine feature flag.

First retain the existing tool-less/scoped policy, add stream-json parsing and session persistence, then enable native approval brokerage after its API/UI path works.

Do not fallback to old execution after an uncertain prompt submission. A rollback flag affects the next run.

### Step 5 — Enable Codex app-server

Add the shared JSON-RPC transport, native thread repository integration, early-notification buffering, approval mapping, and native interrupt.

Start with one owned app-server process per active turn and resume the durable thread on subsequent turns. Process reuse is a later optimisation.

Preserve AgentOS’s read-only policy until explicitly configured otherwise; changing wire transport must not broaden execution permissions. [A: codex_runtime.py:325–331](D:/AgentOS/backend/pyworker/runtime/codex_runtime.py:325)

### Step 6 — Extract ACP transport and migrate Antigravity

Reuse the current Python ACP implementation’s framing, validation, cancellation, and model/mode checks, but separate:

- Process launcher.
- Generic JSON-RPC connection.
- ACP session operations.
- Engine dialect.
- Normalised event translator.

Initially retain `agy acp --stdio` and required plan mode. Probe resume support through fixture-compatible capability/version handling; otherwise report resume unsupported. RuseBot’s managed runtime uses a different launcher and resume dialect. [A: agy_acp_runtime.py:80–82](D:/AgentOS/backend/pyworker/runtime/agy_acp_runtime.py:80), [R: antigravity.ts:58–76](D:/rusebot/server/drivers/antigravity.ts:58)

### Step 7 — Add OpenCode ACP alongside legacy OpenCode

Keep existing legacy sessions routed to the wrapper until ACP session compatibility is established.

Preserve model/provider resolution and locked-agent behaviour. The current runtime refuses unrestricted agents and scoped MCP input; ACP migration must explicitly demonstrate equivalent restrictions before enabling those capabilities. [A: opencode_runtime.py:592–618](D:/AgentOS/backend/pyworker/runtime/opencode_runtime.py:592)

### Step 8 — Opt the renderer into normalised events

First replace the current “anything non-active is terminal” logic with explicit lifecycle terminal recognition. Then add provisional text, tool items, and native request cards.

Keep final persisted messages authoritative after reconnect and completion. Existing UI completion already reloads the conversation. [A: useChat.ts:234–243](D:/AgentOS/frontend/src/rusebot/state/useChat.ts:234)

### CEO loop and missions

**CEO loop:** retain full-text collection. It builds a prompt from system instructions plus transcript, then parses the completed reply as a JSON envelope containing `tool_call` or `final`. Native tool events must not become CEO tool dispatches. [A: agent_loop.py:142–181](D:/AgentOS/backend/pyworker/orchestration/agent_loop.py:142)

Initially keep CEO turns isolated because its prompt already contains conversation history. Later, native-session adoption must also change prompt construction to send only incremental input; otherwise history is duplicated.

The fast CEO path likewise wants one sanitised text reply and unwraps harmless JSON only after completion. [A: agent_loop.py:249–265](D:/AgentOS/backend/pyworker/orchestration/agent_loop.py:249)

**Missions:** preserve the existing `AgentResult`/validated-result return boundary. Mission attempts currently wait for full text, cap it, validate it, and conditionally commit success against cancellation guards. [A: conversation_runs.py:673–710](D:/AgentOS/backend/pyworker/core/conversation_runs.py:673), [A: conversation_runs.py:730–754](D:/AgentOS/backend/pyworker/core/conversation_runs.py:730)

Use a fresh native context per mission attempt by default. Do not resume a failed attempt merely because its task ID matches: the previous prompt may already have executed tools.

**Tool sessions:** preserve their completion verification after engine success. A driver terminal is not sufficient to bypass existing application-level verification or output-schema checks. [A: conversation_runs.py:543–573](D:/AgentOS/backend/pyworker/core/conversation_runs.py:543)

### Required verification when implementation begins

Use stdlib fake processes and `unittest`/`IsolatedAsyncioTestCase`, without model calls:

- Contract settlement, ordering, duplicate terminals, and interrupt races.
- Every wire scenario listed above.
- Golden mappings from native messages to normalised events.
- Legacy stream output unchanged; new events cannot terminate old clients.
- Resume identity survives worker restart; unfinished runs are interrupted without resubmission.
- Approval expiry, duplicate answers, failed native delivery, and stale generations.
- Windows child/grandchild cleanup, including worker death.
- Collector JSON correctness with fragmented text and commentary/tool interleaving.
- One usage ledger entry per execution.

## 7. Risks and open questions

| Risk or question | Recommendation |
|---|---|
| **Installed CLI versions may differ from inspected source assumptions.** | Version/dialect-gate features, validate required response shapes, and fail before prompt submission when policy cannot be established. Claude already gates optional flags; Python ACP validates negotiated version. [R: claude.ts:971–987](D:/rusebot/server/drivers/claude.ts:971), [A: agy_acp_runtime.py:391–393](D:/AgentOS/backend/pyworker/runtime/agy_acp_runtime.py:391) |
| **Current UI will misclassify new event names.** | Default to legacy projection until explicit renderer opt-in; change terminal recognition before enabling it. [A: worker.ts:322–324](D:/AgentOS/frontend/src/rusebot/data/worker.ts:322) |
| **Legacy OpenCode IDs may not load through ACP.** | Import as `opencode-legacy`; require an explicit compatibility result before changing protocol. Preserve old routing meanwhile. [A: opencode_runtime.py:377–402](D:/AgentOS/backend/pyworker/runtime/opencode_runtime.py:377) |
| **Antigravity has two different launch contracts.** | Keep separate dialects. Do not substitute the managed executable or assume `session/load` for AgentOS’s `agy acp` launcher. [A: agy_acp_runtime.py:80](D:/AgentOS/backend/pyworker/runtime/agy_acp_runtime.py:80), [R: antigravity.ts:62–75](D:/rusebot/server/drivers/antigravity.ts:62) |
| **Transport migration could broaden tool authority.** | Carry explicit tool policy in every turn and verify effective mode/model before sending prompts. Preserve current locked agents, scoped MCP, and ACP plan mode. [A: opencode_runtime.py:598–605](D:/AgentOS/backend/pyworker/runtime/opencode_runtime.py:598), [A: agy_acp_runtime.py:420–445](D:/AgentOS/backend/pyworker/runtime/agy_acp_runtime.py:420) |
| **Native approval accepted in SQLite but not delivered.** | Store decision and delivery separately; return unavailable on stale requests and never silently retry approvals across process generations. |
| **Questions exceed current approve/deny UI.** | Add structured answer support before advertising the capability; otherwise explicitly cancel questions. [A: convo.py:565–571](D:/AgentOS/backend/pyworker/api/convo.py:565) |
| **Prompt replay can repeat side effects.** | Default automatic retry to failures proven pre-submission. Surface uncertainty after timeout/EOF; require a new user turn for retry. RuseBot’s recovery already tracks submission/output boundaries. [R: codex.ts:1433–1440](D:/rusebot/server/drivers/codex.ts:1433) |
| **Usage semantics differ across engines.** | Store scoped replacement snapshots; ledger only final turn consumption. Never sum context/session snapshots. RuseBot explicitly warns that token-update meanings differ. [R: runtime-events.ts:62–65](D:/rusebot/shared/runtime-events.ts:62) |
| **Full snapshots and deltas can duplicate text.** | Per-item accumulation plus authoritative completion; final reply selected by terminal item IDs. Claude and Codex both suppress fallback deltas after streaming. [R: claude.ts:1430–1433](D:/rusebot/server/drivers/claude.ts:1430), [R: codex.ts:1149–1156](D:/rusebot/server/drivers/codex.ts:1149) |
| **Persistent sessions can duplicate application-supplied history.** | Use native sessions for ordinary conversation turns first; keep CEO/mission compatibility calls isolated until their prompt builders become incremental. [A: agent_loop.py:142–155](D:/AgentOS/backend/pyworker/orchestration/agent_loop.py:142) |
| **Taskkill alone cannot provide crash-proof descendant ownership.** | Implement Job Object supervision before claiming a strict Windows cleanup guarantee. Retain taskkill as a verified fallback, and report cleanup failures. |
| **Shared ACP processes enlarge cancellation blast radius.** | Begin with one process per logical session; add multiplexing only with session routing and failure-isolation tests. RuseBot’s OpenCode sharing is optional configuration, not a requirement. [R: opencode-go.ts:715–716](D:/rusebot/server/drivers/acp/opencode-go.ts:715) |
| **Event persistence may grow rapidly.** | Coalesce adjacent text deltas within a bounded short interval before assigning event sequence numbers; bound tool previews and diagnostics. Persist all admitted events and expose replay without dropping text. |
| **Health and catalogue results can be mistaken for execution readiness.** | Report executable/configuration health separately from authenticated model availability; do not run a model merely to populate health. Preserve unknown states instead of inventing success. |
| **Exactly-once terminal delivery across crashes is impossible at the driver alone.** | Promise one in-process settlement and one durable terminal after reconciliation. Keep run-state arbitration and startup recovery in the manager. [A: conversation_runs.py:265–279](D:/AgentOS/backend/pyworker/core/conversation_runs.py:265), [A: conversation_runs.py:585–619](D:/AgentOS/backend/pyworker/core/conversation_runs.py:585) |