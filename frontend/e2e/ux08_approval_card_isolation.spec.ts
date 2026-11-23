import { expect, test, type Page, type Route } from '@playwright/test';

type Chat = { id: string; title: string };
type Run = { id: string; status: string };
type Rpc = { method: string; params: Record<string, unknown> };

function gate() {
  let release!: () => void;
  const wait = new Promise<void>(resolve => { release = resolve; });
  return { wait, release };
}

async function rpc<T>(page: Page, method: string, params: object) {
  const response = await page.request.post('/__agentos/call', { data: { method, params } });
  expect(response.ok(), method).toBe(true);
  return (await response.json() as { result: T }).result;
}

const cards = (page: Page) => page.getByRole('log').locator('[data-approval-card=tool]');
const button = (page: Page, name: string) => cards(page).getByRole('button', { name, exact: true });

async function selectChat(page: Page, chat: Chat) {
  await page.locator('[data-sidebar-thread-row="' + chat.id + '"]').click();
  await expect(page.locator('[data-chathead-conversation]')).toHaveText(chat.title);
}

async function setup(page: Page, holdA: boolean, scenarios?: { first?: 'reject' | 'lost'; sameRun?: boolean; holdRead?: boolean }) {
  const agents = await Promise.all(['A', 'B'].map(letter => rpc<{ id: string }>(page, 'profiles.create', {
    name: 'UX-08 Agent ' + letter, role_type: 'custom', instructions: 'Reply briefly.',
  })));
  const [a, b] = await Promise.all(agents.map((agent, i) => rpc<Chat>(page, 'conversations.create', {
    title: 'UX-08 chat ' + (i ? 'B' : 'A'), profile_id: agent.id,
  })));
  const runs: Run[] = [];
  const decided: Rpc[] = [];
  const captured = gate();
  const release = gate();
  const delivered = gate();
  const readStarted = gate();
  const releaseRead = gate();
  let firstFinished = false;
  let refreshError = false;
  const pending = new Map<string, { id: string; run_id: string; action_summary: string; scope: string; status: string; expires_at: null }>();
  const ok = (route: Route, result: unknown) => route.fulfill({ json: { result } });
  const dispose = async () => {
    release.release();
    releaseRead.release();
    if (scenarios) await test.info().attach('approval-decision-rpcs', {
      body: JSON.stringify(decided), contentType: 'application/json',
    });
    await Promise.all(runs.map(async run => {
      const actual = await rpc<Run>(page, 'runs.get', { run_id: run.id });
      if (['running', 'queued'].includes(actual.status)) await rpc(page, 'runs.cancel', { run_id: run.id });
    }));
  };
  try {
    for (const [i, chat] of [a, b].entries()) {
      const run = await rpc<Run>(page, 'runs.start_turn', {
        conversation_id: chat.id, engine: 'mock', model_id: 'deepseek-v4.1-flash-free',
        prompt: 'Wait for UX-08 permission [mock:delay=30000]',
      });
      runs.push(run);
      pending.set('ux08-approval-' + i, {
        id: 'ux08-approval-' + i, run_id: run.id, action_summary: 'Permission for chat ' + (i ? 'B' : 'A'),
        scope: 'workspace_write', status: 'pending', expires_at: null,
      });
    }
    if (scenarios?.sameRun) pending.set('ux08-approval-extra', {
      ...pending.get('ux08-approval-0')!, id: 'ux08-approval-extra', action_summary: 'Review a second file in the same run',
    });
    await page.route('**/__agentos/call', async route => {
      const call = route.request().postDataJSON() as Rpc;
      if (call.method === 'approvals.list') {
        if (firstFinished && scenarios?.holdRead) {
          readStarted.release();
          await releaseRead.wait;
        }
        if (refreshError) return route.fulfill({ status: 503, json: { detail: 'Fixture approval refresh unavailable' } });
        return ok(route, { approvals: [...pending.values()] });
      }
      if (call.method === 'approvals.decide') {
        decided.push(call);
        const id = String(call.params.approval_id);
        const item = pending.get(id);
        if (!item || item.run_id !== call.params.run_id) return route.fulfill({ status: 409, json: { detail: 'Already decided or wrong run' } });
        const firstA = id === 'ux08-approval-0' && decided.filter(attempt => attempt.params.approval_id === id).length === 1;
        if (firstA && holdA) {
          if (!scenarios) pending.delete(id);
          captured.release();
          await release.wait;
        }
        if (scenarios && id === 'ux08-approval-0' && !firstA && !firstFinished) {
          return route.fulfill({ status: 409, json: { detail: 'Decision already in flight' } });
        }
        if (firstA) firstFinished = true;
        if (firstA && scenarios?.first === 'reject') {
          return route.fulfill({ status: 422, json: { detail: 'Fixture rejected decision before applying it' } });
        }
        pending.delete(id);
        if (firstA && scenarios?.first === 'lost') return route.abort('failed');
        await ok(route, { ...item, status: call.params.decision === 'approve' ? 'approved' : 'denied' });
        if (firstA) delivered.release();
        return;
      }
      return route.continue();
    });
    await page.addInitScript(id => localStorage.setItem('agentos.rusebot.selected.v1', JSON.stringify(id)), a.id);
    await page.goto('/');
    if (scenarios?.sameRun) await expect(cards(page)).toHaveCount(2);
    const initialCard = scenarios?.sameRun ? cards(page).filter({ hasText: 'Permission for chat A' }) : cards(page);
    await expect(initialCard).toHaveText(/UX-08 Agent A wants permission.*Permission for chat A/s);
    return {
      a, b, runs, decided, captured, release, delivered, dispose, readStarted, releaseRead,
      setRefreshError: (value: boolean) => { refreshError = value; },
    };
  } catch (error) {
    await dispose();
    throw error;
  }
}

for (const holdA of [false, true]) {
  test('UX-08: ' + (holdA ? 'late approval acknowledgement stays in its own chat' : 'decided approval stays in its own chat and survives return'), async ({ page }) => {
    const f = await setup(page, holdA);
    try {
      await button(page, 'Approve').click();
      if (holdA) await f.captured.wait;
      else await expect(cards(page)).toContainText('Approved');
      await selectChat(page, f.b);
      if (holdA) {
        await expect(cards(page)).toHaveCount(1);
        await expect(button(page, 'Deny')).toBeEnabled();
        await selectChat(page, f.a);
        await expect(cards(page)).toContainText('Permission for chat A');
        await expect(button(page, 'Approve')).toBeDisabled();
        await selectChat(page, f.b);
        f.release.release();
        await f.delivered.wait;
      }
      await expect(cards(page)).toHaveCount(1);
      await expect(cards(page)).toContainText('Permission for chat B');
      await expect(cards(page)).not.toContainText('Permission for chat A');
      await expect(button(page, 'Deny')).toBeEnabled();
      expect(f.decided).toHaveLength(1);

      await button(page, 'Deny').click();
      await expect(cards(page)).toContainText('Denied');
      await selectChat(page, f.a);
      await expect(cards(page)).toHaveCount(1);
      await expect(cards(page)).toContainText('Permission for chat A');
      await expect(cards(page)).toContainText('Approved');
      await expect(button(page, 'Approve')).toHaveCount(0);
      await expect(button(page, 'Deny')).toHaveCount(0);
      await selectChat(page, f.b);
      await expect(cards(page)).toHaveCount(1);
      await expect(cards(page)).toContainText('Denied');
      await expect(button(page, 'Approve')).toHaveCount(0);
      expect(f.decided).toEqual([
        { method: 'approvals.decide', params: { run_id: f.runs[0].id, approval_id: 'ux08-approval-0', decision: 'approve' } },
        { method: 'approvals.decide', params: { run_id: f.runs[1].id, approval_id: 'ux08-approval-1', decision: 'deny' } },
      ]);
    } finally {
      await f.dispose();
    }
  });
}

test.describe('AUD-04: shared tool approval decisions', () => {
  const needs = (page: Page) => page.getByRole('region', { name: 'Needs you', exact: true });
  const chatA = (page: Page) => cards(page).filter({ hasText: 'Permission for chat A' });
  const chatB = (page: Page) => cards(page).filter({ hasText: 'Permission for chat B' });
  const resultsA = (page: Page) => needs(page).locator('[data-approval-card=tool]').filter({ hasText: 'Permission for chat A' });
  const choose = (card: ReturnType<typeof cards>, name: 'Approve' | 'Deny') => card.getByRole('button', { name, exact: true });
  const showResults = async (page: Page) => {
    await page.locator('[data-sidebar-nav=results]').click();
    await expect(needs(page)).toBeVisible();
  };
  const firstDecision = (f: Awaited<ReturnType<typeof setup>>, decision: 'approve' | 'deny') => ({
    method: 'approvals.decide', params: { run_id: f.runs[0].id, approval_id: 'ux08-approval-0', decision },
  });
  const writeA = (f: Awaited<ReturnType<typeof setup>>) => f.decided.filter(call => call.params.approval_id === 'ux08-approval-0');

  async function verifyLocked(
    card: ReturnType<typeof cards>, f: Awaited<ReturnType<typeof setup>>, decision: 'approve' | 'deny',
  ) {
    await expect(card).toBeVisible();
    const opposite = choose(card, decision === 'approve' ? 'Deny' : 'Approve');
    await expect.soft(choose(card, 'Approve')).toBeDisabled({ timeout: 1000 });
    await expect.soft(choose(card, 'Deny')).toBeDisabled({ timeout: 1000 });
    if (await opposite.isEnabled()) {
      // Exercise the leaked control on RED rather than only checking its presentation.
      await opposite.click();
      await expect.poll(() => writeA(f).length).toBe(2);
    }
    expect.soft(writeA(f)).toEqual([firstDecision(f, decision)]);
  }

  test('A: a pending Chat approval is locked in Results and settles across navigation', async ({ page }) => {
    const f = await setup(page, true, {});
    try {
      await choose(chatA(page), 'Approve').click();
      await f.captured.wait;
      await showResults(page);
      await verifyLocked(resultsA(page), f, 'approve');
      f.release.release();
      await f.delivered.wait;
      await expect.soft(resultsA(page)).toContainText('Approved', { timeout: 2000 });
      await selectChat(page, f.a);
      await expect(chatA(page)).toContainText('Approved', { timeout: 2000 });
      expect(writeA(f)).toEqual([firstDecision(f, 'approve')]);
    } finally {
      await f.dispose();
    }
  });

  test('B/C: a pending Results decision locks one approval across views and leaves other approvals actionable', async ({ page }) => {
    const f = await setup(page, true, { sameRun: true });
    try {
      await showResults(page);
      await choose(resultsA(page), 'Deny').click();
      await f.captured.wait;
      await selectChat(page, f.a);
      await verifyLocked(chatA(page), f, 'deny');
      const extra = cards(page).filter({ hasText: 'Review a second file in the same run' });
      await choose(extra, 'Approve').click();
      await expect(extra).toContainText('Approved');
      await selectChat(page, f.b);
      await choose(chatB(page), 'Deny').click();
      await expect(chatB(page)).toContainText('Denied');
      await selectChat(page, f.a);
      await expect.soft(choose(chatA(page), 'Deny')).toBeDisabled({ timeout: 1000 });
      f.release.release();
      await f.delivered.wait;
      await expect(chatA(page)).toContainText('Denied', { timeout: 2000 });
      expect(f.decided).toEqual([
        firstDecision(f, 'deny'),
        { method: 'approvals.decide', params: { run_id: f.runs[0].id, approval_id: 'ux08-approval-extra', decision: 'approve' } },
        { method: 'approvals.decide', params: { run_id: f.runs[1].id, approval_id: 'ux08-approval-1', decision: 'deny' } },
      ]);
    } finally {
      await f.dispose();
    }
  });

  test('D: an ordinary double-click on one mounted card issues one decision', async ({ page }) => {
    const f = await setup(page, true, {});
    try {
      await choose(chatA(page), 'Approve').dblclick();
      await f.captured.wait;
      await expect(choose(chatA(page), 'Approve')).toBeDisabled();
      await expect(choose(chatA(page), 'Deny')).toBeDisabled();
      f.release.release();
      await expect(chatA(page)).toContainText('Approved');
      expect(f.decided).toEqual([firstDecision(f, 'approve')]);
    } finally {
      await f.dispose();
    }
  });

  test('E: a confirmed decision survives a list failure and a read-only Retry', async ({ page }) => {
    const f = await setup(page, true, {});
    try {
      await choose(chatA(page), 'Approve').click();
      await f.captured.wait;
      f.setRefreshError(true);
      f.release.release();
      await expect(chatA(page)).toContainText('Approved');
      await showResults(page);
      await expect(needs(page).getByRole('alert')).toContainText('Fixture approval refresh unavailable');
      await expect.soft(resultsA(page)).toContainText('Approved', { timeout: 2000 });
      f.setRefreshError(false);
      await needs(page).getByRole('button', { name: 'Retry', exact: true }).click();
      await expect(needs(page).getByRole('alert')).toHaveCount(0);
      await expect.soft(resultsA(page)).toContainText('Approved', { timeout: 2000 });
      await selectChat(page, f.a);
      await expect(chatA(page)).toContainText('Approved', { timeout: 2000 });
      expect(f.decided).toEqual([firstDecision(f, 'approve')]);
    } finally {
      await f.dispose();
    }
  });

  test('F: a rejected decision remains owned and can be retried after a fresh pending read', async ({ page }) => {
    const f = await setup(page, true, { first: 'reject', holdRead: true });
    try {
      await showResults(page);
      await choose(resultsA(page), 'Approve').click();
      await f.captured.wait;
      f.release.release();
      await f.readStarted.wait;
      await selectChat(page, f.a);
      await expect.soft(choose(chatA(page), 'Approve')).toBeDisabled({ timeout: 1000 });
      await expect.soft(choose(chatA(page), 'Deny')).toBeDisabled({ timeout: 1000 });
      expect(writeA(f)).toEqual([firstDecision(f, 'approve')]);
      f.releaseRead.release();
      await expect.soft(chatA(page).getByRole('alert')).toContainText('Fixture rejected decision before applying it', { timeout: 2000 });
      await expect(choose(chatA(page), 'Approve')).toBeEnabled();
      expect(writeA(f)).toEqual([firstDecision(f, 'approve')]);
      await choose(chatA(page), 'Approve').click();
      await expect(chatA(page)).toContainText('Approved');
      expect(f.decided).toEqual([firstDecision(f, 'approve'), firstDecision(f, 'approve')]);
    } finally {
      await f.dispose();
    }
  });

  test('G: a lost acknowledgement with an absent pending row cannot be replayed or inferred', async ({ page }) => {
    const f = await setup(page, true, { first: 'lost', holdRead: true });
    try {
      await choose(chatA(page), 'Deny').click();
      await f.captured.wait;
      f.release.release();
      await f.readStarted.wait;
      await showResults(page);
      await expect.soft(choose(resultsA(page), 'Approve')).toBeDisabled({ timeout: 1000 });
      f.releaseRead.release();
      await expect.soft(resultsA(page).getByRole('alert')).toContainText('Could not confirm the approval decision', { timeout: 2000 });
      await expect(resultsA(page).getByText(/^(Approved|Denied)$/)).toHaveCount(0);
      if (await resultsA(page).isVisible()) {
        await expect(choose(resultsA(page), 'Approve')).toBeDisabled();
        await expect(choose(resultsA(page), 'Deny')).toBeDisabled();
      }
      await selectChat(page, f.a);
      await expect(chatA(page).getByRole('alert')).toContainText('Could not confirm the approval decision', { timeout: 2000 });
      const statusRead = page.waitForResponse(response => response.request().url().endsWith('/__agentos/call') &&
        (response.request().postDataJSON() as Rpc).method === 'approvals.list');
      await chatA(page).getByRole('button', { name: 'Retry status', exact: true }).click();
      expect((await statusRead).ok()).toBe(true);
      await expect(choose(chatA(page), 'Approve')).toBeDisabled();
      expect(f.decided).toEqual([firstDecision(f, 'deny')]);
    } finally {
      await f.dispose();
    }
  });

  test('H: a late acknowledgement only settles its originating approval after a view switch', async ({ page }) => {
    const f = await setup(page, true, {});
    try {
      await choose(chatA(page), 'Approve').click();
      await f.captured.wait;
      await showResults(page);
      await selectChat(page, f.b);
      f.release.release();
      await f.delivered.wait;
      await expect(chatB(page)).toContainText('Permission for chat B');
      await expect(choose(chatB(page), 'Deny')).toBeEnabled();
      await expect(chatB(page).getByText('Approved', { exact: true })).toHaveCount(0);
      await selectChat(page, f.a);
      await expect(chatA(page)).toContainText('Approved', { timeout: 2000 });
      expect(f.decided).toEqual([firstDecision(f, 'approve')]);
    } finally {
      await f.dispose();
    }
  });
});
