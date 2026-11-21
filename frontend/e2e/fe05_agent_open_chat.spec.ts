import { expect, test, type Page, type Route } from '@playwright/test';

type Agent = { id: string; name: string };
type Chat = { id: string; title: string; profile_id: string | null; created_at: string; updated_at: string };
type Rpc = { method: string; params: Record<string, unknown> };
type Handler = (route: Route, call: Rpc) => Promise<void> | undefined;

const selectedKey = 'agentos.rusebot.selected.v1';
const hintsKey = 'agentos.rusebot.convAgents.v1';
const callsUrl = '**/__agentos/call';
const noWrites = ['conversations.bind_profile', 'conversations.create', 'runs.start_turn', 'ceo.ask'];
const selected = (page: Page) => page.evaluate(key => JSON.parse(localStorage.getItem(key) ?? 'null') as string | null, selectedKey);
const card = (page: Page, agent: Agent) => page.locator('[data-agent-card="' + agent.id + '"]');
const openButton = (page: Page, agent: Agent) => card(page, agent).getByRole('button', { name: 'Open chat with ' + agent.name, exact: true });
const row = (page: Page, chat: Chat) => page.locator('[data-sidebar-thread-row="' + chat.id + '"]');
const composer = (page: Page) => page.locator('[data-tour="composer"]').getByRole('textbox');

function gate() {
  let release!: () => void;
  const promise = new Promise<void>(resolve => { release = resolve; });
  return { promise, release };
}

async function rpc<T>(page: Page, method: string, params: object = {}): Promise<T> {
  const response = await page.request.post('/__agentos/call', { data: { method, params } });
  expect(response.ok(), method + ' on the isolated worker').toBe(true);
  return (await response.json() as { result: T }).result;
}

const agent = (page: Page, name: string) =>
  rpc<Agent>(page, 'profiles.create', { name, role_type: 'custom', instructions: 'Reply briefly.' });
const create = (page: Page, title: string, recipient?: Agent) =>
  rpc<Chat>(page, 'conversations.create', { title, ...(recipient ? { profile_id: recipient.id } : {}) });
const message = (page: Page, chat: Chat, role: 'user' | 'assistant') =>
  rpc(page, 'conversations.add_message', { conversation_id: chat.id, role, content: role + ' in ' + chat.title });
const list = async (page: Page) => (await rpc<{ conversations: Chat[] }>(page, 'conversations.list')).conversations;
const creates = (calls: Rpc[]) => calls.filter(call => call.method === 'conversations.create');
const probes = (calls: Rpc[]) => calls.filter(call => call.method === 'conversations.messages' && call.params.limit === 1);

async function trace(page: Page, handler?: Handler) {
  const calls: Rpc[] = [];
  await page.route(callsUrl, route => {
    const call = route.request().postDataJSON() as Rpc;
    calls.push(call);
    return handler?.(route, call) ?? route.continue();
  });
  return calls;
}

async function home(page: Page, initial?: Chat, hints: Record<string, string> = {}) {
  await page.addInitScript(({ id, hints, selectedKey, hintsKey }) => {
    localStorage.setItem(selectedKey, JSON.stringify(id));
    localStorage.setItem(hintsKey, JSON.stringify(hints));
  }, { id: initial?.id ?? null, hints, selectedKey, hintsKey });
  await page.goto('/?ui=rusebot');
  await expect(composer(page)).toBeEnabled();
  if (initial) await expect(row(page, initial)).toHaveAttribute('aria-current', 'page');
}

async function team(page: Page, target: Agent) {
  await page.locator('[data-sidebar-nav=team]').click();
  await expect(openButton(page, target)).toBeVisible();
}

async function expectSelected(page: Page, chat: Chat) {
  await expect.poll(() => selected(page)).toBe(chat.id);
  await expect(row(page, chat)).toHaveAttribute('aria-current', 'page');
  await expect(composer(page)).toBeVisible();
}

async function expectReuse(page: Page, calls: Rpc[], chat: Chat) {
  await expectSelected(page, chat);
  expect(calls.filter(call => noWrites.includes(call.method))).toEqual([]);
}

const replyList = (route: Route, conversations: Chat[]) => route.fulfill({ json: { result: { conversations } } });
const reject = (route: Route) => route.fulfill({ status: 503, json: { status: 503, detail: 'Fixture evidence unavailable.' } });

test('reuses the newest durably bound chat without hints, even when the latest message is an assistant reply', async ({ page }) => {
  const target = await agent(page, 'FE-05 active specialist');
  const first = await create(page, 'FE-05 latest assistant', target);
  const second = await create(page, 'FE-05 latest user', target);
  await message(page, first, 'user');
  await message(page, second, 'user');
  await message(page, first, 'assistant');
  const before = await list(page);

  const calls = await trace(page);
  await home(page);
  await team(page, target);
  // Use the Agent details entry point here. Other cases use the Team card.
  await card(page, target).locator('[data-bot-id]').click();
  await page.locator('[data-agent-panel]').getByRole('button', { name: 'Open chat', exact: true }).click();
  await expectReuse(page, calls, first);
  expect(probes(calls).map(call => call.params.conversation_id)).toEqual([first.id]);
  expect((await list(page)).filter(chat => chat.profile_id === target.id)).toEqual(before.filter(chat => chat.profile_id === target.id));
});

test('skips a newer empty chat, and viewing it leaves worker activity unchanged', async ({ page }) => {
  const target = await agent(page, 'FE-05 empty versus active');
  const active = await create(page, 'FE-05 older with messages', target);
  await message(page, active, 'assistant');
  const empty = await create(page, 'FE-05 newer empty', target);
  const before = (await list(page)).find(chat => chat.id === empty.id);
  const calls = await trace(page);
  await home(page, undefined, { [empty.id]: target.id, [active.id]: target.id });
  await row(page, empty).click();
  await expectSelected(page, empty);
  await team(page, target);
  await openButton(page, target).click();
  await expectReuse(page, calls, active);
  expect(probes(calls).map(call => call.params.conversation_id)).toEqual([empty.id, active.id]);
  expect((await list(page)).find(chat => chat.id === empty.id)).toEqual(before);
});

test('when all eligible chats are empty, reuses the newest by creation time', async ({ page }) => {
  const target = await agent(page, 'FE-05 all empty');
  const older = await create(page, 'FE-05 older empty', target);
  const newer = await create(page, 'FE-05 newest empty', target);
  const calls = await trace(page);
  await home(page, undefined, { [older.id]: target.id, [newer.id]: target.id });
  await team(page, target);
  await openButton(page, target).click();
  await expectReuse(page, calls, newer);
  expect(probes(calls)).toHaveLength(2);
});

test('breaks equal activity timestamps by conversation ID independently of the response and hint order', async ({ page }) => {
  const target = await agent(page, 'FE-05 activity tie');
  const pair = await Promise.all([
    create(page, 'FE-05 tie one', target), create(page, 'FE-05 tie two', target),
  ]);
  const [first, second] = pair.sort((a, b) => a.id < b.id ? -1 : 1);
  await message(page, first, 'user');
  await message(page, second, 'assistant');
  const rows = await list(page);
  const stamp = rows[0].updated_at;
  const calls = await trace(page, (route, call) => call.method === 'conversations.list'
    ? replyList(route, [
      { ...second, updated_at: stamp }, { ...first, updated_at: stamp },
      ...rows.filter(chat => chat.profile_id !== target.id),
    ]) : undefined);
  await home(page, undefined, { [second.id]: target.id, [first.id]: target.id });
  await team(page, target);
  await openButton(page, target).click();
  await expectReuse(page, calls, first);
});

test('ignores CEO, another Agent, and unbound legacy hints before creating a durably bound chat', async ({ page }) => {
  const target = await agent(page, 'FE-05 safe creation');
  const other = await agent(page, 'FE-05 different specialist');
  const ceo = await create(page, 'FE-05 CEO conversation');
  const legacy = await create(page, 'FE-05 unbound legacy conversation');
  const different = await create(page, 'FE-05 other specialist conversation', other);
  for (const chat of [ceo, legacy, different]) await message(page, chat, 'user');
  const calls = await trace(page);
  await home(page, undefined, { [ceo.id]: target.id, [legacy.id]: target.id, [different.id]: target.id });
  await team(page, target);
  await openButton(page, target).click();
  await expect(composer(page)).toBeVisible();
  const owned = (await list(page)).filter(chat => chat.profile_id === target.id);
  expect(owned).toHaveLength(1);
  await expectSelected(page, owned[0]);
  expect(creates(calls)).toEqual([{
    method: 'conversations.create', params: { title: target.name + ' — custom', profile_id: target.id },
  }]);
  expect(calls.filter(call => call.method === 'conversations.bind_profile')).toEqual([]);
});

test('rapid repeated Open Chat clicks share one creation request', async ({ page }) => {
  const target = await agent(page, 'FE-05 repeated open');
  const requested = gate();
  const release = gate();
  const calls = await trace(page, (route, call) => {
    if (call.method !== 'conversations.create') return;
    requested.release();
    return release.promise.then(() => route.continue());
  });
  await home(page);
  await team(page, target);
  try {
    await openButton(page, target).evaluate(button => {
      (button as HTMLButtonElement).click();
      (button as HTMLButtonElement).click();
    });
    await requested.promise;
  } finally {
    release.release();
  }
  await expect(composer(page)).toBeVisible();
  expect(creates(calls)).toHaveLength(1);
  const owned = (await list(page)).filter(chat => chat.profile_id === target.id);
  expect(owned).toHaveLength(1);
  await expectSelected(page, owned[0]);
});

for (const navigation of ['saved chat', 'New Chat', 'another view'] as const) {
  test('a late create cannot replace ' + navigation, async ({ page }) => {
    const target = await agent(page, 'FE-05 delayed create');
    const other = await agent(page, 'FE-05 navigation owner');
    const original = await create(page, 'FE-05 original selected', other);
    const destination = await create(page, 'FE-05 deliberate selection', other);
    const accepted = gate();
    const release = gate();
    const calls = await trace(page, (route, call) => {
      if (call.method !== 'conversations.create') return;
      return (async () => {
        const response = await route.fetch();
        accepted.release();
        await release.promise;
        await route.fulfill({ response });
      })();
    });
    await home(page, original);
    await team(page, target);
    try {
      await openButton(page, target).click();
      await accepted.promise;
      if (navigation === 'saved chat') await row(page, destination).click();
      if (navigation === 'New Chat') {
        await newChat(page);
        await composer(page).fill('Keep this independent draft.');
      }
      if (navigation === 'another view') await page.locator('[data-sidebar-nav=results]').click();
    } finally {
      release.release();
    }
    const created = (await list(page)).find(chat => chat.profile_id === target.id);
    expect(created).toBeDefined();
    // Inclusion signals that the renderer handled the delayed acknowledgement.
    await expect(row(page, created!)).toBeVisible();
    expect(creates(calls)).toHaveLength(1);
    if (navigation === 'saved chat') await expectSelected(page, destination);
    if (navigation === 'New Chat') {
      expect(await selected(page)).toBeNull();
      await expect(composer(page)).toHaveValue('Keep this independent draft.');
    }
    if (navigation === 'another view') {
      expect(await selected(page)).toBe(original.id);
      await expect(page.locator('main[data-view=results]')).toBeVisible();
    }
  });
}

test('superseding a lookup before it completes prevents a create', async ({ page }) => {
  const target = await agent(page, 'FE-05 superseded lookup');
  const original = await create(page, 'FE-05 lookup navigation destination');
  const requested = gate();
  const release = gate();
  let opening = false;
  const calls = await trace(page, (route, call) => {
    if (!opening || !['conversations.list', 'conversations.create'].includes(call.method)) return;
    requested.release();
    return release.promise.then(() => route.continue());
  });
  await home(page, original);
  await team(page, target);
  opening = true;
  const responded = page.waitForResponse(response => {
    const req = response.request();
    if (req.method() !== 'POST' || new URL(req.url()).pathname !== '/__agentos/call') return false;
    const call = req.postDataJSON() as Rpc;
    return ['conversations.list', 'conversations.create'].includes(call.method);
  });
  try {
    await openButton(page, target).click();
    await requested.promise;
    await page.locator('[data-sidebar-nav=results]').click();
  } finally {
    release.release();
  }
  await (await responded).finished();
  // A browser round trip lets the response handler and its React updates run.
  await page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
  expect(creates(calls)).toEqual([]);
  expect(await selected(page)).toBe(original.id);
  await expect(page.locator('main[data-view=results]')).toBeVisible();
});

for (const evidence of ['capped list', 'failed list', 'failed message read'] as const) {
  test('keeps navigation and creates nothing on a ' + evidence, async ({ page }) => {
    const target = await agent(page, 'FE-05 unavailable evidence');
    const original = await create(page, 'FE-05 stay here');
    if (evidence === 'failed message read') {
      await create(page, 'FE-05 probe older', target);
      await create(page, 'FE-05 probe newer', target);
    }
    let opening = false;
    const capped = Array.from({ length: 500 }, (_, index) => ({ ...original, id: 'capped-' + index }));
    const calls = await trace(page, (route, call) => {
      if (!opening) return;
      if (call.method === 'conversations.list' && evidence === 'capped list') return replyList(route, capped);
      if (call.method === 'conversations.list' && evidence === 'failed list') return reject(route);
      if (call.method === 'conversations.messages' && call.params.limit === 1 && evidence === 'failed message read') return reject(route);
    });
    await home(page, original);
    await team(page, target);
    opening = true;
    await openButton(page, target).click();
    const error = page.getByRole('alert').filter({ hasText: /could not verify|too many chats/i });
    await expect.poll(async () => creates(calls).length > 0 || await error.isVisible()).toBe(true);
    expect(creates(calls)).toEqual([]);
    await expect(error).toContainText(/try again|retry/i);
    expect(await selected(page)).toBe(original.id);
    await expect(page.locator('main[data-view=team]')).toBeVisible();
  });
}

async function newChat(page: Page) {
  await page.locator('[data-sidebar-new]').click();
  await page.locator('[data-new-chat]').click();
}

test('explicit New Chat sends into distinct durable chats and keeps each draft with its chat', async ({ page }) => {
  const target = await agent(page, 'FE-05 independent drafts');
  const original = await create(page, 'FE-05 starting chat', target);
  const calls = await trace(page);
  await home(page, original);
  const created: Chat[] = [];
  for (const suffix of ['first', 'second']) {
    await newChat(page);
    await expect.poll(() => selected(page)).toBeNull();
    await expect(composer(page)).toHaveValue('');
    const prompt = 'FE-05 independent ' + suffix + ' message';
    await composer(page).fill(prompt);
    expect(creates(calls)).toHaveLength(created.length);
    const send = page.getByRole('button', { name: 'Send', exact: true });
    await expect(send).toBeEnabled();
    await send.click();
    await expect(page.getByRole('log').getByText(/Mock task execution succeeded/)).toBeVisible();
    const chat = (await list(page)).find(chat => chat.title === prompt && chat.profile_id === target.id);
    expect(chat).toBeDefined();
    await expectSelected(page, chat!);
    created.push(chat!);
    await composer(page).fill('Unsent ' + suffix + ' draft');
  }
  expect(new Set([original.id, ...created.map(chat => chat.id)]).size).toBe(3);
  expect(creates(calls).map(call => call.params.profile_id)).toEqual([target.id, target.id]);
  for (const [index, chat] of created.entries()) {
    await row(page, chat).click();
    await expect(composer(page)).toHaveValue(index === 0 ? 'Unsent first draft' : 'Unsent second draft');
  }
});
