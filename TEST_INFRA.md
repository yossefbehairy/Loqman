# AgentOS — Enterprise Test Infrastructure & Quality Architecture

## 1. Executive Summary & Quality Mission

AgentOS is a next-generation Virtual AI Organization Studio transforming multi-agent execution into a sleek, Grok/ChatGPT-inspired conversational and hierarchical workspace. Enterprise organizations rely on AgentOS to coordinate mission-critical workflows across specialized AI departments (Planning & Strategy, Research & Feasibility, Quality Assurance & Review).

To guarantee enterprise-grade stability, zero-regression deployments, and seamless user experiences, AgentOS enforces a **Multi-Tiered Test Engineering Architecture** spanning unit validation, database integrity, inter-service contracts, live Server-Sent Events (SSE) telemetry, and end-to-end browser automation with Playwright.

---

## 2. Test Philosophy & The 5-Tier Testing Pyramid

AgentOS adheres to the **Agentic SDLC Quality Standards**, rejecting brittle superficial checks in favor of requirement-driven, opaque-box, and adversarial verification.

```
       ▲
      / \     Tier 5: Adversarial, Resiliency & Chaos Verification
     /---\    Tier 4: Live SSE Telemetry, Status Halos & Streaming
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
- **Execution Target**: Pytest (`backend/tests/test_api.py`, `backend/tests/test_db.py`, `backend/tests/test_team_chat.py`, `backend/tests/test_sse_and_api.py`).
- **Guarantees**: Verifies strict JSON serialization, SQLite WAL mode, foreign key enforcement (`PRAGMA foreign_keys=ON`), and persona seeded records.

### Tier 3: Playwright End-to-End User Journeys (R1 through R5)
- **Scope**: Real browser automation simulating complete executive and operator personas across the Chromium engine.
- **Execution Target**: Playwright Test (`frontend/e2e/r1_executive_chat.spec.ts` through `r5_team_chat_stream.spec.ts`, and `frontend/e2e/studio.spec.ts`).
- **Guarantees**: Real DOM inspection, reactive state transitions, CSS styling and badge prominence, persona pill selections, modal forms, and collapsible navigation without page reloads.

### Tier 4: Live Telemetry, Status Halos & Streaming
- **Scope**: Real-time asynchronous telemetry, SSE event broadcast and client consumption (`node.status.changed`, `team.message`, `execution.finished`).
- **Execution Target**: Playwright + Pytest integration harnesses.
- **Guarantees**: Real-time pulsing halos during `running` state, emerald green badge transitions on `completed` with duration in milliseconds, and error state indicators.

### Tier 5: Adversarial Resiliency & Boundary Hardening
- **Scope**: Edge cases, Unicode/Arabic natural name handling (`د. سارة`, `م. طارق`, `أ. ياسمين`), malicious script injection prevention in chat composers, rapid view switching, and malformed JSON contract rejection.
- **Guarantees**: The application gracefully recovers from interrupted streams, invalid inputs, and unexpected state transitions without crashing.

---

## 3. Feature Inventory & Requirements Traceability Matrix

Every requirement outlined in `ORIGINAL_REQUEST.md` is strictly traceable to an authoritative test specification:

| Requirement ID | Feature Specification | Implementation Component | Authoritative E2E Test Suite | Target Milestone |
|----------------|----------------------|--------------------------|------------------------------|------------------|
| **R1.1** | Grok/ChatGPT Minimalist Landing (Clean chat, no canvas clutter) | `frontend/src/components/chat/ExecutiveChat.tsx` | `frontend/e2e/r1_executive_chat.spec.ts` | M2 |
| **R1.2** | Executive Persona Selector Pill (👑 General Manager, 👔 Dr. Sara, 👔 Eng. Tarek, 👔 Prof. Yasmin) | `frontend/src/components/chat/ManagerSelectorPill.tsx` | `frontend/e2e/r1_executive_chat.spec.ts` | M2 |
| **R1.3** | Direct Manager Consultation & Goal Briefing | `backend/app/api/routes/chat.py`, `ExecutiveChat.tsx` | `frontend/e2e/r1_executive_chat.spec.ts` | M1, M2 |
| **R1.4** | Collapsible Left Sidebar (Seamless 1-click view switching) | `frontend/src/components/sidebar/Sidebar.tsx` | `frontend/e2e/r1_executive_chat.spec.ts` | M2 |
| **R2.1** | Hierarchical Department Structure (Planning, Research, QA) | `frontend/src/components/company/CompanyContractsView.tsx` | `frontend/e2e/r2_organization_hierarchy.spec.ts` | M3 |
| **R2.2** | Visual Designation (👑 Manager golden crown vs 💼 Specialist badge) | `CompanyContractsView.tsx`, `AgentNode.tsx` | `frontend/e2e/r2_organization_hierarchy.spec.ts` | M3, M4 |
| **R2.3** | User-Defined Natural Naming (د. سارة, م. طارق, أ. ياسمين, أحمد, خالد, ليلى) | `backend/app/runtime/mock_runtime.py`, `defaultCompany.ts` | `frontend/e2e/r2_organization_hierarchy.spec.ts` | M1, M3 |
| **R3.1** | Custom System Prompt Editing | `frontend/src/components/company/ContractEditorModal.tsx` | `frontend/e2e/r3_contracts_and_prompts.spec.ts` | M3 |
| **R3.2** | Strict Execution Contract (JSON Schema, Max Retries 1-3, Timeout 10-300s) | `ContractEditorModal.tsx`, `PUT /api/agent-profiles/{id}` | `frontend/e2e/r3_contracts_and_prompts.spec.ts` | M1, M3 |
| **R3.3** | Zero-Cost Model Catalog & Reasoning Variants (DeepSeek Free, Muse Spark; low, high, max) | `backend/app/api/routes/models.py`, `ContractEditorModal.tsx` | `frontend/e2e/r3_contracts_and_prompts.spec.ts` | M1, M3 |
| **R4.1** | Interactive Agent Map with Department Zones/Boundaries | `frontend/src/components/map/InteractiveAgentMap.tsx`, `DepartmentZone.tsx` | `frontend/e2e/r4_interactive_agent_map.spec.ts` | M4 |
| **R4.2** | Live Execution Telemetry (Pulse halo, emerald green completed with ms, error warning) | `InteractiveAgentMap.tsx`, `AgentNode.tsx`, SSE handler | `frontend/e2e/r4_interactive_agent_map.spec.ts` | M4 |
| **R4.3** | Interactive Node Contract Inspection & Deliverable Preview Modal | `InteractiveAgentMap.tsx`, `ContractCardModal.tsx` | `frontend/e2e/r4_interactive_agent_map.spec.ts` | M4 |
| **R5.1** | Chronological Inter-Agent Team Chat Feed (Slack/Discord style) | `frontend/src/components/stream/TeamChatStreamView.tsx` | `frontend/e2e/r5_team_chat_stream.spec.ts` | M5 |
| **R5.2** | Multi-Turn Handover Progression (Turn 1 Delegation, Turn 2 Deliverable, Turn 3 Synthesis) | `backend/app/engine/executor.py`, `TeamChatStreamView.tsx` | `frontend/e2e/r5_team_chat_stream.spec.ts` | M5 |
| **R5.3** | Expandable JSON Deliverables & SSE Stream Reception | `TeamChatStreamView.tsx`, `GET /api/executions/{id}/events` | `frontend/e2e/r5_team_chat_stream.spec.ts` | M5 |
| **Legacy** | DAG Pipeline Validation & Studio Backward Compatibility | `frontend/src/components/canvas/WorkflowCanvas.tsx` | `frontend/e2e/studio.spec.ts` | M6 |

---

## 4. Test Architecture & Harness Design

### 4.1 Playwright Test Configuration
- **Runner**: `@playwright/test` v1.64.1
- **Target Browser**: Chromium (headless mode)
- **Base URL**: `http://127.0.0.1:5173`
- **Proxy**: Vite dev server proxies `/api` requests to backend at `http://127.0.0.1:8000`.
- **Isolation Principle**: Each test case operates independently. Test setup resets or selects required views cleanly using dedicated data attributes and resilient locators.

### 4.2 Resilient Locator Strategy
To avoid fragile DOM pathing, tests prioritize:
1. ARIA role locators: `page.getByRole('button', { name: '...' })`, `page.getByRole('heading', { ... })`
2. Explicit semantic text locators: `page.locator('text=...')`
3. Data attributes and selector pills: `[data-view="..."]`, `[data-persona="..."]`, `[data-department="..."]`
4. Form controls: `page.locator('textarea[name="system_prompt"]')`, `page.locator('select[name="model"]')`

### 4.3 Deterministic AI Runtime Harness
Tests execute with `RUNTIME_MODE=mock` enabled in the environment. `MockRuntime` returns instant, structured, zero-cost responses tailored to each persona and role:
- `planner` (د. سارة): Delivers milestone breakdowns, scope definitions, and execution phases.
- `analyst` (م. طارق): Delivers risk matrices, feasibility evaluations, and security checkpoints.
- `reviewer` (أ. ياسمين): Delivers verification summaries, compliance sign-offs, and final verdicts.
- `general_manager` (المدير العام): Coordinates organizational objectives and delegates missions.

This guarantees tests run with 100% determinism without consuming live LLM tokens or incurring network latency.

---

## 5. Real-World Application Scenarios

### Scenario A: Executive Mission Briefing & Strategy Alignment
1. The company executive opens AgentOS.
2. The UI lands on the **Executive Chat** with a welcoming consultation greeting.
3. The executive selects **👔 د. سارة (Planning Director)** via the persona selector pill.
4. The executive types: *"Design the technical roadmap for the AI Enterprise Knowledge Base."*
5. Dr. Sara responds instantly with structured phases and recommends dispatching to the Planning Department.

### Scenario B: Organizational Governance & Contract Enforcement
1. The administrator navigates via the left sidebar to **Company & Contracts**.
2. The UI displays the 3 departments: Planning & Strategy, Research & Feasibility, and Quality Assurance & Review.
3. Managers are clearly prominent with the `👑 Manager` golden badge; specialists show the `💼 Specialist` badge.
4. The administrator clicks on **أحمد (Lead Architect)** and edits his Execution Contract:
   - Sets strict JSON validation to `true`.
   - Constrains maximum retry attempts to `2`.
   - Sets timeout limit to `60` seconds.
   - Selects `deepseek-v4.1-flash-free` with `high` reasoning variant.
5. The administrator saves the contract, which persists immediately.

### Scenario C: Live Visual Telemetry on the Interactive Map
1. The user navigates to the **Interactive Agent Map**.
2. Nodes are grouped in visual department boundaries.
3. When an execution runs:
   - Active nodes glow with a pulsing amber/blue halo (`running`).
   - Finished nodes turn emerald green, displaying execution duration in milliseconds (`420 ms`).
   - Clicking any completed node opens the Contract Card and displays the verified deliverable preview.

### Scenario D: Multi-Turn Handover Audit in Team Stream
1. The user navigates to **Team Chat Stream**.
2. A chronological feed displays live handovers:
   - **Turn 1**: Manager د. سارة delegates tasks to engineer أحمد.
   - **Turn 2**: أحمد returns a structured deliverable with architectural stages.
   - **Turn 3**: Manager د. سارة reviews the deliverable and synthesizes approval.
3. The user expands the deliverable card to inspect the raw JSON output.

---

## 6. Coverage Thresholds & Quality Gates

| Metric | Target Threshold | Enforcement Mechanism |
|--------|------------------|----------------------|
| **Playwright E2E Suite Pass Rate** | **100%** (0 failures, 0 skipped) | `npm test` / CI Pipeline |
| **Pytest Backend Suite Pass Rate** | **100%** (0 failures, 0 errors) | `pytest backend/tests` |
| **Requirements Traceability (R1-R5)** | **100%** | Audit against `TEST_INFRA.md` |
| **TypeScript Typecheck** | **0 errors** | `tsc -b` |
| **Linter Compliance** | **0 errors** | `npm run lint` (`oxlint`) |
| **Flakiness Tolerance** | **0 flaky tests** | Deterministic MockRuntime & explicit Playwright wait conditions |

Any deviation from 100% pass rate blocks deployment and milestone advancement.
