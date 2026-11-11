import { test, expect, type Page } from '@playwright/test';

/**
 * CEO Control Plane: the chat-first console — ask the CEO over live data,
 * approve or deny proposals right inside the conversation, keep a local ask
 * history. The full approvals inbox (older sessions, every status) lives in a
 * dialog opened from the topbar bell.
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
  const approvalsDialog = (page: Page) => page.getByRole('dialog', { name: 'صندوق الاعتماد' });
  const pendingCards = (page: Page) =>
    approvalsDialog(page).locator('li').filter({ hasText: TEAM_SUMMARY });
  /** The actionable proposal card rendered inline under the CEO's reply. */
  const inlineCard = (page: Page) =>
    page.locator('.proposal-card').filter({ hasText: TEAM_SUMMARY }).first();

  async function askTeamQuestion(page: Page): Promise<void> {
    await askComposer(page).fill(TEAM_QUESTION);
    await sendButton(page).click();
  }

  /** Opens the approvals inbox dialog once its initial fetch has settled. */
  async function openApprovals(page: Page): Promise<void> {
    await page.getByRole('button', { name: 'Pending approvals' }).click();
    await expect(approvalsDialog(page)).toBeVisible();
    await expect(refreshButton(page)).toBeEnabled();
  }

  async function closeApprovals(page: Page): Promise<void> {
    await approvalsDialog(page).getByRole('button', { name: 'إغلاق', exact: true }).click();
    await expect(approvalsDialog(page)).not.toBeVisible();
  }

  test('Sidebar opens the CEO console first with the ask composer and the approvals inbox', async ({
    page,
  }) => {
    await page.goto('/');

    // Executive-first: the CEO entry leads the sidebar.
    await expect(page.locator('aside nav button').first()).toHaveText('CEO');

    await page.getByRole('button', { name: 'CEO', exact: true }).click();

    // The console opens on the welcome state: question composer in the middle.
    await expect(page.getByRole('heading', { name: 'هنعمل إيه النهارده؟' })).toBeVisible();
    await expect(askComposer(page)).toBeVisible();

    // The full approvals inbox lives behind the topbar bell — the chat itself
    // is where decisions happen, and nothing executes without one.
    await openApprovals(page);
    await expect(
      approvalsDialog(page).getByRole('heading', { name: 'صندوق الاعتماد' })
    ).toBeVisible();
    await closeApprovals(page);
  });

  test('A team-creation question renders the CEO reply and an actionable pending proposal', async ({
    page,
  }) => {
    test.setTimeout(60000);
    await page.goto('/#ceo');
    await openApprovals(page);
    const before = await pendingCards(page).count();
    await closeApprovals(page);

    await askTeamQuestion(page);

    // 1. The deterministic mock reply over live company data renders.
    const reply = page.getByRole('region', { name: 'رد لقمان' });
    await expect(reply).toBeVisible({ timeout: 20000 });
    await expect(reply).toContainText('Based on live company data');

    // 2. The proposal surfaces right in the chat as an actionable card: it is
    // pending, awaits the user's decision and offers approve/deny inline.
    const card = inlineCard(page);
    await expect(card).toBeVisible({ timeout: 20000 });
    await expect(card.getByText('معلّق', { exact: true })).toBeVisible();
    await expect(card.getByText('بانتظار اعتمادك', { exact: true })).toBeVisible();
    await expect(card.getByRole('button', { name: 'اعتماد', exact: true })).toBeVisible();
    await expect(card.getByRole('button', { name: 'رفض', exact: true })).toBeVisible();

    // 3. …and is recorded in the approvals inbox as pending (a new card
    // arrives; nothing is ever auto-executed).
    await openApprovals(page);
    await expect(pendingCards(page)).toHaveCount(before + 1, { timeout: 20000 });
    await expect(pendingCards(page).first().getByText('معلّق', { exact: true })).toBeVisible();
    await closeApprovals(page);
  });

  test('A suggestion card pre-fills the composer and the ask lands in the sidebar history', async ({
    page,
  }) => {
    test.setTimeout(60000);
    await page.goto('/#ceo');

    // The welcome suggestions are real CEO actions, not decoration.
    await page.getByRole('button', { name: /فريق مراجعة أمنية/ }).click();
    await expect(askComposer(page)).toHaveValue(TEAM_QUESTION);
    await expect(sendButton(page)).toBeEnabled();

    await sendButton(page).click();

    // The reply renders and the question is kept in the sidebar history.
    await expect(page.getByRole('region', { name: 'رد لقمان' })).toBeVisible({
      timeout: 20000,
    });
    const historyItem = page.locator('.conversation-list button').first();
    await expect(historyItem).toContainText(TEAM_QUESTION);

    // “New question” returns to the welcome state; opening the thread from the
    // sidebar restores the recorded exchange.
    await page.getByRole('button', { name: 'New question', exact: true }).click();
    await expect(page.getByRole('heading', { name: 'هنعمل إيه النهارده؟' })).toBeVisible();
    await historyItem.click();
    await expect(page.getByRole('region', { name: 'رد لقمان' })).toBeVisible();
  });

  test('The composer picks models from the live free catalog with effort variants', async ({
    page,
  }) => {
    await page.goto('/#ceo');

    // The picker is fed by GET /api/models/free; its label shows the model name.
    const picker = page.getByRole('button', { name: 'موديل لقمان' });
    await expect(picker).toContainText('DeepSeek V4.1 Flash Free (Mock)', { timeout: 15000 });

    await picker.click();
    const menu = page.locator('.model-menu').first();
    await expect(menu).toBeVisible();
    expect(await menu.locator('.model-option-copy').count()).toBeGreaterThanOrEqual(5);

    // Selecting another free model updates the picker (persisted to settings).
    await menu.locator('button').filter({ hasText: 'Cat Jam Free (Mock)' }).click();
    await expect(page.getByRole('status')).toContainText('CEO model updated');
    await expect(picker).toContainText('Cat Jam Free (Mock)');

    // The effort picker lists the selected model's reasoning variants.
    const effort = page.getByRole('button', { name: 'مستوى التفكير' });
    await effort.click();
    await expect(page.locator('.effort-menu')).toBeVisible();
    await page.locator('.effort-menu button').filter({ hasText: 'xhigh' }).click();
    await expect(effort).toContainText('xhigh');
  });

  test('The sidebar collapses to icons without losing navigation', async ({ page }) => {
    await page.goto('/#ceo');

    await page.getByRole('button', { name: 'Collapse sidebar' }).click();
    await expect(page.locator('aside.sidebar.collapsed')).toBeVisible();
    await expect(page.getByText('RECENT QUESTIONS')).not.toBeVisible();

    // Icon-only navigation still works (accessible names come from titles).
    await page.getByRole('button', { name: 'Company' }).click();
    await expect(page.getByRole('heading', { name: 'Company', exact: true })).toBeVisible();

    await page.getByRole('button', { name: 'Expand sidebar' }).click();
    await expect(page.locator('aside.sidebar.collapsed')).not.toBeVisible();
  });

  test('Approving in the chat requires the in-app confirmation step, then leaves the pending list', async ({
    page,
  }) => {
    test.setTimeout(60000);
    await page.goto('/#ceo');
    await openApprovals(page);
    const before = await pendingCards(page).count();
    await closeApprovals(page);

    await askTeamQuestion(page);
    const card = inlineCard(page);
    await expect(card).toBeVisible({ timeout: 20000 });

    // Arm the decision inline: an explicit confirmation strip appears (no window.confirm).
    await card.getByRole('button', { name: 'اعتماد', exact: true }).click();
    const gate = card.getByRole('group', { name: 'تأكيد القرار' });
    await expect(gate).toBeVisible();
    await expect(gate.getByText('تأكيد اعتماد هذا الإجراء؟ سيُنفَّذ فوراً.')).toBeVisible();

    // Cancel decides nothing.
    await gate.getByRole('button', { name: 'إلغاء', exact: true }).click();
    await expect(gate).not.toBeVisible();
    await expect(card.getByRole('button', { name: 'اعتماد', exact: true })).toBeVisible();

    // Confirm: the action executes, the decision line renders inline and the
    // card itself turns approved.
    await card.getByRole('button', { name: 'اعتماد', exact: true }).click();
    await card
      .getByRole('group', { name: 'تأكيد القرار' })
      .getByRole('button', { name: 'تأكيد الاعتماد', exact: true })
      .click();

    await expect(page.getByText('تم اعتماد الإجراء وتنفيذه.')).toBeVisible({ timeout: 15000 });
    await expect(card.getByText('معتمد', { exact: true })).toBeVisible({ timeout: 15000 });

    // The decided action left the pending list and files under the approved tab.
    await openApprovals(page);
    await expect(pendingCards(page)).toHaveCount(before, { timeout: 15000 });
    await page.getByRole('button', { name: 'معتمدة', exact: true }).click();
    const approvedCard = pendingCards(page).first();
    await expect(approvedCard).toBeVisible();
    await expect(approvedCard.getByText('معتمد', { exact: true })).toBeVisible();
    await closeApprovals(page);
  });

  test('Approving a team from the chat materializes it in the Company view', async ({ page }) => {
    test.setTimeout(60000);
    await page.goto('/#ceo');

    await askTeamQuestion(page);
    const card = inlineCard(page);
    await expect(card).toBeVisible({ timeout: 20000 });

    // Approve through the in-app confirmation gate, right under the reply.
    await card.getByRole('button', { name: 'اعتماد', exact: true }).click();
    await card
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

  test('Denying in the chat shows the confirmation step and records the refusal', async ({
    page,
  }) => {
    test.setTimeout(60000);
    await page.goto('/#ceo');
    await openApprovals(page);
    const before = await pendingCards(page).count();
    await closeApprovals(page);

    await askTeamQuestion(page);
    const card = inlineCard(page);
    await expect(card).toBeVisible({ timeout: 20000 });

    await card.getByRole('button', { name: 'رفض', exact: true }).click();
    const gate = card.getByRole('group', { name: 'تأكيد القرار' });
    await expect(gate).toBeVisible();
    await expect(gate.getByText('تأكيد رفض هذا الإجراء؟ لن يُنفَّذ شيء.')).toBeVisible();

    await gate.getByRole('button', { name: 'تأكيد الرفض', exact: true }).click();

    await expect(page.getByText('تم رفض الإجراء — لم يُنفَّذ شيء.')).toBeVisible({ timeout: 15000 });
    await expect(card.getByText('مرفوض', { exact: true })).toBeVisible({ timeout: 15000 });

    // The pending list dropped it; the refusal is filed under the denied tab.
    await openApprovals(page);
    await expect(pendingCards(page)).toHaveCount(before, { timeout: 15000 });
    await page.getByRole('button', { name: 'مرفوضة', exact: true }).click();
    const deniedCard = pendingCards(page).first();
    await expect(deniedCard).toBeVisible();
    await expect(deniedCard.getByText('مرفوض', { exact: true })).toBeVisible();
    await closeApprovals(page);
  });

  test('Empty questions can never be sent', async ({ page }) => {
    await page.goto('/#ceo');

    const send = sendButton(page);
    await expect(send).toBeDisabled();

    await askComposer(page).fill('   ');
    await expect(send).toBeDisabled();

    // Whitespace-only questions stay unsent — no reply region ever appears.
    await expect(page.getByRole('region', { name: 'رد لقمان' })).not.toBeVisible();

    // A real question enables the send action.
    await askComposer(page).fill('سؤال حقيقي');
    await expect(send).toBeEnabled();
  });
});
