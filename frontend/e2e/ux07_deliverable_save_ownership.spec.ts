import { expect, test, type Page } from '@playwright/test';

type Chat = { id: string; title: string };
type Run = { id: string; status: string };
type Draft = { id: string; run_id: string; filename: string };
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

const save = (page: Page) => page.getByRole('button', { name: 'Save reply as deliverable for review' });
const cards = (page: Page) => page.locator('[data-deliverable]');
const preview = (page: Page, draft: Draft) => page.getByRole('button', { name: 'Preview ' + draft.filename, exact: true });

async function selectChat(page: Page, chat: Chat) {
  await page.locator('[data-sidebar-thread-row="' + chat.id + '"]').click();
  await expect(page.locator('[data-chathead-conversation]')).toHaveText(chat.title);
}

async function setup(page: Page, outcome: 'success' | 'failure' | 'refresh-error' | 'overlap') {
  const profile = await rpc<{ id: string }>(page, 'profiles.create', {
    name: 'UX-07 specialist', role_type: 'custom', instructions: 'Reply briefly.',
  });
  const chats = await Promise.all(['A', 'B'].map(letter => rpc<Chat>(page, 'conversations.create', {
    title: 'UX-07 chat ' + letter, profile_id: profile.id,
  })));
  const runs = await Promise.all(chats.map(async chat => {
    const run = await rpc<Run>(page, 'runs.start_turn', {
      conversation_id: chat.id, engine: 'mock', model_id: 'deepseek-v4.1-flash-free', prompt: 'Write the report for ' + chat.title,
    });
    await expect.poll(async () => (await rpc<Run>(page, 'runs.get', { run_id: run.id })).status).toBe('succeeded');
    return run;
  }));
  const [a, b] = chats;
  const [runA, runB] = runs;
  const captured = gate();
  const release = gate();
  const handled = gate();
  const refresh = gate();
  const calls: Rpc[] = [];
  const created: Draft[] = [];
  let attemptedA = false;
  let afterCreate = false;
  await page.route('**/__agentos/call', async route => {
    const call = route.request().postDataJSON() as Rpc;
    if (call.method === 'deliverables.create') {
      calls.push(call);
      const isA = call.params.run_id === runA.id;
      if (isA && !attemptedA) {
        attemptedA = true;
        if (outcome === 'failure') {
          captured.release();
          await release.wait;
          await route.fulfill({ status: 503, json: { detail: 'Deliverable creation unavailable' } });
          handled.release();
          return;
        }
      }
      const response = await route.fetch();
      expect(response.ok()).toBe(true);
      created.push((await response.json() as { result: Draft }).result);
      if (isA && created.filter(draft => draft.run_id === runA.id).length === 1) {
        captured.release();
        await release.wait;
        afterCreate = true;
      }
      await route.fulfill({ response });
      if (isA) handled.release();
      return;
    }
    if (afterCreate && outcome === 'refresh-error' && call.method === 'deliverables.list' && call.params.run_id === runA.id) {
      afterCreate = false;
      await route.fulfill({ status: 503, json: { detail: 'Run deliverables lookup unavailable' } });
      refresh.release();
      return;
    }
    await route.continue();
  });
  await page.addInitScript(id => localStorage.setItem('agentos.rusebot.selected.v1', JSON.stringify(id)), a.id);
  await page.goto('/');
  await expect(save(page)).toBeVisible();
  return { a, b, runA, runB, captured, release, handled, refresh, calls, created };
}

for (const outcome of ['success', 'failure', 'overlap'] as const) {
  test('UX-07: deliverable ' + outcome + ' stays with the originating chat', async ({ page }) => {
    const f = await setup(page, outcome);
    try {
      await save(page).click();
      await f.captured.wait;
      await expect(page.getByRole('button', { name: 'Saving deliverable…' })).toBeDisabled();
      await selectChat(page, f.b);
      await expect(save(page)).toBeVisible();
      if (outcome === 'overlap') {
        await save(page).click();
        await expect.poll(() => f.created.find(draft => draft.run_id === f.runB.id)?.filename).toBeTruthy();
        await expect(preview(page, f.created.find(draft => draft.run_id === f.runB.id)!)).toBeVisible();
      }
      f.release.release();
      await f.handled.wait;
      await page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
      await expect(cards(page)).toHaveCount(outcome === 'overlap' ? 1 : 0);
      await expect(page.getByRole('alert').filter({ hasText: 'Deliverable creation unavailable' })).toHaveCount(0);
      if (outcome !== 'failure') await expect(preview(page, f.created.find(draft => draft.run_id === f.runA.id)!)).toHaveCount(0);
      await selectChat(page, f.a);
      if (outcome === 'failure') {
        await expect(page.getByRole('alert')).toContainText('Deliverable creation unavailable');
        expect(f.calls).toHaveLength(1);
        await save(page).click();
        await expect.poll(() => f.created.length).toBe(1);
      }
      await expect(preview(page, f.created.find(draft => draft.run_id === f.runA.id)!)).toBeVisible();
      await expect(cards(page)).toHaveCount(1);
      expect(f.calls.map(call => call.params.run_id)).toEqual(outcome === 'overlap' ? [f.runA.id, f.runB.id] :
        outcome === 'failure' ? [f.runA.id, f.runA.id] : [f.runA.id]);
    } finally {
      f.release.release();
    }
  });
}

test('UX-07: a confirmed save survives a failed refresh without issuing another create', async ({ page }) => {
  const f = await setup(page, 'refresh-error');
  try {
    await save(page).click();
    await f.captured.wait;
    f.release.release();
    await f.refresh.wait;
    const [saved] = f.created;
    await expect(preview(page, saved)).toBeVisible();
    await expect(page.getByRole('alert').filter({ hasText: /could not save/i })).toHaveCount(0);
    await expect(save(page)).toHaveCount(0);
    await selectChat(page, f.b);
    await expect(save(page)).toBeVisible();
    await selectChat(page, f.a);
    await expect(preview(page, saved)).toHaveCount(1);
    await page.getByRole('button', { name: 'Results', exact: true }).click();
    await expect(preview(page, saved)).toHaveCount(1);
    const result = await rpc<{ drafts: Draft[] }>(page, 'deliverables.list', { run_id: f.runA.id });
    expect(result.drafts.map(draft => draft.id)).toEqual([saved.id]);
    expect(f.calls).toHaveLength(1);
  } finally {
    f.release.release();
  }
});
