import { test, expect, type Page } from '@playwright/test';

/**
 * CEO Control Plane: ask the CEO over live data + approval-gated proposals.
 *
 * The mock runtime is deterministic: a team-creation question proposes a
 * `new_team` action (recorded as pending — never auto-executed). Decisions
 * require an explicit in-app confirmation step on top of the local
 * X-AgentOS-Confirm header the client sends.
 */
test.describe('CEO Control Plane', () => {
  const TEAM_QUESTION = 'اعمل فريق مراجعة أمنية';
  // Summary fragment of the deterministic mock proposal.
  const TEAM_SUMMARY = 'Security Review Team';

  const askComposer = (page: Page) => page.getByLabel('سؤال للمدير العام');
  const sendButton = (page: Page) => page.getByRole('button', { name: 'إرسال', exact: true });
  const refreshButton = (page: Page) =>
    page.getByRole('button', { name: 'تحديث المقترحات', exact: true });
  const pendingCards = (page: Page) => page.locator('main li').filter({ hasText: TEAM_SUMMARY });

  async function askTeamQuestion(page: Page): Promise<void> {
    await askComposer(page).fill(TEAM_QUESTION);
    await sendButton(page).click();
  }

  /** The approvals inbox finished its initial fetch (refresh is no longer busy). */
  async function waitForInbox(page: Page): Promise<void> {
    await expect(refreshButton(page)).toBeEnabled();
  }

  test('Sidebar opens the CEO control plane first with ask surface and approvals inbox', async ({
    page,
  }) => {
    await page.goto('/');

    // Executive-first: the CEO entry leads the sidebar.
    await expect(page.locator('aside nav button').first()).toHaveText('CEO');

    await page.getByRole('button', { name: 'CEO', exact: true }).click();

    await expect(page.getByRole('heading', { name: /CEO Control Plane/ })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'اسأل المدير العام' })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'صندوق الاعتماد' })).toBeVisible();
    await expect(askComposer(page)).toBeVisible();
  });

  test('A team-creation question renders the CEO reply and records a pending proposal', async ({
    page,
  }) => {
    test.setTimeout(60000);
    await page.goto('/#ceo');
    await waitForInbox(page);
    const before = await pendingCards(page).count();

    await askTeamQuestion(page);

    // 1. The deterministic mock reply over live company data renders.
    const reply = page.getByRole('region', { name: 'رد المدير العام' });
    await expect(reply).toBeVisible({ timeout: 20000 });
    await expect(reply).toContainText('Based on live company data');

    // 2. The proposal is surfaced as an awaiting-approval chip…
    await expect(page.getByText('بانتظار اعتمادك', { exact: true }).first()).toBeVisible();

    // 3. …and is recorded in the approvals inbox as pending (a new card arrives;
    // nothing is ever auto-executed).
    await expect(pendingCards(page)).toHaveCount(before + 1, { timeout: 20000 });
    const card = pendingCards(page).first();
    await expect(card).toBeVisible();
    await expect(card.getByText('معلّق', { exact: true })).toBeVisible();
  });

  test('Approving requires the in-app confirmation step, then leaves the pending list', async ({
    page,
  }) => {
    test.setTimeout(60000);
    await page.goto('/#ceo');
    await waitForInbox(page);
    const before = await pendingCards(page).count();

    await askTeamQuestion(page);
    const cards = pendingCards(page);
    await expect(cards).toHaveCount(before + 1, { timeout: 20000 });

    // Arm the decision: an explicit confirmation strip appears (no window.confirm).
    await cards.first().getByRole('button', { name: 'اعتماد', exact: true }).click();
    const gate = cards.first().getByRole('group', { name: 'تأكيد القرار' });
    await expect(gate).toBeVisible();
    await expect(gate.getByText('تأكيد اعتماد هذا الإجراء؟ سيُنفَّذ فوراً.')).toBeVisible();

    // Cancel decides nothing.
    await gate.getByRole('button', { name: 'إلغاء', exact: true }).click();
    await expect(gate).not.toBeVisible();
    await expect(cards).toHaveCount(before + 1);

    // Confirm the approval: the action executes and leaves the pending list.
    await cards.first().getByRole('button', { name: 'اعتماد', exact: true }).click();
    await cards
      .first()
      .getByRole('group', { name: 'تأكيد القرار' })
      .getByRole('button', { name: 'تأكيد الاعتماد', exact: true })
      .click();

    await expect(page.getByText('تم اعتماد الإجراء وتنفيذه.')).toBeVisible({ timeout: 15000 });
    await expect(cards).toHaveCount(before, { timeout: 15000 });

    // The decided action now lives under the approved status tab.
    await page.getByRole('button', { name: 'معتمدة', exact: true }).click();
    const approvedCard = page.locator('main li').filter({ hasText: TEAM_SUMMARY }).first();
    await expect(approvedCard).toBeVisible();
    await expect(approvedCard.getByText('معتمد', { exact: true })).toBeVisible();
  });

  test('Approving a team materializes it in the Company view', async ({ page }) => {
    test.setTimeout(60000);
    await page.goto('/#ceo');
    await waitForInbox(page);
    const before = await pendingCards(page).count();

    await askTeamQuestion(page);
    const cards = pendingCards(page);
    await expect(cards).toHaveCount(before + 1, { timeout: 20000 });

    // Approve through the in-app confirmation gate.
    await cards.first().getByRole('button', { name: 'اعتماد', exact: true }).click();
    await cards
      .first()
      .getByRole('group', { name: 'تأكيد القرار' })
      .getByRole('button', { name: 'تأكيد الاعتماد', exact: true })
      .click();
    await expect(page.getByText('تم اعتماد الإجراء وتنفيذه.')).toBeVisible({ timeout: 15000 });

    // The approve response carries the really-created profiles; the app
    // materializes them into the company state (org chart is plain DOM text).
    await page.getByRole('button', { name: 'Company', exact: true }).click();
    await page.getByRole('button', { name: 'Org chart', exact: true }).click();

    const teamName = 'Security Review Team (فريق المراجعة الأمنية)';
    await expect(page.getByText(teamName, { exact: true }).first()).toBeVisible({
      timeout: 15000,
    });
    for (const member of ['Hassan (حسن)', 'Farida (فريدة)', 'Kareem (كريم)']) {
      await expect(page.getByText(member, { exact: true }).first()).toBeVisible();
    }
  });

  test('Denying shows the confirmation step and records the refusal', async ({ page }) => {
    test.setTimeout(60000);
    await page.goto('/#ceo');
    await waitForInbox(page);
    const before = await pendingCards(page).count();

    await askTeamQuestion(page);
    const cards = pendingCards(page);
    await expect(cards).toHaveCount(before + 1, { timeout: 20000 });

    await cards.first().getByRole('button', { name: 'رفض', exact: true }).click();
    const gate = cards.first().getByRole('group', { name: 'تأكيد القرار' });
    await expect(gate).toBeVisible();
    await expect(gate.getByText('تأكيد رفض هذا الإجراء؟ لن يُنفَّذ شيء.')).toBeVisible();

    await gate.getByRole('button', { name: 'تأكيد الرفض', exact: true }).click();

    await expect(page.getByText('تم رفض الإجراء — لم يُنفَّذ شيء.')).toBeVisible({ timeout: 15000 });
    await expect(cards).toHaveCount(before, { timeout: 15000 });

    // The refused action now lives under the denied status tab.
    await page.getByRole('button', { name: 'مرفوضة', exact: true }).click();
    const deniedCard = page.locator('main li').filter({ hasText: TEAM_SUMMARY }).first();
    await expect(deniedCard).toBeVisible();
    await expect(deniedCard.getByText('مرفوض', { exact: true })).toBeVisible();
  });

  test('Empty questions can never be sent', async ({ page }) => {
    await page.goto('/#ceo');

    const send = sendButton(page);
    await expect(send).toBeDisabled();

    await askComposer(page).fill('   ');
    await expect(send).toBeDisabled();

    // Whitespace-only questions stay unsent — no reply region ever appears.
    await expect(page.getByRole('region', { name: 'رد المدير العام' })).not.toBeVisible();

    // A real question enables the send action.
    await askComposer(page).fill('سؤال حقيقي');
    await expect(send).toBeEnabled();
  });
});
