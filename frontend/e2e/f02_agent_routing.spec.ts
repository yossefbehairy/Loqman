import { expect, test as base, type Page, type Request } from '@playwright/test';

type Rpc = { method: string; params: Record<string, unknown> };
type Call = Rpc & { status: number | null };
type Profile = { id: string; name: string };
type Conversation = { id: string; title: string; profile_id: string | null };
type Message = { role: string; content: string; client_message_id: string | null };

const rpcUrl = '**/__agentos/call';
const profileUnavailable = 'Conversation profile is no longer available.';
const mutationMethods = new Set([
  'ceo.ask', 'runs.start_turn', 'conversations.bind_profile', 'conversations.create',
  'conversations.add_message', 'profiles.create', 'profiles.update', 'profiles.delete',
]);

function browserRpc(request: Request): Rpc | undefined {
  if (request.method() !== 'POST' || new URL(request.url()).pathname !== '/__agentos/call') return;
  return request.postDataJSON() as Rpc;
}

const test = base.extend<{ routing: Call[] }>({
  routing: async ({ page }, provideRouting, testInfo) => {
    const calls = new Map<Request, Call>();
    const routing: Call[] = [];
    page.on('request', (request) => {
      const rpc = browserRpc(request);
      if (!rpc || !mutationMethods.has(rpc.method)) return;
      const call = { ...rpc, status: null };
      calls.set(request, call);
      routing.push(call);
    });
    page.on('response', (response) => {
      const call = calls.get(response.request());
      if (call) call.status = response.status();
    });
    try {
      await provideRouting(routing);
    } finally {
      await testInfo.attach('browser-routing', {
        body: JSON.stringify(routing, null, 2),
        contentType: 'application/json',
      });
    }
  },
});

async function call<T>(page: Page, method: string, params: Record<string, unknown> = {}): Promise<T> {
  // APIRequestContext reaches the mock worker independently of browser route interception.
  const response = await page.request.post('/__agentos/call', { data: { method, params } });
  expect(response.ok(), `Mock worker fixture ${method} (HTTP ${response.status()})`).toBe(true);
  return (await response.json()).result as T;
}

const createProfile = (page: Page, name: string) =>
  call<Profile>(page, 'profiles.create', { name, role_type: 'custom', instructions: 'Answer briefly.' });

const createChat = (page: Page, title: string, profileId?: string) =>
  call<Conversation>(page, 'conversations.create', { title, ...(profileId ? { profile_id: profileId } : {}) });

const listChats = (page: Page) => call<{ conversations: Conversation[] }>(page, 'conversations.list');
const listProfiles = (page: Page) => call<{ profiles: Profile[] }>(page, 'profiles.list');
const history = (page: Page, chat: Conversation) =>
  call<{ messages: Message[] }>(page, 'conversations.messages', { conversation_id: chat.id });

function savedRow(page: Page, chat: Conversation) {
  return page.locator(`[data-sidebar-thread-row="${chat.id}"]`);
}

function composer(page: Page) {
  return page.locator('[data-tour="composer"]').getByRole('textbox');
}

const savedOverride = { engine: 'mock', model: 'deepseek-v4.1-flash-free' };

async function rememberOverride(page: Page, chat: Conversation) {
  await page.addInitScript(({ id, choice }) => {
    localStorage.setItem('agentos.rusebot.chatExecution.v1.' + id, JSON.stringify(choice));
  }, { id: chat.id, choice: savedOverride });
}

async function openSavedChat(page: Page, chat: Conversation, mappedId?: string) {
  await page.addInitScript(({ id, mapped }) => {
    localStorage.setItem('agentos.rusebot.selected.v1', JSON.stringify(id));
    localStorage.setItem('agentos.rusebot.convAgents.v1', JSON.stringify(mapped ? { [id]: mapped } : {}));
  }, { id: chat.id, mapped: mappedId });
  await page.goto('/?ui=rusebot');
  await expect(savedRow(page, chat)).toHaveAttribute('aria-current', 'page');
  await expect(page.locator('[data-chathead-conversation]')).toHaveText(chat.title);
}

async function omitProfile(page: Page, hidden: Profile) {
  await page.route(rpcUrl, async (route) => {
    if (browserRpc(route.request())?.method !== 'profiles.list') return route.continue();
    const upstream = await route.fetch();
    const body = await upstream.json() as { result?: { profiles: Profile[] } };
    if (upstream.ok() && body.result) body.result.profiles = body.result.profiles.filter(({ id }) => id !== hidden.id);
    return route.fulfill({ status: upstream.status(), json: body });
  });
}

function expectedTurn(chat: Conversation, prompt: string, status = 200): Call {
  return {
    method: 'runs.start_turn',
    params: { conversation_id: chat.id, engine: 'mock', model_id: expect.stringMatching(/\S/), prompt, request_key: expect.any(String) },
    status,
  };
}

function expectedAsk(chat: Conversation, question: string): Call {
  return {
    method: 'ceo.ask',
    params: { conversation_id: chat.id, engine: 'mock', model_id: expect.stringMatching(/\S/), question, request_id: expect.any(String) },
    status: 200,
  };
}

async function send(page: Page, chat: Conversation, text: string) {
  const box = composer(page);
  await box.fill(text);
  await expect(page.getByRole('button', { name: 'Send', exact: true })).toBeEnabled();
  const [response] = await Promise.all([
    page.waitForResponse((candidate) => {
      const rpc = browserRpc(candidate.request());
      return rpc?.params.conversation_id === chat.id && (rpc.method === 'runs.start_turn' || rpc.method === 'ceo.ask');
    }, { timeout: 10000 }),
    box.press('Enter'),
  ]);
  return response;
}

test('A: a failed profiles.list still allows a saved specialist turn with an explicit pair', async ({ page, routing }) => {
  const specialist = await createProfile(page, 'F-02 A specialist');
  const chat = await createChat(page, 'F-02 A known binding', specialist.id);
  expect((await listProfiles(page)).profiles).toContainEqual(expect.objectContaining({ id: specialist.id }));
  expect((await listChats(page)).conversations).toContainEqual(chat);
  const error = 'F-02 profile catalog temporarily unreachable';
  await page.route(rpcUrl, (route) => browserRpc(route.request())?.method === 'profiles.list'
    ? route.fulfill({ status: 503, json: { status: 503, detail: error } })
    : route.continue());

  await rememberOverride(page, chat);
  await openSavedChat(page, chat);
  await expect(page.locator('[data-load-error]')).toContainText(error);
  const prompt = 'Summarize the blue inventory.';
  await send(page, chat, prompt);
  expect(routing).toEqual([expectedTurn(chat, prompt)]);
  expect(routing[0].params).toMatchObject({ engine: savedOverride.engine, model_id: savedOverride.model });
});

for (const staleTarget of ['specialist', 'CEO'] as const) {
  test(`B: a stale ${staleTarget} hint cannot override a saved specialist binding`, async ({ page, routing }) => {
    const a = await createProfile(page, 'F-02 B bound specialist');
    const b = staleTarget === 'specialist'
      ? await createProfile(page, 'F-02 B stale specialist')
      : { id: 'custom', name: 'Loqman' };
    const chat = await createChat(page, `F-02 B stale ${staleTarget} mapping`, a.id);
    const catalog = (await listProfiles(page)).profiles;
    expect(catalog.map(({ id }) => id)).toEqual(expect.arrayContaining([a.id, b.id]));
    expect((await listChats(page)).conversations).toContainEqual(chat);
    // Keep B in the browser catalog and omit only A. The explicit execution pair lets this isolate recipient routing.
    await omitProfile(page, a);

    await rememberOverride(page, chat);
    await openSavedChat(page, chat, b.id);
    const recipientLabel = await composer(page).getAttribute('aria-label');
    const prompt = 'Summarize the green inventory.';
    await send(page, chat, prompt);
    expect.soft(routing).toEqual([expectedTurn(chat, prompt)]);
    expect.soft(recipientLabel, 'The composer must not present the stale hint as the recipient').not.toBe(`Message ${b.name}`);
    expect((await listChats(page)).conversations.find(({ id }) => id === chat.id)?.profile_id).toBe(a.id);
  });
}

for (const metadata of ['missing', 'cached'] as const) {
  test(`C: deleted specialist with ${metadata} metadata keeps the draft and reports the missing Agent`, async ({ page, routing }, testInfo) => {
    const specialist = await createProfile(page, `F-02 C ${metadata} specialist`);
    const chat = await createChat(page, `F-02 C deleted ${metadata} profile`, specialist.id);
    const oldReply = 'Previously saved specialist reply.';
    await call(page, 'conversations.add_message', { conversation_id: chat.id, role: 'assistant', content: oldReply });
    const before = await history(page, chat);
    if (metadata === 'cached') {
      await openSavedChat(page, chat);
      await expect(composer(page)).toHaveAccessibleName(`Message ${specialist.name}`);
    }

    expect(await call(page, 'profiles.delete', { profile_id: specialist.id })).toEqual({ deleted: specialist.id });
    expect((await listProfiles(page)).profiles.map(({ id }) => id)).not.toContain(specialist.id);
    expect((await listChats(page)).conversations.find(({ id }) => id === chat.id)?.profile_id).toBe(specialist.id);
    const probe = await page.request.post('/__agentos/call', {
      data: {
        method: 'runs.start_turn',
        params: { conversation_id: chat.id, engine: 'mock', model_id: 'deepseek-v4.1-flash-free', prompt: 'Deletion contract probe.' },
      },
    });
    const rejection = await probe.json();
    expect(probe.status()).toBe(422);
    expect(rejection).toMatchObject({ detail: profileUnavailable });
    expect(await history(page, chat)).toEqual(before);
    await testInfo.attach('worker-deletion-evidence', {
      body: JSON.stringify({ conversationId: chat.id, originalProfileId: specialist.id, status: probe.status(), rejection }, null, 2),
      contentType: 'application/json',
    });

    if (metadata === 'missing') await openSavedChat(page, chat);
    await expect(page.getByRole('log').getByText(oldReply, { exact: true })).toBeVisible();
    const prompt = 'Keep this unsent inventory question.';
    if (metadata === 'missing') {
      await composer(page).fill(prompt);
      await expect(page.locator('[data-composer-no-provider]')).toContainText('This Agent is missing from the Agent list.');
      await expect(page.getByRole('button', { name: 'Send', exact: true })).toBeDisabled();
      await composer(page).press('Enter');
      expect(routing).toEqual([]);
    } else {
      const response = await send(page, chat, prompt);
      expect(response.status()).toBe(422);
      expect(routing).toEqual([expectedTurn(chat, prompt, 422)]);
      await expect(page.getByRole('alert').filter({ hasText: profileUnavailable })).toBeVisible();
    }
    await expect(composer(page)).toHaveValue(prompt);
    expect(await history(page, chat)).toEqual(before);
  });
}

test('D: a remembered conversation omitted from the list is not an unbound CEO chat', async ({ page, routing }) => {
  const specialist = await createProfile(page, 'F-02 D specialist');
  const remembered = await createChat(page, 'F-02 D omitted saved chat', specialist.id);
  const visible = await createChat(page, 'F-02 D list control');
  expect((await listChats(page)).conversations).toContainEqual(remembered);
  await page.addInitScript((id) => {
    localStorage.setItem('agentos.rusebot.selected.v1', JSON.stringify(id));
    localStorage.setItem('agentos.rusebot.convAgents.v1', '{}');
  }, remembered.id);
  await page.route(rpcUrl, async (route) => {
    if (browserRpc(route.request())?.method !== 'conversations.list') return route.continue();
    const upstream = await route.fetch();
    const body = await upstream.json() as { result?: { conversations: Conversation[] } };
    if (upstream.ok() && body.result) {
      body.result.conversations = body.result.conversations.filter(({ id }) => id !== remembered.id);
    }
    return route.fulfill({ status: upstream.status(), json: body });
  });

  const rememberedHistory = page.waitForResponse((response) => {
    const rpc = browserRpc(response.request());
    return rpc?.method === 'conversations.messages' && rpc.params.conversation_id === remembered.id;
  });
  await page.goto('/?ui=rusebot');
  expect((await rememberedHistory).ok()).toBe(true);
  await expect(savedRow(page, visible)).toBeVisible();
  await expect(savedRow(page, remembered)).toHaveCount(0);
  await expect(page.getByRole('textbox', { name: 'Loading…', exact: true })).toHaveCount(0);
  const box = composer(page);
  if (await box.isVisible() && await box.isEditable()) {
    await expect(page.locator('[data-chathead-pill]')).toContainText('mock');
    await box.fill('Summarize the yellow inventory.');
    // Observe beyond the keypress so an assertion on an initially empty call log cannot pass early.
    const observed = page.waitForRequest((request) => {
      const rpc = browserRpc(request);
      return !!rpc && mutationMethods.has(rpc.method);
    }, { timeout: 1500 }).catch((error: unknown) => {
      if (error instanceof Error && error.name === 'TimeoutError') return null;
      throw error;
    });
    await box.press('Enter');
    if (await observed) await expect.poll(() => routing[0]?.status).toEqual(expect.any(Number));
  }
  expect(routing, 'An omitted conversation row supplies no recipient for a new message').toEqual([]);
});

test('E: an existing unbound CEO chat sends a scoped CEO ask', async ({ page, routing }) => {
  const chat = await createChat(page, 'F-02 E unbound CEO chat');
  expect(chat.profile_id).toBeNull();
  await openSavedChat(page, chat);
  await expect(composer(page)).toHaveAccessibleName('Message Loqman');
  const prompt = 'Summarize the orange inventory.';
  await send(page, chat, prompt);
  expect(routing).toEqual([expectedAsk(chat, prompt)]);
});

test('E: a saved specialist with loaded metadata sends a direct turn', async ({ page, routing }) => {
  const specialist = await createProfile(page, 'F-02 E loaded specialist');
  const chat = await createChat(page, 'F-02 E bound specialist chat', specialist.id);
  await openSavedChat(page, chat);
  await expect(composer(page)).toHaveAccessibleName(`Message ${specialist.name}`);
  const prompt = 'Summarize the purple inventory.';
  await send(page, chat, prompt);
  expect(routing).toEqual([expectedTurn(chat, prompt)]);
});

test('E: a legacy unbound specialist chat binds before its direct turn', async ({ page, routing }) => {
  const specialist = await createProfile(page, 'F-02 E legacy specialist');
  const chat = await createChat(page, 'F-02 E legacy specialist chat');
  expect(chat.profile_id).toBeNull();
  await openSavedChat(page, chat, specialist.id);
  await expect(composer(page)).toHaveAccessibleName(`Message ${specialist.name}`);
  const prompt = 'Summarize the silver inventory.';
  await send(page, chat, prompt);
  expect(routing).toEqual([
    { method: 'conversations.bind_profile', params: { conversation_id: chat.id, profile_id: specialist.id }, status: 200 },
    expectedTurn(chat, prompt),
  ]);
  expect((await listChats(page)).conversations.find(({ id }) => id === chat.id)?.profile_id).toBe(specialist.id);
});
