import { expect, test, type Page } from '@playwright/test';

type Agent = { id: string; name: string };
type Chat = { id: string; title: string; profile_id: string | null; updated_at: string };
type ConversationList = { conversations: Chat[] };
type Rpc = { method: string; params: Record<string, unknown> };

const row = (page: Page, id: string) => page.locator(`[data-sidebar-thread-row="${id}"]`);
const composer = (page: Page) => page.locator('[data-tour="composer"]').getByRole('textbox');
const sendButton = (page: Page) => page.getByRole('button', { name: 'Send', exact: true });
const selected = (page: Page) => page.evaluate(() => JSON.parse(localStorage.getItem('agentos.rusebot.selected.v1') ?? 'null') as string | null);

function gate() {
  let release!: () => void;
  const promise = new Promise<void>(resolve => { release = resolve; });
  return { promise, release };
}

type HeldList = { arrived: ReturnType<typeof gate>; release: ReturnType<typeof gate>; snapshot?: Chat[] };

async function rpc<T>(page: Page, method: string, params: object = {}) {
  const response = await page.request.post('/__agentos/call', { data: { method, params } });
  expect(response.ok(), method + ' on the isolated worker').toBe(true);
  return (await response.json() as { result: T }).result;
}

const agent = (page: Page, name: string) =>
  rpc<Agent>(page, 'profiles.create', { name, role_type: 'custom', instructions: 'Reply briefly.' });
const create = (page: Page, title: string, recipient?: Agent) =>
  rpc<Chat>(page, 'conversations.create', { title, ...(recipient ? { profile_id: recipient.id } : {}) });
const list = async (page: Page) => (await rpc<ConversationList>(page, 'conversations.list')).conversations;

function nextResponse(page: Page, method: string) {
  return page.waitForResponse(response => {
    const request = response.request();
    return request.method() === 'POST' && new URL(request.url()).pathname === '/__agentos/call'
      && (request.postDataJSON() as Rpc).method === method;
  });
}

async function expectChat(page: Page, id: string, recipient: Agent) {
  await expect(row(page, id)).toHaveAttribute('aria-current', 'page');
  expect(await selected(page)).toBe(id);
  await expect(composer(page)).toHaveAccessibleName('Message ' + recipient.name);
}

async function home(page: Page, chat: Chat, recipient: Agent, hints: Record<string, string> = {}) {
  await page.addInitScript(({ id, hints }) => {
    localStorage.setItem('agentos.rusebot.selected.v1', JSON.stringify(id));
    localStorage.setItem('agentos.rusebot.convAgents.v1', JSON.stringify(hints));
  }, { id: chat.id, hints });
  await page.goto('/');
  await expectChat(page, chat.id, recipient);
}

async function send(page: Page, prompt: string) {
  await composer(page).fill(prompt);
  await expect(sendButton(page)).toBeEnabled();
  await sendButton(page).click();
}

async function trackLists(page: Page, { failOld = false, failFirstStartIn }: {
  failOld?: boolean;
  failFirstStartIn?: string;
} = {}) {
  let nextList: HeldList | undefined;
  const calls: Rpc[] = [];
  await page.route('**/__agentos/call', async route => {
    const call = route.request().postDataJSON() as Rpc;
    calls.push(call);
    if (call.method === 'runs.start_turn' && call.params.conversation_id === failFirstStartIn) {
      failFirstStartIn = undefined;
      return route.fulfill({ status: 503, json: { status: 503, detail: 'Fixture pauses execution after binding.' } });
    }
    if (call.method !== 'conversations.list' || !nextList) return route.continue();
    const held = nextList;
    nextList = undefined;
    // Capture the worker's actual list before any later creates, binds or messages.
    const response = await route.fetch();
    expect(response.ok()).toBe(true);
    held.snapshot = (await response.json() as { result: ConversationList }).result.conversations;
    held.arrived.release();
    await held.release.promise;
    if (failOld) return route.fulfill({ status: 503, json: { status: 503, detail: 'Fixture list read failed.' } });
    return route.fulfill({ response });
  });
  return {
    calls,
    holdNext() {
      const held: HeldList = { arrived: gate(), release: gate() };
      nextList = held;
      return held;
    },
  };
}

async function finishHeldRefresh(page: Page, held: HeldList) {
  // The completing run checks approvals after it finishes handling the list response.
  const handled = nextResponse(page, 'approvals.list');
  held.release.release();
  await handled;
}

test('AUD-01: Retry restores the initial chat list and resumes startup discovery', async ({ page }) => {
  const recipient = await agent(page, 'AUD-01 remembered specialist');
  const remembered = await create(page, 'AUD-01 remembered chat', recipient);
  const other = await create(page, 'AUD-01 background chat', recipient);
  const retryArrived = gate();
  const releaseRetry = gate();
  const calls: Rpc[] = [];
  const listFailure = 'Fixture lost the initial conversation list.';
  let listReads = 0;
  await page.route('**/__agentos/call', async route => {
    const call = route.request().postDataJSON() as Rpc;
    calls.push(call);
    if (call.method !== 'conversations.list') return route.continue();
    listReads++;
    if (listReads === 1) return route.fulfill({ status: 503, json: { detail: listFailure } });
    retryArrived.release();
    await releaseRetry.promise;
    return route.continue();
  });
  await page.addInitScript(cid => {
    localStorage.setItem('agentos.rusebot.selected.v1', JSON.stringify(cid));
    localStorage.setItem('agentos.rusebot.convAgents.v1', JSON.stringify({ [cid]: 'custom' }));
  }, remembered.id);

  const alert = page.getByRole('alert').filter({ hasText: listFailure });
  const mutations = () => calls.filter(call =>
    ['conversations.create', 'conversations.bind_profile', 'conversations.add_message', 'ceo.ask', 'runs.start_turn'].includes(call.method));
  try {
    await page.goto('/');
    await expect(alert).toBeVisible();
    expect(await selected(page)).toBe(remembered.id);
    await expect(row(page, remembered.id)).toHaveCount(0);
    expect(mutations()).toEqual([]);
    const importsBeforeRetry = calls.filter(call => call.method === 'conversations.import_ceo').length;

    await alert.getByRole('button', { name: 'Retry', exact: true }).click();
    await retryArrived.promise;
    await expect(alert, 'Keep the startup error visible until the list succeeds').toBeVisible();
    expect(mutations()).toEqual([]);
    releaseRetry.release();

    await expectChat(page, remembered.id, recipient);
    await expect(row(page, other.id)).toBeVisible();
    await expect(alert).toHaveCount(0);
    await expect.poll(() => calls.filter(call => call.method === 'runs.list' && call.params.conversation_id === other.id))
      .toEqual([{ method: 'runs.list', params: { conversation_id: other.id, limit: 1 } }]);
    expect(calls.filter(call => call.method === 'conversations.import_ceo')).toHaveLength(importsBeforeRetry);
    expect(listReads).toBe(2);
    expect(mutations()).toEqual([]);

    const started = nextResponse(page, 'runs.start_turn');
    await send(page, 'Continue with the remembered specialist.');
    expect((await started).ok()).toBe(true);
    expect(mutations()).toEqual([expect.objectContaining({
      method: 'runs.start_turn', params: expect.objectContaining({ conversation_id: remembered.id }),
    })]);
  } finally {
    releaseRetry.release();
  }
});

for (const failOld of [false, true]) {
  test(failOld ? 'UX-05: a failed background refresh preserves a newly opened chat' : 'UX-05: an older list cannot erase a newly opened chat', async ({ page }) => {
    const first = await agent(page, 'UX-05 refresh owner');
    const target = await agent(page, 'UX-05 Open Chat target');
    const a = await create(page, 'UX-05 original chat', first);
    const browser = await trackLists(page, { failOld });
    await home(page, a, first);

    const old = browser.holdNext();
    try {
      await send(page, 'Complete the refresh owner task.');
      await old.arrived.promise;
      expect(old.snapshot?.some(chat => chat.id === a.id)).toBe(true);
      expect(old.snapshot?.some(chat => chat.profile_id === target.id)).toBe(false);

      await page.locator('[data-sidebar-nav=team]').click();
      await page.locator(`[data-agent-card="${target.id}"]`).getByRole('button', {
        name: 'Open chat with ' + target.name, exact: true,
      }).click();
      await expect(composer(page)).toHaveAccessibleName('Message ' + target.name);
      const b = (await list(page)).find(chat => chat.profile_id === target.id);
      if (!b) throw new Error('The worker did not confirm Agent Open Chat creation');
      await expectChat(page, b.id, target);

      await finishHeldRefresh(page, old);
      await expectChat(page, b.id, target);

      if (!failOld) {
        const used = nextResponse(page, 'runs.start_turn');
        await send(page, 'Send in the newly opened chat.');
        const firstResponse = await used;
        expect(firstResponse.ok()).toBe(true);
        expect(firstResponse.request().postDataJSON()).toMatchObject({
          params: { conversation_id: b.id, prompt: 'Send in the newly opened chat.' },
        });

        // New Chat from B should still create a separate conversation for the same Agent.
        await page.locator('[data-sidebar-new]').click();
        await page.locator('[data-new-chat]').click();
        await expect.poll(() => selected(page)).toBeNull();
        const sent = nextResponse(page, 'runs.start_turn');
        await send(page, 'Send in a separate chat for the same Agent.');
        const response = await sent;
        expect(response.ok()).toBe(true);
        const secondId = (response.request().postDataJSON() as Rpc).params.conversation_id as string;
        expect(secondId).not.toBe(b.id);
        await expectChat(page, secondId, target);
        expect((await list(page)).filter(chat => chat.profile_id === target.id).map(chat => chat.id).sort())
          .toEqual([b.id, secondId].sort());
        expect(browser.calls.some(call => call.method === 'ceo.ask')).toBe(false);
      } else {
        await composer(page).fill('The new chat still accepts a draft.');
        await expect(sendButton(page)).toBeEnabled();
      }
    } finally {
      old.release.release();
    }
  });
}

test('UX-05: the latest worker list updates sidebar ordering and survives a late earlier read', async ({ page }) => {
  const first = await agent(page, 'UX-05 earlier reader');
  const second = await agent(page, 'UX-05 later reader');
  const b = await create(page, 'UX-05 later completion', second);
  const a = await create(page, 'UX-05 earlier completion', first);
  const browser = await trackLists(page);
  await home(page, a, first);
  const old = browser.holdNext();
  let latest: HeldList | undefined;
  const order = () => page.locator('[data-sidebar-thread-row]').evaluateAll(
    (rows, ids) => rows.map(node => node.getAttribute('data-sidebar-thread-row')).filter(id => id && ids.includes(id)),
    [a.id, b.id],
  );
  try {
    await send(page, 'Complete the earlier owner.');
    await old.arrived.promise;
    expect(old.snapshot?.filter(chat => [a.id, b.id].includes(chat.id)).map(chat => chat.id)).toEqual([a.id, b.id]);

    await row(page, b.id).click();
    await expectChat(page, b.id, second);
    latest = browser.holdNext();
    await send(page, 'Complete the later owner.');
    await latest.arrived.promise;
    expect(latest.snapshot?.find(chat => chat.id === b.id)?.updated_at)
      .not.toBe(old.snapshot?.find(chat => chat.id === b.id)?.updated_at);
    await finishHeldRefresh(page, latest);
    await expect.poll(order).toEqual([b.id, a.id]);

    await finishHeldRefresh(page, old);
    await expect.poll(order).toEqual([b.id, a.id]);
    await expectChat(page, b.id, second);
  } finally {
    latest?.release.release();
    old.release.release();
  }
});

test('UX-05: an earlier list cannot undo a durable Agent binding acknowledgement', async ({ page }) => {
  const first = await agent(page, 'UX-05 previous run Agent');
  const recipient = await agent(page, 'UX-05 binding recipient');
  const a = await create(page, 'UX-05 binding race source', first);
  const legacy = await create(page, 'UX-05 unbound legacy chat');
  const browser = await trackLists(page, { failFirstStartIn: legacy.id });
  await home(page, a, first, { [legacy.id]: recipient.id });
  const old = browser.holdNext();
  try {
    await send(page, 'Hold a list containing the unbound chat.');
    await old.arrived.promise;
    expect(old.snapshot?.find(chat => chat.id === legacy.id)?.profile_id).toBeNull();
    await row(page, legacy.id).click();
    await expectChat(page, legacy.id, recipient);
    await send(page, 'Bind the Agent before the held list completes.');
    await expect(page.getByRole('alert')).toContainText('Fixture pauses execution after binding.');
    expect((await list(page)).find(chat => chat.id === legacy.id)?.profile_id).toBe(recipient.id);

    await finishHeldRefresh(page, old);
    await expectChat(page, legacy.id, recipient);
    const sent = nextResponse(page, 'runs.start_turn');
    await send(page, 'Continue directly to the durably bound Agent.');
    expect((await sent).ok()).toBe(true);
    expect(browser.calls.filter(call => call.method === 'conversations.bind_profile')).toEqual([{
      method: 'conversations.bind_profile',
      params: { conversation_id: legacy.id, profile_id: recipient.id },
    }]);
    expect(browser.calls.some(call => call.method === 'ceo.ask')).toBe(false);
  } finally {
    old.release.release();
  }
});
