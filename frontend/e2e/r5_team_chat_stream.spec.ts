import { test, expect } from '@playwright/test';
import {
  expectAssignedAck,
  expectTaskCompletion,
  inspectDialog,
  openNewestInboxReview,
  seedDepartments,
  sendDelegatedMission,
} from './helpers';

/**
 * R5: Inter-Agent Team Chat & Handover Stream.
 *
 * The Team Dialogue Stream shell renders live; recorded handover turns are
 * produced per delegated task (delegation → deliverable → synthesis), surface
 * in the Team Stream ledger backed by each employee's `activeTask`, and are
 * also inspected through the Inbox review modal, while the conversation feed
 * reports live message arrival.
 */
test.describe('R5: Inter-Agent Team Chat & Handover Stream', () => {
  test('Renders chronological Slack/Discord-style team conversation feed', async ({ page }) => {
    await page.goto('/#stream');

    // 1. Verify feed header / title
    await expect(page.getByRole('heading', { name: /Team Dialogue Stream/ })).toBeVisible();
    await expect(page.getByText('Live Inter-Agent Stream')).toBeVisible();
    await expect(page.getByText(/Stream Ready/)).toBeVisible();

    // 2. Verify the stream overview metrics and search/filter controls
    await expect(page.getByText('Recorded Handover Turns')).toBeVisible();
    await expect(page.getByText('Collaborating Departments')).toBeVisible();
    await expect(page.getByText('Context Integrity')).toBeVisible();
    await expect(page.getByPlaceholder('Search dialogues...')).toBeVisible();

    // 3. With no recorded tasks yet the feed renders its empty placeholder
    await expect(page.getByText('0 Turns')).toBeVisible();
    await expect(page.getByText('No dialogue yet — turns appear here when agents report.')).toBeVisible();
  });

  test('Recorded handover progression displays delegation turn, deliverable and sender labels', async ({
    page,
  }) => {
    test.setTimeout(120000);
    await seedDepartments(page, ['planning']);

    // Trigger a real employee run so handover turns are recorded
    await sendDelegatedMission(page, 'Delegate this mission to planner: جولة تسليم متعددة الأدوار لخطة الإطلاق');
    await expectAssignedAck(page);
    await expectTaskCompletion(page);

    // Open the recorded deliverable from the Inbox
    const reviewCard = await openNewestInboxReview(page);
    await reviewCard.click();

    // 1. Inter-agent dialogue tab lists the recorded handover turn
    const dialog = inspectDialog(page);
    await dialog.getByRole('button', { name: /حوار ومناقشة الفريق/ }).click();
    await expect(dialog.getByText('Turn 1')).toBeVisible();

    // 2. The turn carries its sender role label and recipient
    await expect(dialog.getByText('(planner)')).toBeVisible();
    await expect(dialog.getByText('CEO', { exact: true })).toBeVisible();

    // 3. Turn text is the recorded deliverable summary
    await expect(dialog.locator('pre, div').filter({ hasText: 'key_objective' }).first()).toBeVisible();
  });

  test('Expandable JSON deliverables allow inspecting structured outputs', async ({ page }) => {
    test.setTimeout(120000);
    await seedDepartments(page, ['planning']);

    await sendDelegatedMission(page, 'Delegate this mission to planner: مخرجات JSON قابلة للفحص الهيكلي');
    await expectAssignedAck(page);
    await expectTaskCompletion(page);

    const reviewCard = await openNewestInboxReview(page);
    await reviewCard.click();
    const dialog = inspectDialog(page);

    // Switch to the deliverable output tab
    await dialog.getByRole('button', { name: /المخرجات والتقرير الفني/ }).click();

    // The structured JSON block expands with the task payload
    await expect(dialog.getByText('Structured Deliverable JSON Schema')).toBeVisible();
    const jsonBlock = dialog.locator('pre');
    await expect(jsonBlock).toBeVisible();
    await expect(jsonBlock).toContainText('"output"');
    await expect(dialog.getByRole('button', { name: 'Copy JSON' })).toBeVisible();
  });

  test('Live updates arrive in the conversation feed in chronological order', async ({ page }) => {
    test.setTimeout(120000);
    await seedDepartments(page, ['planning']);

    // Send the mission and wait for the live report cycle to complete
    await sendDelegatedMission(page, 'Delegate this mission to planner: بث حي لسلامة التحديثات اللحظية');
    await expectAssignedAck(page);
    await expectTaskCompletion(page);

    // The feed keeps arrival order: user ask → assignment ack → completion report
    const bubbles = page.locator('main div.rounded-2xl');
    const texts = await bubbles.allInnerTexts();
    const userIdx = texts.findIndex((t) => t.includes('بث حي لسلامة التحديثات اللحظية'));
    const ackIdx = texts.findIndex((t) => t.includes('assigned to'));
    const doneIdx = texts.findIndex((t) => t.includes('Employee task') && t.includes('succeeded'));

    expect(userIdx).toBeGreaterThanOrEqual(0);
    expect(ackIdx).toBeGreaterThan(userIdx);
    expect(doneIdx).toBeGreaterThan(ackIdx);
  });

  test('Delegated mission fills the Team Stream ledger with real handover turns', async ({ page }) => {
    test.setTimeout(120000);
    await seedDepartments(page, ['planning']);

    const missionText = 'Delegate this mission to planner: سجل التسليم الحقيقي في تيار الفريق';
    await sendDelegatedMission(page, missionText);
    await expectAssignedAck(page);

    // The acknowledgement names the real assignee this mission was delegated to
    const ackText = await page.getByText(/assigned to/).first().innerText();
    const assignee = ackText.match(/assigned to (.*)\. Its result/)?.[1]?.trim();
    expect(assignee).toBeTruthy();

    await expectTaskCompletion(page);

    // Open the Team Stream — the recorded turns are now backed by agent.activeTask
    await page.goto('/#stream');
    await expect(page.getByRole('heading', { name: /Team Dialogue Stream/ })).toBeVisible();
    await expect(page.getByText('No dialogue yet — turns appear here when agents report.')).not.toBeVisible();

    // Delegation → deliverable → synthesis: exactly the three recorded handover turns
    await expect(page.getByText('3 Turns')).toBeVisible();

    const turnCard = (n: number) => page.locator('div.os-card').filter({ hasText: `Turn ${n}` });

    // Turn 1 — manager → employee, carrying the real delegation instruction text
    const turn1 = turnCard(1);
    await expect(turn1).toHaveCount(1);
    await expect(turn1).toContainText('Adel Mansour');
    await expect(turn1).toContainText(assignee!);
    await expect(turn1).toContainText('سجل التسليم الحقيقي في تيار الفريق');

    // Turn 2 — employee → manager: the real deliverable, with its attached payload
    const turn2 = turnCard(2);
    await expect(turn2).toHaveCount(1);
    await expect(turn2).toContainText(assignee!);
    await expect(turn2).toContainText('"key_objective"');
    const payloadToggle = turn2.getByRole('button', {
      name: /View attached deliverable payload JSON/,
    });
    await expect(payloadToggle).toBeVisible();
    await payloadToggle.click();
    await expect(turn2.locator('pre')).toContainText('"output"');

    // Turn 3 — manager → employee: the mission's recorded synthesis
    const turn3 = turnCard(3);
    await expect(turn3).toHaveCount(1);
    await expect(turn3).toContainText('Adel Mansour');
    await expect(turn3).toContainText('key_objective');
  });
});
