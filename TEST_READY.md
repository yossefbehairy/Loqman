# AgentOS — Test Suite Readiness & Execution Guide (`TEST_READY.md`)

## 1. Test Suite Status
The comprehensive End-to-End (E2E) Playwright testing track for AgentOS is **fully authored, validated, and ready** for progressive milestone verification (M1 through M6).

- **Total E2E Test Files**: 6 files
- **Total E2E Test Cases**: 26 tests
- **Linter Status**: 0 errors, 0 warnings (`oxlint`)
- **TypeScript Status**: Clean compilation (`tsc -b`)
- **Vite Build**: Success (`vite build`)
- **Authoritative Requirements Reference**: `d:\AgentOS\.agents\teamwork\ORIGINAL_REQUEST.md`
- **Infrastructure Architecture Reference**: `d:\AgentOS\TEST_INFRA.md`

---

## 2. Test Runner Commands

### 2.1 Complete E2E Suite Execution
Run all 26 tests in the Playwright suite:
```powershell
cd d:\AgentOS\frontend
npm test
# OR
npm run test:e2e
```

### 2.2 Milestone & Requirement-Specific Runners
Run targeted specs matching individual feature tracks:

| Requirement / Milestone | Target Spec File | Execution Command |
|-------------------------|------------------|-------------------|
| **R1 (Executive Chat & Navigation)** | `frontend/e2e/r1_executive_chat.spec.ts` | `npx playwright test e2e/r1_executive_chat.spec.ts` |
| **R2 (Organization & Hierarchy)** | `frontend/e2e/r2_organization_hierarchy.spec.ts` | `npx playwright test e2e/r2_organization_hierarchy.spec.ts` |
| **R3 (Contracts & Zero-Cost Models)** | `frontend/e2e/r3_contracts_and_prompts.spec.ts` | `npx playwright test e2e/r3_contracts_and_prompts.spec.ts` |
| **R4 (Interactive Agent Map & Halos)** | `frontend/e2e/r4_interactive_agent_map.spec.ts` | `npx playwright test e2e/r4_interactive_agent_map.spec.ts` |
| **R5 (Team Chat Stream & Handovers)** | `frontend/e2e/r5_team_chat_stream.spec.ts` | `npx playwright test e2e/r5_team_chat_stream.spec.ts` |
| **Legacy Studio Compatibility** | `frontend/e2e/studio.spec.ts` | `npx playwright test e2e/studio.spec.ts` |

### 2.3 Backend Pytest Execution
Run all 48 backend integration and unit tests:
```powershell
cd d:\AgentOS
d:\AgentOS\backend\.venv\Scripts\pytest.exe backend\tests -v
```

---

## 3. Comprehensive E2E Coverage Breakdown

### Suite 1: `frontend/e2e/r1_executive_chat.spec.ts` (R1 Coverage)
1. **Clean Minimalist Landing**: Verifies application opens immediately into the Executive Chat view without canvas nodes or toolbar clutter (`Start Trigger`, `Lead Planner`, and DAG controls hidden).
2. **Persona Selector Pill Selection**: Verifies switching between `👑 General Manager`, `👔 Dr. Sara` (د. سارة), `👔 Eng. Tarek` (م. طارق), and `👔 Prof. Yasmin` (أ. ياسمين).
3. **Direct Consultation & Guidance**: Submits user goal/mission in Arabic/English to manager and verifies direct structured guidance reply.
4. **Seamless 5-View Sidebar Navigation**: Validates 1-click view switching (`chat`, `company`, `map`, `stream`, `settings`) and collapsible sidebar toggle without full page reloads.

### Suite 2: `frontend/e2e/r2_organization_hierarchy.spec.ts` (R2 Coverage)
1. **Three Core Departments**: Verifies rendering and segmentation of Planning & Strategy, Research & Feasibility, and Quality Assurance & Review.
2. **Visual Hierarchy & Badges**: Verifies `👑 Manager` golden crown badge prominence over `💼 Specialist` individual contributor badges.
3. **Natural Naming Support**: Verifies rendering of authentic Arabic and English names (`د. سارة`, `م. طارق`, `أ. ياسمين`, `أحمد`, `خالد`, `ليلى`).
4. **Department Filtering Tabs**: Validates department selection tabs cleanly filter and isolate department members.

### Suite 3: `frontend/e2e/r3_contracts_and_prompts.spec.ts` (R3 Coverage)
1. **Contracts Directory Display**: Verifies department members and their assigned execution contracts in the Company & Contracts view.
2. **System Prompt Editing**: Modifies persona instructions / system prompt in modal and confirms persistence.
3. **Execution Contract Parameters**: Verifies strict JSON enforcement toggle, maximum retry attempt bounds (1-3), and timeout duration limits.
4. **Zero-Cost Models & Reasoning Variants**: Validates selection of `deepseek-v4.1-flash-free` and `muse-spark-1.3-contributor-free`, and dynamic reasoning variants (`low`, `high`, `max`).

### Suite 4: `frontend/e2e/r4_interactive_agent_map.spec.ts` (R4 Coverage)
1. **Department Zone Boundaries**: Verifies visual department boundary zones on the interactive map grouping agent nodes.
2. **Status Indicator Halos**: Verifies live execution states — pulsing halos for `running` nodes, emerald green transition with duration in milliseconds (`ms`) upon completion, and warning halos on error.
3. **Node Contract Card & Deliverable Preview**: Verifies clicking any map node opens the Contract Card and displays live/historical deliverable preview.

### Suite 5: `frontend/e2e/r5_team_chat_stream.spec.ts` (R5 Coverage)
1. **Chronological Conversation Feed**: Verifies Slack/Discord-style chronological handover feed.
2. **Multi-Turn Handover Progression**: Verifies Turn 1 manager delegation, Turn 2 employee deliverable, and Turn 3 manager synthesis using natural names.
3. **Expandable JSON Deliverables**: Verifies interactive toggle to expand and inspect structured deliverable JSON blocks.
4. **Live SSE Message Ingestion**: Verifies real-time event streaming and live feed updating without UI stutter.

### Suite 6: `frontend/e2e/studio.spec.ts` (Cohesive Backward Compatibility)
1. **Canvas Pipeline Rendering**: Verifies 5-node demo DAG and free contributor model badge.
2. **Node Inspector & Role Library**: Verifies opening inspector on node click, privacy notice, and task depth controls.
3. **DAG Topological Validation**: Verifies cycle detection and valid graph confirmation.
4. **Pipeline Execution & SSE Drawer**: Verifies end-to-end execution streaming to drawer.
5. **SQLite Execution History**: Verifies execution history modal and past run inspection.
6. **Palette Node Creation**: Verifies adding agent nodes via floating palette.
7. **DeepSeek Reasoning Selection**: Verifies dynamic reasoning variants in inspector dropdown.
