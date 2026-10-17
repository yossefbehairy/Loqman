import { test, expect } from '@playwright/test';
import {
  DEPARTMENTS,
  addSpecialist,
  createTeam,
  expectAssignedAck,
  expectTaskCompletion,
  gotoCompany,
  inspectDialog,
  seedDepartments,
  sendDelegatedMission,
  switchCompanyView,
  teamChip,
} from './helpers';

/**
 * AgentOS Impeccable UI Verification.
 *
 * The workspace starts clean (a single CEO). Tests create their departments /
 * specialists through the UI, then drive the real mission flow: a delegation
 * message starts a backend employee task whose acknowledgement and completion
 * report arrive in the same conversation thread and whose deliverable lands in
 * the Inbox for inspection.
 */
test.describe('AgentOS Impeccable UI Verification', () => {
  test('should load company view with graph canvas and floating detail inspector', async ({
    page,
  }) => {
    await page.goto('/#company');
    await expect(page).toHaveTitle('AgentOS');

    // Verify the shell branding/sidebar footer
    await expect(page.getByText('Local workspace')).toBeVisible();

    // Verify Navigation Links
    for (const label of ['Company', 'Conversations', 'Results', 'Calendar', 'Settings']) {
      await expect(page.getByRole('button', { name: label, exact: true })).toBeVisible();
    }
    await expect(page.getByRole('button', { name: /^Inbox/ })).toBeVisible();

    // Create the three core desks so their chips and members exist
    await seedDepartments(page, ['planning', 'research', 'quality']);

    // Verify Company Graph Canvas
    const canvas = page.locator('canvas').first();
    await expect(canvas).toBeVisible();

    // Verify Department Focus Chips
    await expect(teamChip(page, 'Planning & Strategy')).toBeVisible();
    await expect(teamChip(page, 'Research & Feasibility')).toBeVisible();
    await expect(teamChip(page, 'Quality & Review')).toBeVisible();

    // Verify the floating detail inspector (last created manager stays selected)
    await expect(page.getByRole('button', { name: 'Close inspector' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Edit contract' })).toBeVisible();
  });

  test('should switch between Graph and Org chart views seamlessly', async ({ page }) => {
    await page.goto('/#company');
    await seedDepartments(page, ['planning', 'research', 'quality']);

    // Switch to Org Chart
    await switchCompanyView(page, 'org');
    await expect(page.getByText('Adel Mansour').first()).toBeVisible();
    await expect(page.getByText('Planning & Strategy', { exact: true }).first()).toBeVisible();
    await expect(page.getByText('د. سارة').first()).toBeVisible();
    await expect(page.getByText('م. طارق').first()).toBeVisible();
    await expect(page.getByText('أ. ياسمين').first()).toBeVisible();

    // Switch back to Graph
    await switchCompanyView(page, 'graph');
    await expect(page.locator('canvas').first()).toBeVisible();
  });

  test('should navigate to Inbox and verify reviews and notices', async ({ page }) => {
    test.setTimeout(120000);
    await seedDepartments(page, ['planning']);
    await sendDelegatedMission(page, 'Delegate this mission to planner: مراجعة خطة إطلاق المنتج الجديد');
    await expectAssignedAck(page);
    await expectTaskCompletion(page);

    await page.goto('/#inbox');
    await expect(page.getByRole('heading', { name: 'Inbox' })).toBeVisible();

    // The delegated deliverable lands as a review item
    const reviewItem = page.locator('div[role="button"][aria-label^="Inspect"]').first();
    await expect(reviewItem).toBeVisible();
    await expect(reviewItem.getByText('Waiting for review')).toBeVisible();

    // Test filter tabs
    await page.getByRole('button', { name: /^Reviews/ }).click();
    await expect(page.locator('div[role="button"][aria-label^="Inspect"]').first()).toBeVisible();

    await page.getByRole('button', { name: /^Notices/ }).click();
    await expect(page.getByText('No items in this inbox folder.')).toBeVisible();

    await page.getByRole('button', { name: /^All/ }).click();
    await expect(page.locator('div[role="button"][aria-label^="Inspect"]').first()).toBeVisible();

    // Approve the pending review
    await page.getByRole('button', { name: 'Approve all reviews' }).click();
    await expect(page.getByRole('status')).toContainText('All pending reviews approved');
    await expect(page.getByText('Approved').first()).toBeVisible();

    // Test Mark all as read
    await page.getByRole('button', { name: 'Mark all as read' }).click();
    await expect(page.getByRole('status')).toContainText('All notices marked as read');
  });

  test('should interact with Conversations and receive live mission responses', async ({
    page,
  }) => {
    test.setTimeout(120000);
    await seedDepartments(page, ['planning']);

    await page.goto('/#conversations');
    await expect(page.getByRole('heading', { name: 'OpenCode Chat' })).toBeVisible();

    // Verify Adel Mansour CEO is selected by default
    await expect(page.getByText('OpenCode · Adel Mansour', { exact: true })).toBeVisible();

    // Type a message (explicit delegation to the planner employee). The last
    // role keyword on the line must be the target, so the command closes it.
    const textarea = page.locator('textarea[aria-label="Message text"]');
    const message = 'Prepare risk matrix and quality checks for new mission. Delegate this mission to planner.';
    await textarea.fill(message);
    await page.getByRole('button', { name: 'Send message' }).click();

    // Verify user message appeared
    await expect(page.getByText(message).first()).toBeVisible();

    // Wait for the live assignment acknowledgement from the planner
    await expectAssignedAck(page, 'د. سارة');

    // Wait for the simulated completion report in the same thread
    await expectTaskCompletion(page);
  });

  test('should display Results metrics, bar chart, and receipt table', async ({ page }) => {
    await page.goto('/#results');
    await expect(page.locator('h1:has-text("Results")')).toBeVisible();
    await expect(page.locator('text=Pipeline runs')).toBeVisible();
    await expect(page.locator('text=Success rate')).toBeVisible();
    await expect(page.locator('text=Token cost')).toBeVisible();
    await expect(page.locator('text=Runs per day')).toBeVisible();
    await expect(page.locator('text=Execution Run Receipts')).toBeVisible();
  });

  test('should render Calendar scheduling and open Settings', async ({ page }) => {
    await page.goto('/#calendar');
    await expect(page.locator('h1:has-text("Calendar")')).toBeVisible();
    await expect(page.getByText('Schedule New Pipeline Run')).toBeVisible();
    await expect(
      page.getByText('No scheduled runs yet. Create one above to run pipelines on a schedule.')
    ).toBeVisible();

    await page.goto('/#settings');
    await expect(page.locator('h1:has-text("Settings")')).toBeVisible();
    await expect(page.locator('text=OpenCode Engine')).toBeVisible();
    await expect(page.getByText('● Online')).toBeVisible();
    await expect(page.locator('text=Default model')).toBeVisible();
  });

  test('should open and close Contract modal and Help modal', async ({ page }) => {
    await gotoCompany(page);
    const planning = DEPARTMENTS.planning;
    await createTeam(page, planning.name, planning.manager, planning.managerRole);

    // Click on the Dr. Sara manager card in Org chart
    await switchCompanyView(page, 'org');
    await page.getByRole('button', { name: /د\. سارة/ }).first().click();

    // Click Edit contract button in the inspector
    await page.getByRole('button', { name: 'Edit contract' }).click();
    await expect(page.locator('h3:has-text("Edit Contract")')).toBeVisible();
    await expect(page.getByPlaceholder('e.g. Layla Haddad...')).toHaveValue('د. سارة');

    // Close modal
    await page.getByRole('button', { name: 'Cancel' }).click();
    await expect(page.locator('h3:has-text("Edit Contract")')).not.toBeVisible();

    // Open Help modal with shortcut key 'h'
    await page.keyboard.press('h');
    await expect(page.locator('h3:has-text("How to Read the Company Graph")')).toBeVisible();
    await page.getByRole('button', { name: 'Got it' }).click();
    await expect(page.locator('h3:has-text("How to Read the Company Graph")')).not.toBeVisible();
  });

  test('should click on review item in Inbox and inspect active task & inter-agent dialogue', async ({
    page,
  }) => {
    test.setTimeout(120000);
    await seedDepartments(page, ['planning']);
    await sendDelegatedMission(page, 'Delegate this mission to planner: مخرجات قابلة للفحص في صندوق المراجعة');
    await expectAssignedAck(page);
    await expectTaskCompletion(page);

    await page.goto('/#inbox');
    await expect(page.locator('h1:has-text("Inbox")')).toBeVisible();

    // Click the newest review card
    await page.locator('div[role="button"][aria-label^="Inspect"]').first().click();

    // Verify TaskInspectionModal opened on the recorded inter-agent dialogue
    const dialog = inspectDialog(page);
    await expect(dialog).toBeVisible();
    await expect(dialog.getByRole('button', { name: /حوار ومناقشة الفريق/ })).toBeVisible();
    await expect(dialog.getByText('Turn 1')).toBeVisible();

    // Switch to output JSON tab
    await dialog.getByRole('button', { name: /المخرجات والتقرير الفني/ }).click();
    await expect(dialog.getByText('Structured Deliverable JSON Schema')).toBeVisible();

    // Close modal
    await dialog.getByRole('button', { name: 'Close', exact: true }).click();
    await expect(dialog).not.toBeVisible();
  });

  test('should inspect agent active task from graph/org chart inspector', async ({ page }) => {
    await gotoCompany(page);
    const planning = DEPARTMENTS.planning;
    await createTeam(page, planning.name, planning.manager, planning.managerRole);
    await addSpecialist(page, 'أحمد', 'أخصائي البرمجة والتنفيذ');
    await switchCompanyView(page, 'org');

    // Click on Ahmed
    await page.getByRole('button', { name: /أحمد/ }).first().click();

    // Click "View active task & dialogue (ماذا يفعل الآن)" button in detail panel
    await page.getByRole('button', { name: /View active task & dialogue/ }).click();

    // Verify task modal opens with the agent's active work
    const dialog = inspectDialog(page);
    await expect(dialog.locator('h2')).toContainText('أحمد · Active Work & Coordination');
    await expect(dialog.getByText('Planning & Strategy', { exact: true })).toBeVisible();
    await expect(dialog.getByText(/أعمل حالياً على تنفيذ بنود العقد/)).toBeVisible();

    // The deliverable output tab reports the empty structured payload
    await dialog.getByRole('button', { name: /المخرجات والتقرير الفني/ }).click();
    await expect(dialog.getByText('No structured JSON payload attached to this task.')).toBeVisible();

    // Close modal
    await dialog.getByRole('button', { name: 'Close', exact: true }).click();
    await expect(dialog).not.toBeVisible();
  });

  test('should open Team Stream page and verify inter-agent dialogue turns', async ({ page }) => {
    await page.goto('/#stream');
    await expect(page.locator('h1:has-text("Team Dialogue Stream")')).toBeVisible();
    await expect(page.locator('text=Live Inter-Agent Stream')).toBeVisible();
    await expect(page.locator('text=Stream Ready')).toBeVisible();

    // The stream shell is live; recorded turns appear when agents report
    await expect(page.getByText('Recorded Handover Turns')).toBeVisible();
    await expect(page.getByText('No dialogue yet — turns appear here when agents report.')).toBeVisible();
  });
});
