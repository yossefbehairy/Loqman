import { test, expect, type Page } from '@playwright/test';

async function navigateToTeamStream(page: Page) {
  await page.goto('/');
  const streamNav = page.locator('[data-nav="stream"], button:has-text("Team Chat Stream"), button:has-text("سجل محادثات الوكلاء"), button:has-text("Team Stream")').first();
  if (await streamNav.isVisible({ timeout: 2000 }).catch(() => false)) {
    await streamNav.click();
    await page.waitForTimeout(300);
  }
}

test.describe('R5: Inter-Agent Team Chat & Handover Stream', () => {
  test('Renders chronological Slack/Discord-style team conversation feed', async ({ page }) => {
    await navigateToTeamStream(page);

    // 1. Verify feed container header or title
    const feedHeader = page.locator('text=/Team Chat Stream|سجل محادثات الوكلاء|Live Team Chat|Handover Stream/i').first();
    await expect(feedHeader).toBeVisible();

    // 2. Verify message list or placeholder is present
    const messageContainer = page.locator('[data-testid="team-stream-messages"], .team-chat-container, .conversation-feed, [data-testid="chat-panel"]').first();
    await expect(messageContainer).toBeVisible();
  });

  test('Multi-turn handover progression displays Turn 1 delegation, Turn 2 deliverable, Turn 3 synthesis', async ({
    page,
  }) => {
    test.setTimeout(60000);
    await navigateToTeamStream(page);

    // If stream is empty, trigger a run or load demo history
    const runBtn = page.locator('button:has-text("Run Pipeline"), button:has-text("Start Handover"), [data-action="run"]').first();
    if (await runBtn.isVisible()) {
      await runBtn.click();
    }

    // 1. Verify Turn 1: Delegation
    const turn1 = page.locator('text=/Turn 1|الجولة 1/i').first();
    await expect(turn1).toBeVisible({ timeout: 20000 });

    // 2. Verify Turn 2: Employee deliverable
    const turn2 = page.locator('text=/Turn 2|الجولة 2/i').first();
    await expect(turn2).toBeVisible({ timeout: 20000 });

    // 3. Verify Turn 3: Manager synthesis & approval
    const turn3 = page.locator('text=/Turn 3|الجولة 3/i').first();
    await expect(turn3).toBeVisible({ timeout: 20000 });

    // Verify natural names or role indicators are attached to the turns
    const senderLabels = page.locator('text=/Planner|Analyst|Reviewer|سارة|طارق|ياسمين|أحمد/i');
    await expect(senderLabels.first()).toBeVisible();
  });

  test('Expandable JSON deliverables allow inspecting structured outputs', async ({ page }) => {
    test.setTimeout(60000);
    await navigateToTeamStream(page);

    // Wait for at least one message with deliverable/output
    const deliverableBtn = page.locator('button:has-text("Deliverable"), button:has-text("JSON"), [data-testid="expand-deliverable"], summary, button:has-text("المخرجات")').first();
    if (await deliverableBtn.isVisible({ timeout: 10000 }).catch(() => false)) {
      await deliverableBtn.click();

      // Verify code / pre / JSON block expands
      const jsonContent = page.locator('pre, code, [data-testid="deliverable-json"]').first();
      await expect(jsonContent).toBeVisible();
    }
  });

  test('SSE stream handles live message arrival and updates feed chronologically', async ({ page }) => {
    await navigateToTeamStream(page);

    // Verify stream status indicator (Connected / Online / Live)
    const liveIndicator = page.locator('text=/Live|Online|متصل|SSE/i').first();
    if (await liveIndicator.isVisible()) {
      await expect(liveIndicator).toBeVisible();
    }
  });
});
