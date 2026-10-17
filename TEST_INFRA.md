# AgentOS — Enterprise Test Infrastructure & Quality Architecture

## 1. Executive Summary & Quality Mission

AgentOS is a next-generation Virtual AI Organization Studio transforming multi-agent execution into a sleek, Grok/ChatGPT-inspired conversational and hierarchical workspace. Enterprise organizations rely on AgentOS to coordinate mission-critical workflows across specialized AI departments (Planning & Strategy, Research & Feasibility, Quality Assurance & Review).

To guarantee enterprise-grade stability, zero-regression deployments, and seamless user experiences, AgentOS enforces a **Multi-Tiered Test Engineering Architecture** spanning unit validation, database integrity, inter-service contracts, live mission telemetry, and end-to-end browser automation with Playwright.

---

## 2. Test Philosophy & The 5-Tier Testing Pyramid

AgentOS adheres to the **Agentic SDLC Quality Standards**, rejecting brittle superficial checks in favor of requirement-driven, opaque-box, and adversarial verification.

```
       ▲
      / \     Tier 5: Adversarial, Resiliency & Chaos Verification
     /---\    Tier 4: Live Mission Telemetry, Durations & Handovers
    /-----\   Tier 3: Playwright End-to-End User Journeys (R1 - R5)
   /-------\  Tier 2: Backend API Contracts & SQLite Integrity
  /---------\ Tier 1: Core Unit Logic, Sanitization & State Machines
```

### Tier 1: Core Unit Logic, Sanitization & State Machines
- **Scope**: Core algorithms, prompt formatting, output sanitizers, and workflow state machine transitions.
- **Execution Target**: Pytest (`backend/tests/test_context_and_sanitizer.py`, `backend/tests/test_state_machine.py`, `backend/tests/test_validator.py`).
- **Characteristics**: Instantaneous (< 50ms), zero network overhead, deterministic in-memory fixtures.

### Tier 2: Backend API Contracts & Database Integrity
- **Scope**: REST endpoints, SQLite foreign key cascades, transaction atomicity, agent profile persistence, and direct manager consultation APIs (`POST /api/chat`, `PUT /api/agent-profiles/{id}`).
- **Execution Target**: Pytest (`backend/tests/test_api.py`, `backend/tests/test_db.py`, `backend/tests/test_team_chat.py`, `backend/tests/test_sse_and_api.py`). The current suite runs 143 tests.
- **Guarantees**: Verifies strict JSON serialization, SQLite WAL mode, foreign key enforcement (`PRAGMA foreign_keys=ON`), and persona seeded records.

### Tier 3: Playwright End-to-End User Journeys (R1 through R5)
- **Scope**: Real browser automation simulating executive and operator journeys across the Chromium engine.
- **Execution Target**: Playwright Test (`frontend/e2e/r1_executive_chat.spec.ts` through `r5_team_chat_stream.spec.ts`, plus `frontend/e2e/obsidian_physics_graph.spec.ts`, `frontend/e2e/agentos_ui_craft.spec.ts` and `frontend/e2e/ceo_control_plane.spec.ts`). The suite runs 40 tests across 8 spec files.
- **Guarantees**: Real DOM inspection, reactive state transitions, role tag styling and hierarchy prominence, conversation delegation flows, modal forms, and sidebar navigation without page reloads.

### Tier 4: Live Mission Telemetry, Durations & Handovers
- **Scope**: Delegated missions create real backend employee tasks. The client polls mission state every 2.5s and appends running and completion reports to the conversation feed; finished tasks deliver review items with recorded handover turns to the Inbox.
- **Execution Target**: Playwright driving the full delegation flow (`POST /api/missions/delegate` → poll `GET /api/missions/{id}` → Inbox review inspection).
- **Guarantees**: Arrival-order conversation updates (ask → assignment acknowledgement → completion report), execution duration in milliseconds on completed review items, and inspectable per-task handover turns. Each delegation records Turn 1 on the target employee's `agent.activeTask` the moment it is dispatched, and the terminal poll fills in the real deliverable (Turn 2) and the manager's synthesis or factual failure/cancel status (Turn 3) — so the Team Dialogue Stream ledger shows the real delegation → deliverable → synthesis progression with sender labels.

### Tier 5: Adversarial Resiliency & Boundary Hardening
- **Scope**: Edge cases, Unicode/Arabic natural name handling (`د. سارة`, `م. طارق`, `أ. ياسمين`), delegation grammar correctness, focus-chip state transitions, rapid view switching, and modal cancellation flows.
- **Guarantees**: The application gracefully recovers from interrupted missions, invalid inputs, and unexpected state transitions without crashing.

---

## 3. Feature Inventory & Requirements Traceability Matrix

Every requirement outlined in `ORIGINAL_REQUEST.md` is strictly traceable to an authoritative test specification:

| Requirement ID | Feature Specification | Implementation Component | Authoritative E2E Test Suite | Target Milestone |
|----------------|----------------------|--------------------------|------------------------------|------------------|
| **R1.1** | Minimalist Landing: clean workspace with no canvas clutter | `frontend/src/components/company/CompanyView.tsx` | `frontend/e2e/r1_executive_chat.spec.ts` | M2 |
| **R1.2** | Persona Selection: CEO and managers in the Conversations agent list | `frontend/src/components/conversations/ConversationsView.tsx` | `frontend/e2e/r1_executive_chat.spec.ts` | M2 |
| **R1.3** | Manager Consultation & Delegated Mission Flow | `ConversationsView.tsx`, `backend/app/api/routes/missions.py` | `frontend/e2e/r1_executive_chat.spec.ts` | M1, M2 |
| **R1.4** | Sidebar Navigation: 1-click switching across all 7 views | `frontend/src/App.tsx` (inline sidebar nav) | `frontend/e2e/r1_executive_chat.spec.ts` | M2 |
| **R2.1** | Hierarchical Department Structure (Planning, Research, QA) | `CompanyView.tsx`, `frontend/src/components/company/CompanyOrgChart.tsx` | `frontend/e2e/r2_organization_hierarchy.spec.ts` | M3 |
| **R2.2** | Visual Designation: `Manager` / `Executive` / `Specialist` role tags | `CompanyView.tsx` inspector (`os-tag-amber` / `os-tag-mono` / `os-tag-green`) | `frontend/e2e/r2_organization_hierarchy.spec.ts` | M3, M4 |
| **R2.3** | Natural Naming (د. سارة, م. طارق, أ. ياسمين, أحمد, خالد, ليلى) | `backend/app/runtime/mock_runtime.py`, `frontend/src/components/modals/NewTeamModal.tsx` | `frontend/e2e/r2_organization_hierarchy.spec.ts` | M1, M3 |
| **R3.1** | Custom System Prompt Editing | `frontend/src/components/modals/ContractModal.tsx` | `frontend/e2e/r3_contracts_and_prompts.spec.ts` | M3 |
| **R3.2** | Strict Execution Contract (Strict JSON, Retry Bound 0-5, Timeout) | `ContractModal.tsx`, `frontend/src/components/settings/SettingsView.tsx`, `PUT /api/agent-profiles/{id}` | `frontend/e2e/r3_contracts_and_prompts.spec.ts` | M1, M3 |
| **R3.3** | Zero-Cost Model Catalog & Reasoning Variants (DeepSeek Free, Muse Spark; low, high, max) | `backend/app/api/routes/models.py`, `ContractModal.tsx` | `frontend/e2e/r3_contracts_and_prompts.spec.ts` | M1, M3 |
| **R4.1** | Interactive Agent Map with Department Zones/Groupings | `frontend/src/components/company/CompanyGraphCanvas.tsx`, `CompanyView.tsx` (focus chips) | `frontend/e2e/r4_interactive_agent_map.spec.ts` | M4 |
| **R4.2** | Execution Telemetry: completed duration in ms | `frontend/src/components/inbox/InboxView.tsx` review meta | `frontend/e2e/r4_interactive_agent_map.spec.ts` | M4 |
| **R4.3** | Node Contract Inspection & Deliverable Preview | `frontend/src/components/company/AgentDetailPanel.tsx`, `frontend/src/components/modals/TaskInspectionModal.tsx` | `frontend/e2e/r4_interactive_agent_map.spec.ts` | M4 |
| **R5.1** | Chronological Inter-Agent Feed Shell (Slack/Discord style) | `frontend/src/components/stream/TeamStreamView.tsx` | `frontend/e2e/r5_team_chat_stream.spec.ts` | M5 |
| **R5.2** | Recorded Handover Progression (delegation turn, deliverable, sender labels) | `TaskInspectionModal.tsx`, `backend/app/runtime/mock_runtime.py` | `frontend/e2e/r5_team_chat_stream.spec.ts` | M5 |
| **R5.3** | Expandable JSON Deliverables & Live Feed Ordering | `TaskInspectionModal.tsx`, `ConversationsView.tsx` mission polling | `frontend/e2e/r5_team_chat_stream.spec.ts` | M5 |
| **G1** | Obsidian-Style Physics Graph (repulsion, link distance, gravity, pulse, labels) | `CompanyGraphCanvas.tsx` | `frontend/e2e/obsidian_physics_graph.spec.ts` | M4 |
| **C1** | Workspace Craft Sweep (shell, Inbox, Results, Settings, modals, stream) | `App.tsx` shell + `frontend/src/components/*` views | `frontend/e2e/agentos_ui_craft.spec.ts` | M2-M6 |
| **C2** | CEO Control Plane: ask over live company data + human-confirmed proposals (P1-P3), with approved teams materialized into the Company view | `frontend/src/components/ceo/CeoControlView.tsx`, `frontend/src/api/client.ts`, `frontend/src/App.tsx`, `backend/app/api/routes/ceo.py` | `frontend/e2e/ceo_control_plane.spec.ts` | P1-P3 |

### P3 approval → Company materialization
Approving a `new_team` / `new_profile` action returns the really-created profile rows (`created_profiles`) and the proposed `team_name` in `ActionOut`. `CeoControlView.tsx` forwards them through `onProfilesCreated`; `App.tsx` re-fetches `/api/agent-profiles` for full contracts (falling back to the shallow response rows if that fetch fails) and appends one `Team` plus one `Agent` per profile to the local company state that feeds the Company graph and org chart. The dedicated E2E case asserts the approved team and all three members appear in the org chart right after approval.

---

## 4. Test Architecture & Harness Design

### 4.1 Playwright Test Configuration
- **Runner**: `@playwright/test` v1.64.1, `testDir: ./e2e`, single Chromium project (headless)
- **Base URL**: `http://127.0.0.1:5173` (Vite dev server, proxying `/api` to the backend on `http://127.0.0.1:8000`)
- **Determinism**: `workers: 1`, `retries: 0`, `trace: on-first-retry`
- **Managed Servers**: the config auto-starts both the Vite dev server and `uvicorn backend.app.main:app` (with `reuseExistingServer: true`)
- **Runtime Mode**: export `RUNTIME_MODE=mock` before any run; the mock runtime serves deterministic, zero-cost responses and the free-model catalog
- **Isolation Principle**: each test opens a fresh browser context with clean `localStorage`; specs seed their own teams and missions through the UI

### 4.2 Resilient Locator Strategy
To avoid fragile DOM pathing, tests prioritize:
1. ARIA role locators: `page.getByRole('button', { name: '...' })`, `page.getByRole('heading', { ... })`, `page.locator('[role="dialog"]')`
2. Semantic text and placeholder locators: `page.getByText('...', { exact: true })`, `textarea[aria-label="Message text"]`
3. Label-scoped form controls in the contract modal: `div:has(> label:text-is("Model")) > select`
4. State markers where the semantics live in classes: role tags (`os-tag-amber`, `os-tag-mono`, `os-tag-green`), active focus chips (`border-[var(--acc)]`), dimmed desks (`opacity-40`)
5. Shared flows in `frontend/e2e/helpers.ts`: department seeding via the New team modal, specialist creation, the delegation + acknowledgement + completion wait chain, Inbox review opening, and the task inspection dialog

Because react-force-graph draws nodes onto a canvas, graph nodes are not DOM-addressable; tests select the same people through the org chart, which is the accessible selection surface.

### 4.3 Deterministic AI Runtime Harness
Tests execute with `RUNTIME_MODE=mock` enabled in the environment. `MockRuntime` returns instant, structured, zero-cost responses and exposes the seeded employee profiles:
- `planner` (د. سارة): milestone breakdowns, scope definitions, execution phases.
- `analyst` (م. طارق): risk matrices, feasibility evaluations, security checkpoints.
- `reviewer` (أ. ياسمين): verification summaries, compliance sign-offs, final verdicts.
- `coder` (أحمد), `researcher` (خالد), `data_extractor` (ليلى): specialist execution profiles.

Manager role titles map to these profiles through keyword rules (تخطيط → planner, مخاطر → analyst, جودة → reviewer), and the mock model catalog advertises `DeepSeek V4.1 Flash Free (Mock)` alongside `muse-spark-1.3-contributor-free`.

A composer message that names a role in the delegation grammar ("Delegate this mission to planner: ...") calls `POST /api/missions/delegate`. The assignment acknowledgement returns immediately, the mission poller reports completion in the same thread, and the deliverable lands in the Inbox for review. This guarantees tests run with 100% determinism without consuming live LLM tokens or incurring network latency.

---

## 5. Real-World Application Scenarios

### Scenario A: Executive Mission Briefing & Delegated Execution
1. The company executive opens AgentOS and lands on the **Company** workspace.
2. The executive opens **Conversations** and selects **د. سارة (Planning Director)** from the agent list.
3. The executive types: *"Delegate this mission to planner: نحتاج خطة متكاملة لتطوير منصة تسجيل الطلاب."*
4. The thread immediately receives the assignment acknowledgement naming د. سارة, then the completion report (`✅ Employee task … succeeded. Its real output is available in Inbox.`).
5. The deliverable appears in the Inbox as a review item awaiting approval.

### Scenario B: Organizational Governance & Contract Enforcement
1. The administrator opens the **Company** view and switches to the **Org chart**.
2. Managers carry the `Manager` tag, the CEO the `Executive` tag, and specialists the `Specialist` tag.
3. The administrator clicks **د. سارة** and edits her Execution Contract in the Contract modal:
   - Updates the System Prompt instructions.
   - Sets Strict JSON Output and a 90-second timeout.
   - Selects `deepseek-v4.1-flash-free` with the `max` reasoning variant.
4. The retry bound lives under **Settings → Retries on failure** (0-5).
5. Saving reports "Contract updated successfully" and the values persist on reopen.

### Scenario C: Live Telemetry on the Company Graph
1. The user opens the **Company** graph: department focus chips group the desks and header stats count the organization.
2. A delegated mission runs through its planning, running and succeeded phases.
3. The finished review item reports its duration in milliseconds (`planner · succeeded · deepseek-v4.1-flash-free · 420ms`).
4. Selecting a member opens the contract card and the active-task dialog with the deliverable output tab.

### Scenario D: Multi-Turn Handover Audit
1. The user opens the **Team Dialogue Stream** to monitor the live inter-agent feed.
2. Each delegated mission records its real handover on the employee agent: **Turn 1** (delegation, manager → employee), **Turn 2** (the deliverable, employee → manager) and **Turn 3** (the manager's synthesis or the factual failure/cancel status, manager → employee) appear in the stream with sender labels.
3. The Inbox review modal records the task's handover turn: **Turn 1** from `(planner)` to `CEO`, carrying the deliverable summary.
4. The user expands the structured deliverable JSON in the output tab.
5. The conversation feed preserves arrival order: user ask → assignment acknowledgement → completion report.

---

## 6. Coverage Thresholds & Quality Gates

| Metric | Target Threshold | Enforcement Mechanism |
|--------|------------------|----------------------|
| **Playwright E2E Suite Pass Rate** | **100%** (0 failures, 0 skipped) | `npm test` with `RUNTIME_MODE=mock` / CI Pipeline |
| **Pytest Backend Suite Pass Rate** | **100%** (0 failures, 0 errors) | `pytest backend/tests` (143 tests) |
| **Requirements Traceability (R1-R5)** | **100%** | Audit against `TEST_INFRA.md` |
| **TypeScript Typecheck** | **0 errors** | `tsc -b` |
| **Linter Compliance** | **0 errors** | `npm run lint` (`oxlint`) |
| **Flakiness Tolerance** | **0 flaky tests** | Deterministic MockRuntime & explicit Playwright wait conditions |

Any deviation from 100% pass rate blocks deployment and milestone advancement.
