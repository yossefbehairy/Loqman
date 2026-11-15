# AgentOS — Test Infrastructure & Quality Architecture

## 1. Executive Summary & Quality Mission

AgentOS is a local-first desktop app for a company of AI agents. Its quality goal is zero-regression delivery with deterministic tests: no test calls a live model, so every run is repeatable on a developer machine and in CI.

Tests run at five layers across the Python worker, the Electron transport, the React UI and the packaging gates. Counts in this document are static counts of test definitions taken when it was written; they are not recorded run results.

---

## 2. Test Philosophy & The Testing Pyramid

AgentOS checks behaviour at the boundary a user or a caller sees, prefers real SQLite files and the real worker process over mocks of internals, and keeps every model call behind a deterministic mock or a fake CLI.

```
       ▲
      / \     Tier 5: Static gates (tsc, oxlint, vite build, secrets scan)
     /---\    Tier 4: Node tests for the Electron transport, worker client and dev bridge
    /-----\   Tier 3: Playwright end-to-end UI tests on the mock runtime
   /-------\  Tier 2: Worker protocol, lifecycle, streams and runtime contract tests (pytest)
  /---------\ Tier 1: Core logic: validators, output checks, state machines (pytest)
```

Live checks with real CLIs and accounts are run by hand and are not part of CI.

### Tier 1: Core Unit Logic
- **Scope**: output validation and output sanitizers.
- **Execution Target**: pytest in `backend/tests/pw/` (`test_context_and_sanitizer.py`, `test_state_machine.py`, `test_validator.py`, `test_output_validation.py`).
- **Characteristics**: pure logic stays in memory; database tests use temporary SQLite files.

### Tier 2: Worker Protocol, Contracts & Database Integrity
- **Scope**: the `agentos-worker/1` JSON Lines protocol, worker start-up and shutdown, stream replay, method handlers (`backend/pyworker/api/`), schema compatibility and the runtime adapters against fake CLIs.
- **Execution Target**: pytest in `backend/tests/` (`test_pyworker_protocol.py`, `test_pyworker_lifecycle.py`, `test_pyworker_streams.py`, `test_pyworker_imports.py`) and in `backend/tests/pw/` (runtime contracts such as `test_claude_runtime_contract.py`, `test_codex_runtime_contract.py`, `test_agy_acp_runtime_contract.py`; plus runs, missions, profiles, workflows, approvals, workspaces and CEO tests).
- **Guarantees**: strict JSON handling, SQLite WAL mode with foreign keys enforced, schema migrations with backups before upgrade, and no partial writes for multi-row operations.

### Tier 3: Playwright End-to-End UI Tests
- **Scope**: the React UI in Chromium, driven through the Vite dev bridge against the mock runtime.
- **Execution Target**: `frontend/e2e/rusebot_ui.spec.ts` (one spec file, 59 tests).
- **Guarantees**: layout and navigation, chat send and stop, conversation isolation, approvals and deliverables, the team map and delegation, agent creation, the AI providers page, and UTC timestamps.

### Tier 4: Node Tests for the Electron Layer
- **Scope**: the stream lifecycle in the renderer transport, the worker client that spawns and talks to the Python worker, and the dev bridge's origin and content-type checks.
- **Execution Target**: `node --test` on `frontend/electron/transport.test.cjs`, `frontend/electron/worker-client.test.cjs` and `frontend/electron/bridge.test.cjs`.

### Tier 5: Static Gates
- **Scope**: TypeScript project build, lint, production build, and secret scanning.
- **Execution Target**: `npx tsc -b`, `npm run lint` (oxlint), `npm run build`, and the Gitleaks job in CI.

---

## 3. Test Inventory

| Area | Location | Runner | Count (static, at time of writing) |
|------|----------|--------|------------------------------------|
| Worker | `backend/tests/`, `backend/tests/pw/` | pytest | 304 test functions in 34 files |
| Electron layer | `frontend/electron/*.test.cjs` | `node --test` | 11 tests in 3 files |
| UI end to end | `frontend/e2e/rusebot_ui.spec.ts` | Playwright | 59 tests in 13 `describe` groups |

### E2E coverage by group
- **Layout**: wide, narrow and mid-size windows; sidebar as a drawer or icon rail; appearance settings (theme, font, chat width) persist.
- **Chat on the real worker**: a message is sent once and its reply restored after reload; Arabic and mixed text keep direction; stop cancels a turn; switching conversations keeps replies in their own chat; failed starts keep the draft; drafts are kept per conversation.
- **Earlier chats**: browser-stored CEO threads from the classic interface open read-only.
- **Approvals and deliverables**: tool approval and denial; failed proposal applies are not shown as approved; deliverables are reviewed, downloaded and listed; runs survive cleared browser storage.
- **Team, contracts and settings**: the team map, Open chat, delegation and delegated-mission status; the contracts list; settings search and profile name.
- **Agents**: only Loqman ships with the app; agents are created by the user or by Loqman, and can be deleted from their panel.
- **AI providers** (engine calls stubbed): rows ordered by state, the install, sign-in and check steps, Send test message, Refresh, default provider and model, and the New agent dialog's engine and model choice.
- **Chat details**: timestamps in local time, long conversations, late responses, the 2,000-character limit for Loqman prompts, and agents keeping their profile.

---

## 4. Test Architecture & Harness Design

### 4.1 Playwright Configuration
- **Runner**: `@playwright/test` (`^1.64.1` in `frontend/package.json`), `testDir: ./e2e`, one Chromium project, headless.
- **Determinism**: `workers: 1`, `retries: 0`, `trace: on-first-retry`, and `fullyParallel: false`.
- **Managed Servers**: the config starts `npm run dev` on `http://127.0.0.1:5173` (reused if already running). The dev bridge spawns the Python worker with `RUNTIME_MODE=mock` and `AGENTOS_DATA_DIR=./.agentos-e2e-data`, so e2e never writes to the real app database.
- **Isolation**: each test starts with a fresh browser context and the browser-stored key `agentos_ceo_threads_v1` set to an empty list.

### 4.2 Locator Strategy
Tests prefer accessible locators: ARIA roles and labels (the UI names its regions and dialogs with `aria-label`), then visible text, and last resort CSS for stable state markers.

### 4.3 Deterministic AI Runtime
- `RUNTIME_MODE=mock` selects `MockRuntime` (`backend/pyworker/runtime/mock_runtime.py`), which returns deterministic replies with no model call.
- The app ships with Loqman as its only agent. Tests that need other agents create them through the UI or through Loqman's own agent creation.
- Engine discovery in the AI providers tests is stubbed, so the page's states are set by the test rather than by the machine running it.

---

## 5. Scenario Coverage

The scenarios that matter most to users are the groups listed in section 3. Each one runs against the same mock runtime, so a failure points at the UI or the worker call that changed, not at a model's output.

---

## 6. Coverage Thresholds & Quality Gates

| Metric | Target | Enforcement Mechanism |
|--------|--------|----------------------|
| **Playwright pass rate** | 100% (0 failures, 0 skipped) | `npx playwright test` in CI (`e2e` job) |
| **Pytest pass rate** | 100%; skips must be explained | `python -m pytest backend/tests -q` in CI (`backend` job) |
| **Node tests** | 100% | `node --test` on the three `frontend/electron` files (`backend` and `frontend` jobs) |
| **TypeScript typecheck** | 0 errors | `npx tsc -b` (`frontend` job) |
| **Linter** | 0 errors (warnings are reported, not blocking) | `npm run lint` (`oxlint`) |
| **Flakiness** | 0 flaky tests | Deterministic mock runtime and explicit Playwright waits |

Any deviation from the target blocks merging.
