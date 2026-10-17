import { test, expect } from '@playwright/test';
import {
  expectAssignedAck,
  expectTaskCompletion,
  seedDepartments,
  selectPersona,
  sendMessage,
} from './helpers';

/**
 * R1: Minimalist Executive Chat & Manager Direct Consultation.
 *
 * The former chat landing was superseded: the app now opens into the Company
 * workspace and direct OpenCode conversations live in the Conversations view.
 * Manager consultation messages that carry an explicit delegation ("Delegate
 * this mission to planner: ...") start a real backend employee mission whose
 * acknowledgement and completion report arrive in the same thread.
 */
test.describe('R1: Minimalist Executive Chat & Manager Direct Consultation', () => {
  test('App opens immediately into a clean workspace without legacy canvas or button clutter', async ({
    page,
  }) => {
    await page.goto('/');

    // 1. The app lands on the Company workspace with the sidebar shell ready
    await expect(page.getByRole('heading', { name: 'Company', exact: true })).toBeVisible();
    await expect(page.getByText('Local workspace')).toBeVisible();
    await expect(page.getByText('OpenCode', { exact: true })).toBeVisible();

    // 2. No legacy workflow-canvas clutter on initial view
    await expect(page.locator('text=Start Trigger')).not.toBeVisible();
    await expect(page.locator('text=Lead Planner')).not.toBeVisible();
    await expect(page.getByRole('button', { name: 'Validate DAG' })).not.toBeVisible();
    await expect(page.getByRole('button', { name: 'Run Pipeline' })).not.toBeVisible();

    // 3. The direct conversation composer is one navigation away and ready
    await page.goto('/#conversations');
    const composer = page.locator('textarea[aria-label="Message text"]');
    await expect(composer).toBeVisible();
    await expect(page.getByRole('button', { name: 'Send message' })).toBeVisible();
  });

  test('Persona list allows choosing General Manager, Dr. Sara, Eng. Tarek, Prof. Yasmin', async ({
    page,
  }) => {
    await seedDepartments(page, ['planning', 'research', 'quality']);
    await page.goto('/#conversations');
    await expect(page.getByRole('heading', { name: 'OpenCode Chat' })).toBeVisible();

    // 1. All four personas exist in the conversation agent list
    const gmPill = page.getByRole('button', { name: /Adel Mansour/ }).first();
    const saraPill = page.getByRole('button', { name: /د\. سارة/ }).first();
    const tarekPill = page.getByRole('button', { name: /م\. طارق/ }).first();
    const yasminPill = page.getByRole('button', { name: /أ\. ياسمين/ }).first();

    await expect(gmPill).toBeVisible();
    await expect(saraPill).toBeVisible();
    await expect(tarekPill).toBeVisible();
    await expect(yasminPill).toBeVisible();

    // 2. Switching persona rewires the active chat header
    await saraPill.click();
    await expect(page.getByText('OpenCode · د. سارة', { exact: true })).toBeVisible();
    await tarekPill.click();
    await expect(page.getByText('OpenCode · م. طارق', { exact: true })).toBeVisible();
    await yasminPill.click();
    await expect(page.getByText('OpenCode · أ. ياسمين', { exact: true })).toBeVisible();

    // 3. Return to the General Manager
    await gmPill.click();
    await expect(page.getByText('OpenCode · Adel Mansour', { exact: true })).toBeVisible();
  });

  test('Sending a chat message to a manager receives a delegated task response', async ({ page }) => {
    await seedDepartments(page, ['planning']);
    await page.goto('/#conversations');

    // Select Dr. Sara for strategic consultation
    await selectPersona(page, 'د. سارة');

    // Locate composer input and type the project goal as an explicit delegation
    const testMessage =
      'Delegate this mission to planner: نحتاج خطة متكاملة لتطوير منصة تسجيل الطلاب وتوصية المقررات للجامعة';
    await sendMessage(page, testMessage);

    // 1. Verify user message appears in chat history
    await expect(page.getByText(testMessage).first()).toBeVisible({ timeout: 5000 });

    // 2. Verify the manager thread receives the assignment acknowledgement
    await expectAssignedAck(page, 'د. سارة');

    // 3. Verify the completed employee response is reported back
    await expectTaskCompletion(page);
  });

  test('Sidebar navigates seamlessly between all 7 views without full page reloads', async ({
    page,
  }) => {
    await page.goto('/');
    await expect(page.getByRole('heading', { name: 'Company', exact: true })).toBeVisible();

    // Track navigation to ensure no full page reload occurs
    let pageReloaded = false;
    page.on('load', () => {
      pageReloaded = true;
    });

    // 1. Verify all 7 core navigation items exist in the sidebar
    const navLabels = ['CEO', 'Company', 'Conversations', 'Team Stream', 'Results', 'Settings'];
    for (const label of navLabels) {
      await expect(page.getByRole('button', { name: label, exact: true })).toBeVisible();
    }
    await expect(page.getByRole('button', { name: /^Inbox/ })).toBeVisible();

    // 2. Navigate through every view
    await page.getByRole('button', { name: 'CEO', exact: true }).click();
    await expect(page.getByRole('heading', { name: /CEO Control Plane/ })).toBeVisible();

    await page.getByRole('button', { name: 'Conversations', exact: true }).click();
    await expect(page.getByRole('heading', { name: 'OpenCode Chat' })).toBeVisible();

    await page.getByRole('button', { name: 'Team Stream', exact: true }).click();
    await expect(page.getByRole('heading', { name: /Team Dialogue Stream/ })).toBeVisible();

    await page.getByRole('button', { name: 'Results', exact: true }).click();
    await expect(page.getByRole('heading', { name: 'Results', exact: true })).toBeVisible();

    await page.getByRole('button', { name: 'Settings', exact: true }).click();
    await expect(page.getByRole('heading', { name: 'Settings', exact: true })).toBeVisible();

    await page.getByRole('button', { name: /^Inbox/ }).click();
    await expect(page.getByRole('heading', { name: 'Inbox', exact: true })).toBeVisible();

    await page.getByRole('button', { name: 'Company', exact: true }).click();
    await expect(page.getByRole('heading', { name: 'Company', exact: true })).toBeVisible();

    // Verify no full page reloads occurred during navigation
    expect(pageReloaded).toBe(false);
  });
});
