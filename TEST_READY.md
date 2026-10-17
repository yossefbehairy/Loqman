# AgentOS — Test Suite Readiness & Execution Guide (`TEST_READY.md`)

## 1. Test Suite Status
The comprehensive End-to-End (E2E) Playwright testing track for AgentOS is **fully authored, validated, and green** against the current UI (M1 through M6 verification).

- **Total E2E Test Files**: 9 files
- **Total E2E Test Cases**: 47 tests
- **Last Full Suite Result**: `47 passed` (0 failed, 0 skipped) with `RUNTIME_MODE=mock`
- **Linter Status**: 0 errors, 0 warnings (`oxlint`)
- **TypeScript Status**: Clean compilation (`tsc -b`)
- **Vite Build**: Success (`vite build`)
- **Authoritative Requirements Reference**: `d:\AgentOS\.agents\teamwork\ORIGINAL_REQUEST.md`
- **Infrastructure Architecture Reference**: `d:\AgentOS\TEST_INFRA.md`

The workspace launches clean: one CEO (`Adel Mansour`) and no department teams. Every spec seeds the departments, specialists and delegated missions it needs through the real UI, so runs are order-independent and need no manual fixture setup beyond the mock backend's seeded employee profiles.

---

## 2. Test Runner Commands

### 2.1 Complete E2E Suite Execution
The mock runtime is mandatory: it serves deterministic, zero-cost model responses (`DeepSeek V4.1 Flash Free (Mock)`) and the seeded employee profiles.
```powershell
cd d:\AgentOS\frontend
$env:RUNTIME_MODE='mock'
npm test
# OR
npm run test:e2e
```

### 2.2 Milestone & Requirement-Specific Runners
Run targeted specs matching individual feature tracks (set `$env:RUNTIME_MODE='mock'` first):

| Requirement / Feature Track | Target Spec File | Tests | Execution Command |
|-----------------------------|------------------|-------|-------------------|
| **R1 (Executive Chat & Navigation)** | `frontend/e2e/r1_executive_chat.spec.ts` | 4 | `npx playwright test e2e/r1_executive_chat.spec.ts` |
| **R2 (Organization & Hierarchy)** | `frontend/e2e/r2_organization_hierarchy.spec.ts` | 4 | `npx playwright test e2e/r2_organization_hierarchy.spec.ts` |
| **R3 (Contracts & Zero-Cost Models)** | `frontend/e2e/r3_contracts_and_prompts.spec.ts` | 4 | `npx playwright test e2e/r3_contracts_and_prompts.spec.ts` |
| **R4 (Interactive Agent Map & Halos)** | `frontend/e2e/r4_interactive_agent_map.spec.ts` | 3 | `npx playwright test e2e/r4_interactive_agent_map.spec.ts` |
| **R5 (Team Chat Stream & Handovers)** | `frontend/e2e/r5_team_chat_stream.spec.ts` | 5 | `npx playwright test e2e/r5_team_chat_stream.spec.ts` |
| **Obsidian Physics Graph** | `frontend/e2e/obsidian_physics_graph.spec.ts` | 4 | `npx playwright test e2e/obsidian_physics_graph.spec.ts` |
| **Workspace UI Craft Sweep** | `frontend/e2e/agentos_ui_craft.spec.ts` | 10 | `npx playwright test e2e/agentos_ui_craft.spec.ts` |
| **CEO Control Plane** | `frontend/e2e/ceo_control_plane.spec.ts` | 6 | `npx playwright test e2e/ceo_control_plane.spec.ts` |
| **Legacy Studio (DAG Canvas & Team Chat)** | `frontend/e2e/studio.spec.ts` | 7 | `npx playwright test e2e/studio.spec.ts` |

### 2.3 Backend Pytest Execution
Run all 142 backend integration and unit tests:
```powershell
cd d:\AgentOS
d:\AgentOS\backend\.venv\Scripts\pytest.exe backend\tests -v
```

---

## 3. Comprehensive E2E Coverage Breakdown

### Suite 1: `frontend/e2e/r1_executive_chat.spec.ts` (R1 Coverage, 4 tests)
1. **Clean Minimalist Landing**: Verifies the app opens into the Company workspace with the sidebar shell ready, without legacy canvas clutter (`Start Trigger`, `Lead Planner`, `Validate DAG`, `Run Pipeline` hidden); the conversation composer is one navigation away.
2. **Persona List Selection**: Verifies the Conversations agent list offers `Adel Mansour` plus the seeded managers `د. سارة`, `م. طارق`, `أ. ياسمين`, and that selecting a persona rewires the active chat header (`OpenCode · {name}`).
3. **Delegated Manager Consultation**: Submits an explicit delegation message to `د. سارة` and verifies the user turn, the assignment acknowledgement naming the assignee, and the completed employee task report in the same thread.
4. **Seamless 7-View Sidebar Navigation**: Validates 1-click switching across Company, Conversations, Team Stream, Results, Calendar, Settings and Inbox with no full page reloads.

### Suite 2: `frontend/e2e/r2_organization_hierarchy.spec.ts` (R2 Coverage, 4 tests)
1. **Three Core Department Desks**: Creates Planning & Strategy, Research & Feasibility and Quality & Review through the New team modal; header stats (`0 specialists · 1 CEO · 3 managers · 3 teams`) and the org chart confirm each desk.
2. **Visual Hierarchy & Role Tags**: Verifies managers carry the amber `Manager` tag, the CEO the mono `Executive` tag in the detail inspector, and specialists the green `Specialist` tag.
3. **Natural Naming Support**: Verifies authentic Arabic manager and specialist names render across the org chart (`د. سارة`, `م. طارق`, `أ. ياسمين`, `أحمد`, `خالد`, `ليلى`).
4. **Department Focus Chips**: Validates that a chip click activates focus (`border-[var(--acc)]`) and dims the other desk cards (`opacity-40`); a second click clears focus and the dimming.

### Suite 3: `frontend/e2e/r3_contracts_and_prompts.spec.ts` (R3 Coverage, 4 tests)
1. **Contract Surface Display**: Verifies the Company view renders the department desk and member; selecting `د. سارة` opens the detail inspector with `Model` / `System Prompt` metadata and the `Edit contract` action; the modal prefills her name and role title.
2. **System Prompt Editing**: Edits the System Prompt, saves, asserts the success toast, and reopens the contract to confirm the value persisted.
3. **Execution Contract Parameters**: Switches Strict JSON Output to `no`, sets the timeout to 90s, verifies both persisted on reopen, and checks the retry bound under Settings → "Retries on failure" (`min=0`, `max=5`).
4. **Zero-Cost Models & Reasoning Variants**: Verifies the Model dropdown lists `deepseek-v4.1-flash-free` and `muse-spark-1.3-contributor-free`, and the Reasoning dropdown lists `low`, `high`, `max`.

### Suite 4: `frontend/e2e/r4_interactive_agent_map.spec.ts` (R4 Coverage, 3 tests)
1. **Map Canvas & Department Zones**: Verifies the Company graph canvas mounts with HUD controls (`Zoom in`, `Reheat simulation`) and the three department focus chips; since react-force-graph draws nodes on the canvas rather than the DOM, member counts are asserted from the header stats.
2. **Completed Task Duration**: Runs a delegated mission to completion and verifies its Inbox review item reports the execution sub-line `planner · succeeded · deepseek-v4.1-flash-free · {N}ms`.
3. **Node Contract Card & Output Preview**: Selects a member in the org chart (the accessible selection surface for canvas nodes) and verifies the contract card (`Model`, `System Prompt`, `Reasoning`) plus the active-task dialog with its deliverable output tab.

### Suite 5: `frontend/e2e/r5_team_chat_stream.spec.ts` (R5 Coverage, 5 tests)
1. **Chronological Feed Shell**: Verifies the Team Dialogue Stream renders its header, live status (`Stream Ready`), overview metrics, search box, and the recorded-turns empty state until tasks report.
2. **Recorded Handover Progression**: After a delegated mission, verifies the Inbox review modal lists the recorded handover turn (`Turn 1`, sender `(planner)`, recipient `CEO`) with its deliverable summary.
3. **Expandable JSON Deliverables**: Verifies the output tab shows `Structured Deliverable JSON Schema`, the JSON payload containing `"output"`, and the `Copy JSON` action.
4. **Live Feed Ordering**: Verifies mission updates arrive in the conversation feed in arrival order: user ask → assignment acknowledgement → completion report.
5. **Team Stream Ledger**: After a delegated mission completes, verifies the Team Stream itself records the real handover — 3 turns (delegation → deliverable → synthesis) with sender labels (`Adel Mansour` ↔ the assigned employee), the real instruction text, the mock planner deliverable (`key_objective`), and the expandable attached payload (`"output"`) — all backed by the employee's `activeTask`.

### Suite 6: `frontend/e2e/obsidian_physics_graph.spec.ts` (4 tests)
1. **Canvas Mount & Physics Status**: Verifies the `react-force-graph-2d` canvas, the "Obsidian Physics Graph" indicator, and the canvas HUD (`Zoom in/out`, `Reset camera`, `Reheat simulation`, `Physics controls`).
2. **Forces Tuning Drawer**: Opens Obsidian Physics (Forces), verifies the repulsion / link distance / center gravity controls plus `Neural Pulse Speed` and `Always Show Labels`, moves sliders and reheats the simulation.
3. **Camera Controls**: Verifies zoom in, zoom out, reheat and reset camera keep the canvas functional.
4. **Department Chip Highlighting**: With two desks created through the UI, verifies focusing each chip activates it and re-clicking clears focus.

### Suite 7: `frontend/e2e/agentos_ui_craft.spec.ts` (10 tests)
1. **Company Shell & Graph Canvas**: Verifies sidebar navigation links, three seeded desks, the graph canvas, department chips and the floating detail inspector.
2. **Graph / Org Chart Switching**: Verifies both company representations render the same people and desks.
3. **Inbox Reviews & Notices**: Verifies a delegated deliverable lands as a "Waiting for review" item, the Reviews / Notices / All filters behave, and Approve all / Mark all as read report their toasts.
4. **Conversations Live Mission**: Sends a delegation to `د. سارة` and verifies the assignment acknowledgement and completion report arrive live.
5. **Results Metrics**: Verifies the run metrics cards and the Execution Run Receipts table render.
6. **Calendar & Settings**: Verifies the scheduling empty state renders and Settings shows the OpenCode engine online.
7. **Contract & Help Modals**: Verifies the contract modal opens prefilled and cancels cleanly, and Help opens with the `h` shortcut and closes with "Got it".
8. **Inbox Review Inspection**: Clicks a review card and verifies the inspection modal opens on the inter-agent dialogue with the structured output tab.
9. **Agent Active Task Inspector**: Verifies the org chart inspector's "View active task & dialogue" action opens the agent's active work, dialogue summary and empty deliverable payload state.
10. **Team Stream Shell**: Verifies the stream header, live status, metrics and recorded-turns empty state render.

### Suite 8: `frontend/e2e/ceo_control_plane.spec.ts` (CEO Control Plane, 6 tests)
1. **Executive-First Navigation & Surfaces**: Verifies the CEO entry leads the sidebar, and opening it shows the `CEO Control Plane` heading, the ask surface (`اسأل المدير العام`, textarea `سؤال للمدير العام`) and the approvals inbox (`صندوق الاعتماد`).
2. **Ask Over Live Data → Pending Proposal**: Asks `اعمل فريق مراجعة أمنية` and verifies the deterministic mock reply (`Based on live company data`) renders in the CEO reply region, a `بانتظار اعتمادك` chip appears, and the proposal is recorded in the approvals inbox as `معلّق` (count `before + 1`; never auto-executed).
3. **Gated Approval**: Verifies `اعتماد` opens the in-app confirmation strip (`تأكيد اعتماد هذا الإجراء؟ سيُنفَّذ فوراً.`); cancel decides nothing; confirming executes the action, shows `تم اعتماد الإجراء وتنفيذه.`, removes the card from the pending list, and files it under the `معتمدة` tab as `معتمد`.
4. **Approved Team Materializes in the Company**: After approving the team proposal, opens the sidebar `Company` view and switches to `Org chart`, then verifies the new team (`Security Review Team (فريق المراجعة الأمنية)`) and all three created members (`Hassan (حسن)`, `Farida (فريدة)`, `Kareem (كريم)`) are present — the approve response's `created_profiles`/`team_name` are materialized into the local company state.
5. **Gated Denial**: Verifies `رفض` shows `تأكيد رفض هذا الإجراء؟ لن يُنفَّذ شيء.`; confirming reports `تم رفض الإجراء — لم يُنفَّذ شيء.`, removes the card from pending, and files it under the `مرفوضة` tab as `مرفوض`.
6. **Empty Question Guard**: Verifies send stays disabled for empty and whitespace-only questions (no reply region ever appears) and enables only for a real question.

### Suite 9: `frontend/e2e/studio.spec.ts` (Legacy Studio, 7 tests)
1. **Demo Pipeline Canvas**: Verifies the `AgentOS` / `Studio` header, `Validate DAG` and `Run Pipeline` controls, the 5 demo nodes (`Start Trigger` → `Lead Planner` → `Risk Analyst` → `Quality Reviewer` → `Capstone Deliverable`) and the `muse-spark-1.3-contributor-free` free-model badge.
2. **Node Inspector**: Clicking `Lead Planner` opens the Node Inspector with the `AI Employee Role Library`, the free-contributor privacy notice and the Quick / Standard / Deep task-depth controls.
3. **DAG Validation**: `Validate DAG` runs the real client-side topological check (Kahn's algorithm plus terminal/reachability checks) against the demo graph without raising the `DAG Graph Validation Issues` overlay.
4. **Pipeline Run & Team Chat**: `Run Pipeline` saves the graph through the workflows API, starts a real backend execution, opens the Execution Drawer and streams the persisted team-chat turns (Turn 1-3) into the `Live Team Chat & Handover Stream`; the Node Outputs tab lists the `Pipeline Stages`.
5. **Execution History**: The `History` modal lists previous runs from SQLite with `Replay / Inspect`.
6. **Studio Palette**: The floating palette's `Add AI Agent Node` appends a `New Agent` node and opens its inspector.
7. **Reasoning Variants**: Selecting `deepseek-v4.1-flash-free` in the inspector re-renders the native reasoning variant dropdown from the live model catalog (low/high/max).

