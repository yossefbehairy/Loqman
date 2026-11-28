import { expect, test, type Page, type Route } from '@playwright/test';

type Rpc = { method: string; params: Record<string, unknown> };
type Handler = (route: Route, call: Rpc) => Promise<void> | undefined;
type Status = 'pending_review' | 'approved';
const api = '**/__agentos/call';
const draft = (id = 'report', status: Status = 'pending_review') => ({
  id, run_id: 'run-' + id, conversation_id: null, filename: id + '.md',
  mime: 'text/markdown', size_bytes: 6, status, content_sha256: 'a'.repeat(64),
  downloadable: status === 'approved', content: null,
});
const view = (page: Page) => page.locator('[data-view=results]');
const section = (page: Page) => view(page).getByRole('region', { name: 'Deliverables' });
const card = (page: Page) => view(page).locator('[data-deliverable=report]');
const empty = 'No deliverables yet';
const ok = (route: Route, result: object) => route.fulfill({ json: { result } });
const fail = (route: Route, detail: string) => route.fulfill({ status: 503, json: { status: 503, detail } });
const unrelatedChats = new WeakMap<Page, string>();
const show = (page: Page, name: 'results' | 'chat') => name === 'results'
  ? page.locator('[data-sidebar-nav=results]').click()
  : page.locator('[data-sidebar-thread-row="' + unrelatedChats.get(page) + '"]').click();

function gate() {
  let release!: () => void;
  const promise = new Promise<void>(resolve => { release = resolve; });
  return { promise, release };
}

async function mock(page: Page, handler: Handler, initial = [draft()]) {
  await page.route(api, route => {
    const call = route.request().postDataJSON() as Rpc;
    return handler(route, call) ?? (call.method === 'deliverables.list_all'
      ? ok(route, { drafts: initial, next_cursor: null })
      : route.continue());
  });
}

async function open(page: Page, withUnrelatedChat = false) {
  if (withUnrelatedChat) {
    const chat = await rpc<{ id: string }>(page, 'conversations.create', { title: 'FE-09A unrelated chat' });
    unrelatedChats.set(page, chat.id);
    await page.addInitScript(id => localStorage.setItem('agentos.rusebot.selected.v1', JSON.stringify(id)), chat.id);
  }
  await page.goto('/?ui=rusebot');
  await expect(page.locator('[data-sidebar-nav=results]')).toBeVisible();
}

async function rpc<T>(page: Page, method: string, params: object) {
  const response = await page.request.post('/__agentos/call', { data: { method, params } });
  expect(response.ok(), method + ' on the isolated mock worker').toBe(true);
  return (await response.json() as { result: T }).result;
}

test('an initial pending Results read shows loading until the empty response arrives', async ({ page }) => {
  const entered = gate();
  const release = gate();
  await mock(page, (route, call) => {
    if (call.method !== 'deliverables.list_all') return;
    return (async () => {
      entered.release();
      await release.promise;
      await ok(route, { drafts: [], next_cursor: null });
    })();
  });
  await open(page);
  await show(page, 'results');
  try {
    await entered.promise;
    await expect(section(page).getByText('Loading deliverables…')).toBeVisible();
    await expect(section(page).getByText(empty, { exact: false })).toHaveCount(0);
  } finally {
    release.release();
  }
  await expect(section(page).getByText(empty, { exact: false })).toBeVisible();
});

test('a failed initial read displays its Results error and retries the list', async ({ page }) => {
  let reads = 0;
  await mock(page, (route, call) => {
    if (call.method !== 'deliverables.list_all') return;
    return ++reads === 1 ? fail(route, 'Results temporarily unavailable') : ok(route, { drafts: [draft()], next_cursor: null });
  });
  await open(page);
  await show(page, 'results');
  await expect(section(page).getByRole('alert')).toContainText('Results temporarily unavailable');
  await expect(section(page).getByText(empty, { exact: false })).toHaveCount(0);
  await section(page).getByRole('button', { name: 'Retry' }).click();
  await expect(card(page)).toBeVisible();
  await expect(section(page).getByRole('alert')).toHaveCount(0);
  expect(reads).toBe(2);
});

test('an acknowledged empty Results read shows the normal empty message', async ({ page }) => {
  await mock(page, () => undefined, []);
  await open(page);
  await show(page, 'results');
  await expect(section(page).getByText(empty, { exact: false })).toBeVisible();
  await expect(section(page).getByRole('alert')).toHaveCount(0);
});

test('a failed refresh retains confirmed cards and a successful empty Retry removes them', async ({ page }) => {
  let reads = 0;
  await mock(page, (route, call) => {
    if (call.method !== 'deliverables.list_all') return;
    reads++;
    if (reads === 2) return fail(route, 'Could not read updated deliverables');
    return ok(route, { drafts: reads === 1 ? [draft()] : [], next_cursor: null });
  });
  await open(page, true);
  await show(page, 'results');
  await expect(card(page)).toBeVisible();
  await show(page, 'chat');
  await show(page, 'results');
  await expect(section(page).getByRole('alert')).toContainText('Showing the last loaded results');
  await expect(card(page)).toBeVisible();
  await section(page).getByRole('button', { name: 'Retry' }).click();
  await expect(section(page).getByText(empty, { exact: false })).toBeVisible();
  await expect(card(page)).toHaveCount(0);
  expect(reads).toBe(3);
});

test('an older Results read finishing after navigation cannot replace a newer snapshot', async ({ page }) => {
  let reads = 0;
  const entered = gate();
  const release = gate();
  const delivered = gate();
  await mock(page, (route, call) => {
    if (call.method !== 'deliverables.list_all') return;
    if (++reads > 1) return ok(route, { drafts: [draft('current')], next_cursor: null });
    return (async () => {
      entered.release();
      await release.promise;
      await ok(route, { drafts: [draft('outdated')], next_cursor: null });
      delivered.release();
    })();
  });
  await open(page, true);
  await show(page, 'results');
  try {
    await entered.promise;
    await show(page, 'chat');
    await show(page, 'results');
    await expect(section(page).getByText('current.md')).toBeVisible();
  } finally {
    release.release();
  }
  await delivered.promise;
  await page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
  await expect(section(page).getByText('outdated.md')).toHaveCount(0);
  await expect(section(page).getByText('current.md')).toBeVisible();
  expect(reads).toBe(2);
});

test('preview errors appear on the card and Retry replaces the error with content', async ({ page }) => {
  let reads = 0;
  await mock(page, (route, call) => call.method === 'deliverables.get'
    ? (++reads === 1 ? fail(route, 'Preview temporarily unavailable') : ok(route, { ...draft(), content: 'Confirmed preview' }))
    : undefined);
  await open(page);
  await show(page, 'results');
  await card(page).getByRole('button', { name: 'Preview report.md' }).click();
  await expect(card(page).getByRole('alert')).toContainText('Preview temporarily unavailable');
  await card(page).getByRole('button', { name: 'Retry preview' }).click();
  await expect(card(page).getByLabel('Contents of report.md')).toHaveText('Confirmed preview');
  await expect(card(page).getByRole('alert')).toHaveCount(0);
  expect(reads).toBe(2);
});

test('a late preview error cannot replace content loaded after reopening', async ({ page }) => {
  const entered = gate();
  const release = gate();
  const delivered = gate();
  let reads = 0;
  await mock(page, (route, call) => {
    if (call.method !== 'deliverables.get') return;
    if (++reads > 1) return ok(route, { ...draft(), content: 'New preview' });
    return (async () => {
      entered.release();
      await release.promise;
      await fail(route, 'Old preview failed');
      delivered.release();
    })();
  });
  await open(page);
  await show(page, 'results');
  const preview = card(page).getByRole('button', { name: 'Preview report.md' });
  await preview.click();
  try {
    await entered.promise;
    await preview.click();
    await preview.click();
    await expect(card(page).getByLabel('Contents of report.md')).toHaveText('New preview');
  } finally {
    release.release();
  }
  await delivered.promise;
  await page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
  await expect(card(page).getByLabel('Contents of report.md')).toHaveText('New preview');
  await expect(card(page).getByRole('alert')).toHaveCount(0);
});

test('review failure stays on the originating card and does not reach Chat', async ({ page }) => {
  let decisions = 0;
  await mock(page, (route, call) => {
    if (call.method !== 'deliverables.decide') return;
    decisions++;
    return fail(route, 'Review temporarily unavailable');
  }, [draft(), draft('other')]);
  await open(page, true);
  await show(page, 'results');
  await card(page).getByRole('button', { name: 'Approve' }).click();
  await expect(card(page).getByRole('alert')).toContainText('Review temporarily unavailable');
  await expect(card(page)).toHaveAttribute('data-deliverable-status', 'pending_review');
  await expect(view(page).locator('[data-deliverable=other]').getByRole('alert')).toHaveCount(0);
  await show(page, 'chat');
  await expect(page.getByText('Review temporarily unavailable', { exact: false })).toHaveCount(0);
  expect(decisions).toBe(1);
});

test('download failure appears on the approved card and preserves metadata', async ({ page }) => {
  await mock(page, (route, call) => call.method === 'deliverables.content'
    ? fail(route, 'Download temporarily unavailable') : undefined, [draft('report', 'approved')]);
  await open(page, true);
  await show(page, 'results');
  await card(page).getByRole('button', { name: 'Download' }).click();
  await expect(card(page).getByRole('alert')).toContainText('Download temporarily unavailable');
  await expect(card(page)).toHaveAttribute('data-deliverable-status', 'approved');
  await expect(card(page).getByRole('button', { name: 'Preview report.md' })).toBeVisible();
  await show(page, 'chat');
  await expect(page.getByText('Download temporarily unavailable', { exact: false })).toHaveCount(0);
});

test('accepted review survives a failed follow-up read without replay', async ({ page }) => {
  let writes = 0;
  let refreshes = 0;
  await mock(page, (route, call) => {
    if (call.method === 'deliverables.decide') {
      writes++;
      expect(call.params).toEqual({ run_id: 'run-report', draft_id: 'report', decision: 'approve', content_sha256: 'a'.repeat(64) });
      return ok(route, draft('report', 'approved'));
    }
    if (call.method === 'deliverables.list') {
      refreshes++;
      return fail(route, 'Follow-up read unavailable');
    }
  });
  await open(page);
  await show(page, 'results');
  await card(page).getByRole('button', { name: 'Approve' }).click();
  await expect(card(page)).toHaveAttribute('data-deliverable-status', 'approved');
  await expect(card(page).getByRole('alert')).toContainText('Review saved');
  await expect(card(page).getByRole('alert')).toContainText('Follow-up read unavailable');
  await expect(card(page).getByRole('button', { name: 'Download' })).toBeEnabled();
  expect(writes).toBe(1);
  expect(refreshes).toBe(1);
});

test('the shared Chat card reports its action error without replacing the transcript', async ({ page }) => {
  const profile = await rpc<{ id: string }>(page, 'profiles.create', { name: 'FE-09A reviewer', role_type: 'custom', instructions: 'Reply briefly.' });
  const chat = await rpc<{ id: string }>(page, 'conversations.create', { profile_id: profile.id, title: 'FE-09A Chat card' });
  const prompt = 'Keep this saved prompt and reply visible.';
  const run = await rpc<{ id: string }>(page, 'runs.start_turn', {
    conversation_id: chat.id, engine: 'mock', model_id: 'deepseek-v4.1-flash-free', prompt,
  });
  await expect.poll(async () => (await rpc<{ status: string }>(page, 'runs.get', { run_id: run.id })).status).toBe('succeeded');
  const saved = await rpc<{ id: string }>(page, 'deliverables.create', { run_id: run.id });
  await page.addInitScript(id => localStorage.setItem('agentos.rusebot.selected.v1', JSON.stringify(id)), chat.id);
  await mock(page, (route, call) => call.method === 'deliverables.decide'
    ? fail(route, 'Chat review temporarily unavailable') : undefined);
  await open(page);
  const transcript = page.getByRole('log');
  const shared = transcript.locator('[data-deliverable="' + saved.id + '"]');
  await shared.getByRole('button', { name: 'Reject' }).click();
  await expect(shared.getByRole('alert')).toContainText('Chat review temporarily unavailable');
  await expect(shared).toHaveAttribute('data-deliverable-status', 'pending_review');
  await expect(transcript.getByText(prompt, { exact: true })).toBeVisible();
  await expect(transcript.locator('[data-role=assistant]')).toBeVisible();
  await expect(page.getByText('Chat review temporarily unavailable', { exact: false })).toHaveCount(1);
});
