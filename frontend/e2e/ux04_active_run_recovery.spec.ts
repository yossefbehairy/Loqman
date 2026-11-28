import { expect, test, type Page } from '@playwright/test';

type Chat = { id: string; title: string };
type Snapshot = {
  id: string;
  conversation_id: string;
  status: 'queued' | 'running' | 'succeeded' | 'cancelled';
  assistant_message_seq: number | null;
  assistant_content: string | null;
};
type Rpc = { method: string; params: Record<string, unknown> };

const specialist = 'UX-04 specialist';
const stop = (page: Page) => page.getByRole('button', { name: 'Stop', exact: true });
const working = (page: Page) => page.locator('[data-working]');
const sidebarRow = (page: Page, id: string) => page.locator(`[data-sidebar-thread-row="${id}"]`);
const send = (page: Page) => page.getByRole('button', { name: 'Send', exact: true });
const settleRender = (page: Page) => page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));

function gate() {
  let release!: () => void;
  const wait = new Promise<void>(resolve => { release = resolve; });
  return { wait, release };
}

async function rpc<T>(page: Page, method: string, params: object) {
  const response = await page.request.post('/__agentos/call', { data: { method, params } });
  expect(response.ok(), method + ' on the isolated worker').toBe(true);
  return (await response.json() as { result: T }).result;
}

async function switchTo(page: Page, chat: Chat) {
  await sidebarRow(page, chat.id).click();
  await expect(page.locator('[data-chathead-conversation]')).toHaveText(chat.title);
}

async function createChats(page: Page) {
  const { id: profileId } = await rpc<{ id: string }>(page, 'profiles.create', {
    name: specialist, role_type: 'custom', instructions: 'Reply briefly.',
  });
  const original = await rpc<Chat>(page, 'conversations.create', { title: 'UX-04 original chat', profile_id: profileId });
  const other = await rpc<Chat>(page, 'conversations.create', { title: 'UX-04 independent chat', profile_id: profileId });
  return { original, other };
}

async function restore(page: Page, mode: 'complete' | 'switching' | 'terminal' | 'unavailable' | 'empty-cache') {
  const { original, other } = await createChats(page);
  const run = await rpc<Snapshot>(page, 'runs.start_turn', {
    conversation_id: original.id, engine: 'mock', model_id: 'deepseek-v4.1-flash-free',
    prompt: `Resume the specialist task. [mock:delay=${mode === 'complete' ? 10000 : 30000}]`,
  });
  const getRun = () => rpc<Snapshot>(page, 'runs.get', { run_id: run.id });
  const cancelRun = () => rpc<Snapshot>(page, 'runs.cancel', { run_id: run.id });
  const allowList = gate();
  const listCaptured = gate();
  const listDelivered = gate();
  const revisitListDelivered = gate();
  const firstGetSettled = gate();
  const mutations: Rpc[] = [];
  const subscriptions: URLSearchParams[] = [];
  let getCount = 0;
  let holdingList = true;
  let capturedList: Promise<{ runs: Snapshot[] }> | undefined;
  let unavailable = mode === 'unavailable' || mode === 'empty-cache';

  page.on('request', request => {
    const url = new URL(request.url());
    if (url.pathname === '/__agentos/stream' && url.searchParams.get('topic') === 'run') subscriptions.push(url.searchParams);
  });

  await page.route('**/__agentos/call', async route => {
    const call = route.request().postDataJSON() as Rpc;
    if (['runs.start_turn', 'runs.cancel', 'conversations.bind_profile', 'ceo.ask'].includes(call.method)) mutations.push(call);
    const reject = () => route.fulfill({ status: 503, json: { status: 503, detail: 'Fixture worker read unavailable.' } });
    if (call.method === 'runs.get' && call.params.run_id === run.id) {
      getCount++;
      if (getCount === 1) {
        if (mode === 'terminal') {
          await listCaptured.wait;
          expect((await cancelRun()).status).toBe('cancelled');
          const response = await route.fetch();
          expect((await response.json() as { result: Snapshot }).result.status).toBe('cancelled');
          await route.fulfill({ response });
        } else {
          await reject();
        }
        firstGetSettled.release();
        return;
      }
      if (unavailable) return reject();
    }
    if (unavailable && call.method === 'runs.list' && call.params.conversation_id === original.id) return reject();
    if (call.method === 'runs.list' && call.params.conversation_id === original.id && call.params.limit === 30) {
      if (!holdingList) {
        await route.fulfill({ response: await route.fetch() });
        revisitListDelivered.release();
        return;
      }
      if (mode !== 'terminal') await firstGetSettled.wait;
      // Strict Mode can start two history reads. Give both the same captured worker snapshot.
      capturedList ??= route.fetch().then(async response => {
        const { result } = await response.json() as { result: { runs: Snapshot[] } };
        expect(result.runs).toEqual([expect.objectContaining({ id: run.id, conversation_id: original.id, status: 'running' })]);
        listCaptured.release();
        return result;
      });
      const result = await capturedList;
      await allowList.wait;
      await route.fulfill({ json: { result } });
      listDelivered.release();
      return;
    }
    return route.continue();
  });
  await page.addInitScript(({ cid, rid, emptyCache }) => {
    localStorage.setItem('agentos.rusebot.selected.v1', JSON.stringify(cid));
    if (emptyCache) localStorage.removeItem('agentos.rusebot.runs.v1');
    else localStorage.setItem('agentos.rusebot.runs.v1', JSON.stringify({ [cid]: [rid] }));
  }, { cid: original.id, rid: run.id, emptyCache: mode === 'empty-cache' });

  const releaseList = () => {
    holdingList = false;
    allowList.release();
  };
  const dispose = async () => {
    releaseList();
    firstGetSettled.release();
    listCaptured.release();
    if (['queued', 'running'].includes((await getRun()).status)) await cancelRun();
  };
  try {
    await expect.poll(async () => (await getRun()).status).toBe('running');
    await page.goto('/');
    if (mode !== 'empty-cache') await firstGetSettled.wait;
    await expect(page.locator('[data-chathead-conversation]')).toHaveText(original.title);
  } catch (error) {
    await dispose();
    throw error;
  }
  const expectSingleSubscription = async () => {
    await expect.poll(() => subscriptions.map(url => Object.fromEntries(url))).toEqual([
      { topic: 'run', id: run.id, last: '0', format: 'normalised-v1' },
    ]);
  };
  return { original, other, run, getRun, releaseList, listCaptured, listDelivered, revisitListDelivered, subscriptions, mutations, dispose,
    expectSingleSubscription, makeAvailable: () => { unavailable = false; } };
}

test('UX-04: a later run list restores progress and Stop after a failed startup status read', async ({ page }) => {
  const fixture = await restore(page, 'complete');
  try {
    await fixture.listCaptured.wait;
    fixture.releaseList();
    await expect(working(page)).toHaveText('Working');
    await expect(stop(page)).toBeVisible();
    await fixture.expectSingleSubscription();
    await expect.poll(async () => (await fixture.getRun()).status, { timeout: 12000 }).toBe('succeeded');
    const saved = await fixture.getRun();
    expect(saved.assistant_content).toBeTruthy();
    await expect(page.locator(`[data-mid="${saved.assistant_message_seq}"]`)).toContainText(saved.assistant_content!);
    await expect(stop(page)).toHaveCount(0);
    await expect(working(page)).toHaveCount(0);
    await fixture.expectSingleSubscription();
    expect(fixture.mutations).toEqual([]);
    const { runs } = await rpc<{ runs: Snapshot[] }>(page, 'runs.list', { conversation_id: fixture.original.id });
    expect(runs.map(run => run.id)).toEqual([fixture.run.id]);
  } finally {
    await fixture.dispose();
  }
});

test('UX-04: switching during recovery preserves the original follower and Stop target', async ({ page }) => {
  const fixture = await restore(page, 'switching');
  try {
    await fixture.listCaptured.wait;
    await switchTo(page, fixture.other);
    fixture.releaseList();
    await fixture.listDelivered.wait;
    await expect(sidebarRow(page, fixture.original.id).getByLabel('Working')).toBeVisible();
    await expect(working(page)).toHaveCount(0);
    await expect(stop(page)).toHaveCount(0);
    await expect(page.getByRole('log')).not.toContainText('Resume the specialist task.');
    await fixture.expectSingleSubscription();

    await switchTo(page, fixture.original);
    await fixture.revisitListDelivered.wait;
    await page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
    await expect(working(page)).toHaveText('Working');
    await expect(stop(page)).toBeVisible();
    await fixture.expectSingleSubscription();
    await stop(page).click();
    await expect.poll(async () => (await fixture.getRun()).status).toBe('cancelled');
    await expect(stop(page)).toHaveCount(0);
    await expect(working(page)).toHaveCount(0);
    expect(fixture.mutations).toEqual([{ method: 'runs.cancel', params: { run_id: fixture.run.id } }]);
    await fixture.expectSingleSubscription();
  } finally {
    await fixture.dispose();
  }
});

test('UX-04: a delayed active snapshot cannot revive a worker-confirmed terminal run', async ({ page }) => {
  const fixture = await restore(page, 'terminal');
  try {
    fixture.releaseList();
    await fixture.listDelivered.wait;
    await page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
    await expect(working(page)).toHaveCount(0);
    await expect(stop(page)).toHaveCount(0);
    expect(fixture.subscriptions).toHaveLength(0);
    expect((await fixture.getRun()).status).toBe('cancelled');
    expect(fixture.mutations).toEqual([]);
  } finally {
    await fixture.dispose();
  }
});

test('UX-04: when both recovery reads fail, Retry can recheck the existing run', async ({ page }) => {
  const fixture = await restore(page, 'unavailable');
  try {
    await expect(page.locator('[data-thread-error]')).toContainText('Could not verify');
    expect(fixture.subscriptions).toHaveLength(0);
    expect(fixture.mutations).toEqual([]);
    fixture.makeAvailable();
    fixture.releaseList();
    await page.locator('[data-thread-retry]').click();
    await expect(working(page)).toHaveText('Working');
    await expect(stop(page)).toBeVisible();
    await expect(page.locator('[data-thread-error]')).toHaveCount(0);
    await fixture.expectSingleSubscription();
    expect(fixture.mutations).toEqual([]);
  } finally {
    await fixture.dispose();
  }
});

test('UX-04 hardening: a late start acknowledgement cannot revive a known terminal run', async ({ page }) => {
  const { original, other } = await createChats(page);
  const mutations: Rpc[] = [];
  const subscriptions: string[] = [];
  const terminalDiscovered = gate();
  const releaseTerminalStream = gate();
  const holdStart = () => ({ captured: gate(), release: gate(), snapshot: undefined as Snapshot | undefined });
  const first = holdStart();
  const second = holdStart();
  await page.route('**/__agentos/call', async route => {
    const call = route.request().postDataJSON() as Rpc;
    if (call.method === 'runs.start_turn') {
      mutations.push(call);
      const held = call.params.conversation_id === original.id ? first : second;
      const response = await route.fetch();
      expect(response.ok()).toBe(true);
      held.snapshot = (await response.json() as { result: Snapshot }).result;
      held.captured.release();
      await held.release.wait;
      return route.fulfill({ response });
    }
    if (call.method === 'runs.list' && call.params.conversation_id === original.id && call.params.limit === 30 && first.snapshot) {
      const response = await route.fetch();
      const { result } = await response.json() as { result: { runs: Snapshot[] } };
      await route.fulfill({ response });
      if (result.runs.some(run => run.id === first.snapshot?.id && run.status === 'succeeded')) terminalDiscovered.release();
      return;
    }
    return route.continue();
  });
  await page.route(/\/__agentos\/stream\?/, async route => {
    const id = new URL(route.request().url()).searchParams.get('id');
    if (id) subscriptions.push(id);
    // Keep any erroneous subscription from immediately replaying the terminal event over the stale active state.
    if (id === first.snapshot?.id) await releaseTerminalStream.wait;
    return route.continue();
  });
  await page.addInitScript(cid => localStorage.setItem('agentos.rusebot.selected.v1', JSON.stringify(cid)), original.id);
  const status = (run: Snapshot) => rpc<Snapshot>(page, 'runs.get', { run_id: run.id });
  const composer = page.getByRole('textbox', { name: `Message ${specialist}` });
  try {
    await page.goto('/');
    await composer.fill('Complete before sending the first acknowledgement.');
    await send(page).click();
    await first.captured.wait;
    if (!first.snapshot) throw new Error('Expected an original start acknowledgement');
    const queued = first.snapshot;
    expect(queued.status).toBe('queued');
    await expect.poll(async () => (await status(queued)).status).toBe('succeeded');
    const terminal = await status(queued);
    const savedReply = page.locator(`[data-mid="${terminal.assistant_message_seq}"]`);
    await switchTo(page, other);
    await switchTo(page, original);
    await terminalDiscovered.wait;
    await expect(savedReply).toContainText(terminal.assistant_content!);
    await settleRender(page);
    const refresh = page.waitForResponse(response => {
      if (!response.url().endsWith('/__agentos/call')) return false;
      const call = response.request().postDataJSON() as Rpc;
      return call.method === 'conversations.messages' && call.params.conversation_id === original.id;
    });
    first.release.release();
    await refresh;
    await settleRender(page);
    await expect(working(page), 'A known terminal run must stay terminal after the queued acknowledgement').toHaveCount(0);
    await expect(stop(page)).toHaveCount(0);
    expect(subscriptions).toEqual([]);
    await expect(savedReply).toContainText(terminal.assistant_content!);
    expect(mutations).toHaveLength(1);

    await switchTo(page, other);
    await composer.fill('Keep the normal delayed acknowledgement active. [mock:delay=30000]');
    await send(page).click();
    await second.captured.wait;
    if (!second.snapshot) throw new Error('Expected a second start acknowledgement');
    const active = second.snapshot;
    await expect.poll(async () => (await status(active)).status).toBe('running');
    second.release.release();
    await expect(stop(page)).toBeVisible();
    await expect.poll(() => subscriptions).toEqual([active.id]);
    await stop(page).click();
    await expect.poll(async () => (await status(active)).status).toBe('cancelled');
    expect(mutations.map(start => start.params.conversation_id)).toEqual([original.id, other.id]);
  } finally {
    first.release.release();
    second.release.release();
    releaseTerminalStream.release();
    if (second.snapshot && ['queued', 'running'].includes((await status(second.snapshot)).status)) {
      await rpc(page, 'runs.cancel', { run_id: second.snapshot.id });
    }
  }
});

test('UX-04 hardening: failed run discovery with an empty cache remains retryable', async ({ page }) => {
  const fixture = await restore(page, 'empty-cache');
  try {
    await expect(page.locator('[data-thread-error]')).toContainText('Could not verify');
    await expect(working(page)).toHaveCount(0);
    await expect(stop(page)).toHaveCount(0);
    expect(fixture.subscriptions).toHaveLength(0);
    fixture.makeAvailable();
    fixture.releaseList();
    await page.locator('[data-thread-retry]').click();
    await expect(working(page)).toHaveText('Working');
    await expect(stop(page)).toBeVisible();
    await expect(page.locator('[data-thread-error]')).toHaveCount(0);
    await fixture.expectSingleSubscription();

    const emptyList = page.waitForResponse(response => {
      if (!response.url().endsWith('/__agentos/call')) return false;
      const call = response.request().postDataJSON() as Rpc;
      return call.method === 'runs.list' && call.params.conversation_id === fixture.other.id && call.params.limit === 30;
    });
    await switchTo(page, fixture.other);
    expect((await (await emptyList).json()).result.runs).toEqual([]);
    await settleRender(page);
    await expect(page.locator('[data-thread-error]')).toHaveCount(0);
    await expect(working(page)).toHaveCount(0);
    await expect(stop(page)).toHaveCount(0);
    expect(fixture.mutations).toEqual([]);
  } finally {
    await fixture.dispose();
  }
});
