import { expect, test, type Page, type Route } from '@playwright/test';

type Rpc = { method: string; params: Record<string, unknown> };
const tool = (id: string, summary: string) => ({
  id, run_id: 'run-' + id, action_summary: summary, scope: 'workspace_write',
  status: 'pending', expires_at: null,
});
const proposal = { id: 'proposal-09b', kind: 'update_profile', summary: 'Update the planner instructions', status: 'pending' };
const needs = (page: Page) => page.getByRole('region', { name: 'Needs you', exact: true });
const empty = (page: Page) => needs(page).getByText('Nothing is waiting on you.', { exact: true });
const badge = (page: Page) => page.locator('[data-needs-you]');
const ok = (route: Route, result: object) => route.fulfill({ json: { result } });
const fail = (route: Route, detail: string) => route.fulfill({ status: 503, json: { status: 503, detail } });
const writes = (calls: Rpc[]) => calls.filter(({ method }) =>
  ['approvals.decide', 'ceo.approve', 'ceo.deny', 'ceo.ask', 'runs.start_turn', 'runs.steer'].includes(method));

function gate() {
  let release!: () => void;
  const promise = new Promise<void>(resolve => { release = resolve; });
  return { promise, release };
}

async function mock(page: Page, handler: (route: Route, call: Rpc) => Promise<void> | undefined) {
  const calls: Rpc[] = [];
  await page.route('**/__agentos/call', route => {
    const call = route.request().postDataJSON() as Rpc;
    calls.push(call);
    return handler(route, call) ?? (call.method === 'ceo.actions'
      ? ok(route, { actions: [] })
      : route.continue());
  });
  return calls;
}

async function openResults(page: Page) {
  await page.goto('/?ui=rusebot');
  await page.locator('[data-sidebar-nav=results]').click();
  await expect(needs(page)).toBeVisible();
}

async function rpc<T>(page: Page, method: string, params: object) {
  const response = await page.request.post('/__agentos/call', { data: { method, params } });
  expect(response.ok(), method + ' on the isolated mock worker').toBe(true);
  return (await response.json() as { result: T }).result;
}

test('initial approval loading keeps the empty message hidden and shows confirmed CEO proposals', async ({ page }) => {
  const entered = gate();
  const release = gate();
  const releaseProposal = gate();
  const calls = await mock(page, (route, call) => {
    if (call.method === 'ceo.actions') return releaseProposal.promise.then(() => ok(route, { actions: [proposal] }));
    if (call.method !== 'approvals.list') return;
    return (async () => {
      entered.release();
      await release.promise;
      await ok(route, { approvals: [] });
    })();
  });
  await openResults(page);
  try {
    await entered.promise;
    await expect(needs(page).getByRole('status')).toHaveText('Loading tool approvals…');
    await expect(empty(page)).toHaveCount(0);
    releaseProposal.release();
    await expect(needs(page).getByText(proposal.summary)).toBeVisible();
  } finally {
    release.release();
    releaseProposal.release();
  }
  await expect(needs(page).getByRole('status')).toHaveCount(0);
  await expect(badge(page)).toHaveAccessibleName('Needs you: 1');
  expect(writes(calls)).toEqual([]);
});

test('successful empty approval and proposal reads show the existing empty message', async ({ page }) => {
  const calls = await mock(page, (route, call) => call.method === 'approvals.list'
    ? ok(route, { approvals: [] }) : undefined);
  await openResults(page);
  await expect.poll(() => calls.filter(({ method }) => method === 'approvals.list').length).toBe(1);
  await expect(empty(page)).toBeVisible();
  await expect(needs(page).getByRole('alert')).toHaveCount(0);
  expect(writes(calls)).toEqual([]);
});

test('an initial approval failure stays local and Retry adds the tool item beside the CEO proposal', async ({ page }) => {
  let reads = 0;
  const pending = tool('retry', 'Check the exported report');
  const calls = await mock(page, (route, call) => {
    if (call.method === 'ceo.actions') return ok(route, { actions: [proposal] });
    if (call.method !== 'approvals.list') return;
    return ++reads === 1 ? fail(route, 'Tool approvals temporarily unavailable') : ok(route, { approvals: [pending] });
  });
  await openResults(page);
  await expect(needs(page).getByRole('alert')).toContainText('Tool approvals temporarily unavailable');
  await expect(page.locator('[data-load-error]')).toHaveCount(0);
  await expect(needs(page).getByText(proposal.summary)).toBeVisible();
  await expect(empty(page)).toHaveCount(0);
  await needs(page).getByRole('button', { name: 'Retry' }).click();
  await expect(needs(page).getByText(pending.action_summary)).toBeVisible();
  await expect(needs(page).getByText(proposal.summary)).toBeVisible();
  await expect(needs(page).getByRole('alert')).toHaveCount(0);
  await expect(badge(page)).toHaveAccessibleName('Needs you: 2');
  expect(reads).toBe(2);
  expect(writes(calls)).toEqual([]);
});

test('a failed initial read with no proposals cannot claim Needs You is empty', async ({ page }) => {
  const calls = await mock(page, (route, call) => call.method === 'approvals.list'
    ? fail(route, 'Cannot read tool approvals') : undefined);
  await openResults(page);
  await expect(needs(page).getByRole('alert')).toContainText('Cannot read tool approvals');
  await expect(empty(page)).toHaveCount(0);
  expect(writes(calls)).toEqual([]);
});

test('a failed post-decision refresh retains other confirmed items and Retry updates the list', async ({ page }) => {
  let reads = 0;
  const chosen = tool('chosen', 'Review requested attachment access');
  const kept = tool('kept', 'Review requested report access');
  const added = tool('added', 'Check a new attachment');
  const calls = await mock(page, (route, call) => {
    if (call.method === 'approvals.decide') return ok(route, { ...chosen, status: 'denied' });
    if (call.method !== 'approvals.list') return;
    reads++;
    if (reads === 2) return fail(route, 'Approval refresh unavailable');
    return ok(route, { approvals: reads === 1 ? [chosen, kept] : [added] });
  });
  await openResults(page);
  const card = needs(page).locator('[data-approval-card=tool]').filter({ hasText: chosen.action_summary });
  await card.getByRole('button', { name: 'Deny' }).click();
  await expect(card).toContainText('Denied');
  await expect(needs(page).getByRole('alert')).toContainText('Showing the last loaded tool approvals');
  await expect(needs(page).getByText(kept.action_summary)).toBeVisible();
  await expect(empty(page)).toHaveCount(0);
  await needs(page).getByRole('button', { name: 'Retry' }).click();
  await expect(needs(page).getByText(added.action_summary)).toBeVisible();
  await expect(needs(page).getByText(kept.action_summary)).toHaveCount(0);
  await expect(needs(page).getByRole('alert')).toHaveCount(0);
  await expect(badge(page)).toHaveAccessibleName('Needs you: 1');
  expect(writes(calls)).toEqual([{
    method: 'approvals.decide',
    params: { run_id: chosen.run_id, approval_id: chosen.id, decision: 'deny' },
  }]);
});

for (const outcome of ['success', 'failure'] as const) {
  test('a superseded approval read cannot apply its late ' + outcome, async ({ page }) => {
    const profile = await rpc<{ id: string }>(page, 'profiles.create', {
      name: 'FE-09B delayed mock agent', role_type: 'custom', instructions: 'Reply briefly.',
    });
    const conversation = await rpc<{ id: string }>(page, 'conversations.create', {
      profile_id: profile.id, title: 'FE-09B approval polling fixture',
    });
    const run = await rpc<{ id: string }>(page, 'runs.start_turn', {
      conversation_id: conversation.id, engine: 'mock', model_id: 'deepseek-v4.1-flash-free',
      prompt: 'Stay active for the approval watcher [mock:delay=30000]',
    });
    const entered = gate();
    const release = gate();
    const delivered = gate();
    const chosen = tool('race-chosen', 'Check the poll fixture request');
    const stale = tool('race-stale', 'Superseded approval');
    const current = tool('race-current', 'Current approval');
    let reads = 0;
    const calls = await mock(page, (route, call) => {
      if (call.method === 'approvals.decide') return ok(route, { ...chosen, status: 'denied' });
      if (call.method !== 'approvals.list') return;
      reads++;
      if (reads === 1) return ok(route, { approvals: [chosen] });
      if (reads !== 2) return ok(route, { approvals: [current] });
      return (async () => {
        entered.release();
        await release.promise;
        if (outcome === 'success') await ok(route, { approvals: [stale] });
        else await fail(route, 'Superseded approval read failed');
        delivered.release();
      })();
    });
    try {
      await openResults(page);
      await entered.promise;
      await needs(page).getByRole('button', { name: 'Deny' }).click();
      await expect(needs(page).getByText(current.action_summary)).toBeVisible();
      release.release();
      await delivered.promise;
      await page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
      expect(await needs(page).getByText(stale.action_summary).count()).toBe(0);
      await expect(needs(page).getByText(current.action_summary)).toBeVisible();
      await expect(needs(page).getByRole('alert')).toHaveCount(0);
      expect(writes(calls).map(({ method }) => method)).toEqual(['approvals.decide']);
    } finally {
      release.release();
      const state = await rpc<{ status: string }>(page, 'runs.get', { run_id: run.id });
      if (state.status === 'running' || state.status === 'queued') await rpc(page, 'runs.cancel', { run_id: run.id });
    }
  });
}

test('an unconfirmed CEO proposal read keeps its existing recovery and blocks false empty copy', async ({ page }) => {
  let unavailable = true;
  const calls = await mock(page, (route, call) => {
    if (call.method === 'approvals.list') return ok(route, { approvals: [] });
    if (call.method === 'ceo.actions' && unavailable) return fail(route, 'Proposal list unavailable');
  });
  await openResults(page);
  const banner = page.locator('[data-load-error]');
  await expect(banner).toContainText('Proposal list unavailable');
  await expect(empty(page)).toHaveCount(0);
  unavailable = false;
  await banner.getByRole('button', { name: 'Retry' }).click();
  await expect(banner).toHaveCount(0);
  await expect(empty(page)).toBeVisible();
  expect(writes(calls)).toEqual([]);
});
