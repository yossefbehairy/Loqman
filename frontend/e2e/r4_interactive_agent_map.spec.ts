import { test, expect } from '@playwright/test';
import {
  DEPARTMENTS,
  addSpecialist,
  createTeam,
  expectAssignedAck,
  expectTaskCompletion,
  gotoCompany,
  inspectDialog,
  openNewestInboxReview,
  seedDepartments,
  sendDelegatedMission,
  switchCompanyView,
  teamChip,
} from './helpers';

/**
 * R4: Live Interactive Agent Map & Execution Halos.
 *
 * The Interactive Agent Map is now the Company graph canvas
 * (react-force-graph). Departments are focus chips/zone groupings; node
 * selection is reachable through the org chart; completed executions surface
 * their duration (ms) in the Inbox review feed.
 */
test.describe('R4: Live Interactive Agent Map & Execution Halos', () => {
  test('Renders interactive map canvas with department zone groupings/boundaries', async ({
    page,
  }) => {
    await seedDepartments(page, ['planning', 'research', 'quality']);

    // 1. Verify the graph canvas container is rendered
    const mapContainer = page.locator('canvas').first();
    await expect(mapContainer).toBeVisible();

    // 2. Verify department zone groupings (focus chips) for the three desks
    await expect(teamChip(page, 'Planning & Strategy')).toBeVisible();
    await expect(teamChip(page, 'Research & Feasibility')).toBeVisible();
    await expect(teamChip(page, 'Quality & Review')).toBeVisible();

    // 3. Verify all agents are present as graph nodes — the canvas draws
    // nodes outside the DOM, so member counts come from the header stats.
    await expect(page.getByText('0 specialists · 1 CEO · 3 managers · 3 teams')).toBeVisible();

    // Canvas HUD stays available for map interactions
    await expect(page.getByRole('button', { name: 'Zoom in' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Reheat simulation' })).toBeVisible();
  });

  test('Completed tasks report ms duration after the running phase', async ({ page }) => {
    test.setTimeout(120000);
    await seedDepartments(page, ['planning']);

    // Trigger a real employee run through the delegated mission flow
    await sendDelegatedMission(page, 'Delegate this mission to planner: مراجعة مخاطر التنفيذ وتحديث خطة الإطلاق');
    await expectAssignedAck(page);
    await expectTaskCompletion(page);

    // Completion is reported in the Inbox review feed with its duration in ms
    const reviewCard = await openNewestInboxReview(page);
    await expect(reviewCard.getByText('Waiting for review')).toBeVisible();
    await expect(
      reviewCard.getByText(/planner · succeeded · deepseek-v4\.1-flash-free · \d+ms/)
    ).toBeVisible();
  });

  test('Clicking a node opens Contract Card and output preview', async ({ page }) => {
    await gotoCompany(page);
    const planning = DEPARTMENTS.planning;
    await createTeam(page, planning.name, planning.manager, planning.managerRole);
    await addSpecialist(page, 'أحمد', 'أخصائي البرمجة والتنفيذ');
    await switchCompanyView(page, 'org');

    // Select the agent node (canvas nodes have no DOM; org chart is the
    // accessible selection surface for the same people).
    await page.getByRole('button', { name: /أحمد/ }).first().click();

    // Verify the contract card panel opens with execution metadata
    const panel = page.locator('main aside.os-card');
    await expect(panel.getByText('Model', { exact: true })).toBeVisible();
    await expect(panel.getByText('System Prompt', { exact: true })).toBeVisible();
    await expect(panel.getByText('Reasoning', { exact: true })).toBeVisible();

    // Verify the output preview is accessible from the active-task inspector
    await page.getByRole('button', { name: /View active task & dialogue/ }).click();
    const dialog = inspectDialog(page);
    await expect(dialog.locator('h2')).toContainText('Active Work & Coordination');
    await dialog.getByRole('button', { name: /المخرجات والتقرير الفني/ }).click();
    await expect(dialog.getByText('Structured Deliverable JSON Schema').or(dialog.getByText('No structured JSON payload attached to this task.'))).toBeVisible();
    await dialog.getByRole('button', { name: 'Close', exact: true }).click();
  });
});
