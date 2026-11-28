import { expect, test as base, type Page, type Request, type Route } from '@playwright/test';

type Rpc = { method: string; params: Record<string, unknown> };
type Profile = { id: string; name: string; instructions: string };
type Chat = { id: string; title: string; profile_id: string | null };
type Run = { id: string; status: string };
type Message = { role: string; content: string };

const rpcUrl = '**/__agentos/call';
const monitored = new Set(['conversations.bind_profile', 'conversations.create', 'runs.start_turn', 'ceo.ask']);
const composer = (page: Page) => page.locator('[data-tour="composer"]').getByRole('textbox');
const sendButton = (page: Page) => page.getByRole('button', { name: 'Send', exact: true });
const unavailable = (page: Page) => page.getByRole('alert').filter({ hasText: /specialist.*no longer available/i });

function browserRpc(request: Request): Rpc | undefined {
  if (request.method() === 'POST' && new URL(request.url()).pathname === '/__agentos/call') {
    return request.postDataJSON() as Rpc;
  }
}

const test = base.extend<{ calls: Rpc[] }>({
  calls: async ({ page }, provide, info) => {
    const calls: Rpc[] = [];
    page.on('request', (request) => {
      const rpc = browserRpc(request);
      if (rpc && monitored.has(rpc.method)) calls.push(rpc);
    });
    try {
      await provide(calls);
    } finally {
      await info.attach('browser-routing', { body: JSON.stringify(calls), contentType: 'application/json' });
    }
  },
});

async function rpc<T>(page: Page, method: string, params: object = {}): Promise<T> {
  const res = await page.request.post('/__agentos/call', { data: { method, params } });
  expect(res.ok(), method + ' on the isolated mock worker').toBe(true);
  return (await res.json() as { result: T }).result;
}

const profile = (page: Page, name: string) =>
  rpc<Profile>(page, 'profiles.create', { name, role_type: 'custom', instructions: 'Use the silver inventory contract.' });
const conversation = (page: Page, title: string, profileId?: string) =>
  rpc<Chat>(page, 'conversations.create', { title, ...(profileId ? { profile_id: profileId } : {}) });
const history = (page: Page, chat: Chat) =>
  rpc<{ messages: Message[] }>(page, 'conversations.messages', { conversation_id: chat.id });
const runs = (page: Page, chat: Chat) =>
  rpc<{ runs: Run[] }>(page, 'runs.list', { conversation_id: chat.id });
const binding = (chat: Chat, agent: Profile): Rpc =>
  ({ method: 'conversations.bind_profile', params: { conversation_id: chat.id, profile_id: agent.id } });
const starts = (calls: Rpc[]) => calls.filter(({ method }) => method === 'runs.start_turn');
const wire = (agent: Profile, prompt: string) => `[[contract]]\n${agent.instructions}\n[[/contract]]\n\n${prompt}`;

async function expectBinding(page: Page, chat: Chat, profileId: string | null) {
  const { conversations } = await rpc<{ conversations: Chat[] }>(page, 'conversations.list');
  expect(conversations.find(({ id }) => id === chat.id)).toMatchObject({ id: chat.id, profile_id: profileId });
}

async function openChat(page: Page, chat: Chat, label: string, hint?: string) {
  await page.addInitScript(({ id, hint }) => {
    localStorage.setItem('agentos.rusebot.selected.v1', JSON.stringify(id));
    localStorage.setItem('agentos.rusebot.convAgents.v1', JSON.stringify(hint ? { [id]: hint } : {}));
  }, { id: chat.id, hint });
  await page.goto('/');
  await expect(composer(page)).toHaveAccessibleName('Message ' + label);
  await expect(page.locator('[data-chathead-conversation]')).toHaveText(chat.title);
}

async function send(page: Page, prompt: string) {
  await composer(page).fill(prompt);
  await expect(sendButton(page)).toBeEnabled();
  await sendButton(page).click();
}

function observe(page: Page, method: string, chat: Chat) {
  return page.waitForResponse((response) => {
    const call = browserRpc(response.request());
    return call?.method === method && call.params.conversation_id === chat.id;
  });
}

async function bindFailure(route: Route, status = 500, detail = 'Injected compatibility failure') {
  await route.fulfill({ status, json: { status, detail } });
}

async function assertBlocked(page: Page, calls: Rpc[], expectedCalls: Rpc[], chat: Chat, prompt: string) {
  // Wait for either rejection feedback or the erroneous dispatch so the absence check cannot pass early.
  await expect.poll(async () => starts(calls).length > starts(expectedCalls).length || await unavailable(page).isVisible()).toBe(true);
  expect(calls).toEqual(expectedCalls);
  await expect(unavailable(page)).toContainText(/draft|open a chat/i);
  await expect(composer(page)).toHaveValue(prompt);
  await expect(page.locator('[data-chathead-conversation]')).toHaveText(chat.title);
}

for (const withHistory of [false, true]) {
  test(`deleted legacy specialist blocks dispatch ${withHistory ? 'with' : 'without'} existing messages`, async ({ page, calls }) => {
    const agent = await profile(page, 'FE-02 deleted legacy specialist');
    const chat = await conversation(page, 'FE-02 unbound legacy conversation');
    const previous = 'A previously saved legacy assistant reply.';
    if (withHistory) await rpc(page, 'conversations.add_message', { conversation_id: chat.id, role: 'assistant', content: previous });
    const before = await history(page, chat);
    await openChat(page, chat, agent.name, agent.id);
    if (withHistory) await expect(page.getByRole('log').getByText(previous, { exact: true })).toBeVisible();
    await rpc(page, 'profiles.delete', { profile_id: agent.id });
    await expect(composer(page)).toHaveAccessibleName('Message ' + agent.name);

    const prompt = 'Keep this unopened specialist request.';
    const response = observe(page, 'conversations.bind_profile', chat);
    await send(page, prompt);
    const rejected = await response;
    expect(rejected.status()).toBe(404);
    expect(await rejected.json()).toEqual({ status: 404, detail: 'Profile not found' });
    await assertBlocked(page, calls, [binding(chat, agent)], chat, prompt);
    expect(await runs(page, chat)).toEqual({ runs: [] });
    expect(await history(page, chat)).toEqual(before);
    await expectBinding(page, chat, null);
  });
}

test('Retry revalidates a prepared wire prompt and blocks later fallback after a real 404', async ({ page, calls }) => {
  const agent = await profile(page, 'FE-02 prepared legacy specialist');
  const chat = await conversation(page, 'FE-02 prepared recovery');
  let bindAttempts = 0;
  let firstStart = true;
  await page.route(rpcUrl, async (route) => {
    const call = browserRpc(route.request());
    if (call?.method === 'profiles.list') {
      const response = await route.fetch();
      const body = await response.json() as { result: { profiles: Profile[] } };
      // Keep the same stale renderer metadata even after reload.
      if (!body.result.profiles.some(({ id }) => id === agent.id)) body.result.profiles.push(agent);
      return route.fulfill({ response, json: body });
    }
    if (call?.params.conversation_id === chat.id) {
      if (call.method === 'conversations.bind_profile' && ++bindAttempts !== 2) return bindFailure(route);
      if (call.method === 'runs.start_turn' && firstStart) {
        firstStart = false;
        return bindFailure(route, 502, 'The prepared legacy request did not reach the worker');
      }
    }
    await route.continue();
  });
  await openChat(page, chat, agent.name, agent.id);
  const prompt = 'Keep the original prepared request.';
  await send(page, prompt);
  const retry = page.getByRole('button', { name: 'Retry', exact: true });
  await expect(retry).toBeVisible();
  expect(starts(calls)).toHaveLength(1);
  const original = starts(calls)[0];
  expect(original.params).toMatchObject({ prompt: wire(agent, prompt), request_key: expect.any(String) });
  expect(await runs(page, chat)).toEqual({ runs: [] });

  await rpc(page, 'profiles.delete', { profile_id: agent.id });
  await retry.click();
  const blocked = [binding(chat, agent), original, binding(chat, agent)];
  await assertBlocked(page, calls, blocked, chat, prompt);
  expect(bindAttempts).toBe(2);
  await page.reload();
  await expect(composer(page)).toHaveAccessibleName('Message ' + agent.name);
  await expect(unavailable(page)).toBeVisible();
  const failedAgain = observe(page, 'conversations.bind_profile', chat);
  await retry.click();
  expect((await failedAgain).status()).toBe(500);
  blocked.push(binding(chat, agent));
  await assertBlocked(page, calls, blocked, chat, prompt);
  await page.getByRole('button', { name: 'Dismiss', exact: true }).click();
  const edited = 'Keep my newer draft for an available specialist.';
  const newAttempt = observe(page, 'conversations.bind_profile', chat);
  await send(page, edited);
  expect((await newAttempt).status()).toBe(500);
  await assertBlocked(page, calls, [...blocked, binding(chat, agent)], chat, edited);
  expect(await runs(page, chat)).toEqual({ runs: [] });
  expect(await history(page, chat)).toEqual({ messages: [] });
  await expectBinding(page, chat, null);
});

test('a conversation-not-found 404 keeps its own error and does not start a turn', async ({ page, calls }) => {
  const agent = await profile(page, 'FE-02 legacy conversation lookup');
  const chat = await conversation(page, 'FE-02 conversation lookup');
  await page.route(rpcUrl, (route) => browserRpc(route.request())?.method === 'conversations.bind_profile'
    ? bindFailure(route, 404, 'Conversation not found') : route.continue());
  await openChat(page, chat, agent.name, agent.id);
  const prompt = 'Keep the draft in the unavailable conversation.';
  await send(page, prompt);
  await expect.poll(async () => starts(calls).length > 0 ||
    await page.getByRole('alert').filter({ hasText: 'Conversation not found' }).isVisible()).toBe(true);
  expect(calls).toEqual([binding(chat, agent)]);
  await expect(unavailable(page)).toHaveCount(0);
  await expect(composer(page)).toHaveValue(prompt);
  expect(await runs(page, chat)).toEqual({ runs: [] });
});

for (const fallback of [false, true]) {
  test(fallback ? '500 binding fallback still sends the original first-message contract' : 'a valid legacy binding uses the durable profile',
    async ({ page, calls }) => {
      const agent = await profile(page, 'FE-02 available specialist');
      const chat = await conversation(page, 'FE-02 available legacy chat');
      if (fallback) {
        await page.route(rpcUrl, (route) => browserRpc(route.request())?.method === 'conversations.bind_profile'
          ? bindFailure(route) : route.continue());
      }
      await openChat(page, chat, agent.name, agent.id);
      const prompt = 'Summarize the silver inventory.';
      const response = observe(page, 'runs.start_turn', chat);
      await send(page, prompt);
      expect((await response).status()).toBe(200);
      expect(calls).toEqual([binding(chat, agent), {
        method: 'runs.start_turn',
        params: { conversation_id: chat.id, engine: 'mock', model_id: expect.any(String),
          prompt: fallback ? wire(agent, prompt) : prompt, request_key: expect.any(String) },
      }]);
      await expectBinding(page, chat, fallback ? null : agent.id);
      expect((await runs(page, chat)).runs).toHaveLength(1);
    });
}

test('an accepted legacy fallback can replay the exact payload and request key', async ({ page, calls }) => {
  const agent = await profile(page, 'FE-02 accepted fallback specialist');
  const chat = await conversation(page, 'FE-02 accepted fallback');
  let accepted: Run | undefined;
  let firstBind = true;
  await page.route(rpcUrl, async (route) => {
    const call = browserRpc(route.request());
    if (call?.params.conversation_id === chat.id) {
      if (call.method === 'conversations.bind_profile' && firstBind) {
        firstBind = false;
        return bindFailure(route);
      }
      if (call.method === 'runs.start_turn' && !accepted) {
        const response = await route.fetch();
        expect(response.ok()).toBe(true);
        accepted = (await response.json() as { result: Run }).result;
        return bindFailure(route, 502, 'Lost acknowledgement after worker acceptance');
      }
    }
    await route.continue();
  });
  await openChat(page, chat, agent.name, agent.id);
  try {
    await send(page, 'Preserve this exact turn [mock:delay=30000]');
    const retry = page.getByRole('button', { name: 'Retry', exact: true });
    await expect(retry).toBeVisible();
    expect(accepted).toBeDefined();
    const replay = observe(page, 'runs.start_turn', chat);
    await retry.click();
    const response = await replay;
    expect(response.status()).toBe(200);
    expect((await response.json()).result.id).toBe(accepted!.id);
    expect(starts(calls)).toHaveLength(2);
    expect(starts(calls)[1]).toEqual(starts(calls)[0]);
    expect((await runs(page, chat)).runs.map(({ id }) => id)).toEqual([accepted!.id]);
    expect((await history(page, chat)).messages.filter(({ role }) => role === 'user')).toHaveLength(1);
  } finally {
    if (accepted) {
      const run = await rpc<Run>(page, 'runs.get', { run_id: accepted.id });
      if (run.status === 'queued' || run.status === 'running') await rpc(page, 'runs.cancel', { run_id: run.id });
    }
  }
});

test('durable deleted binding still outranks a cached hint for an available specialist', async ({ page, calls }) => {
  const deleted = await profile(page, 'FE-02 deleted bound specialist');
  const alternate = await profile(page, 'FE-02 cached alternate specialist');
  const chat = await conversation(page, 'FE-02 durable deleted chat', deleted.id);
  await openChat(page, chat, deleted.name, alternate.id);
  await rpc(page, 'profiles.delete', { profile_id: deleted.id });
  const prompt = 'Keep this message for the original recipient.';
  const response = observe(page, 'runs.start_turn', chat);
  await send(page, prompt);
  const rejected = await response;
  expect(rejected.status()).toBe(422);
  expect(await rejected.json()).toMatchObject({ detail: 'Conversation profile is no longer available.' });
  expect(calls).toEqual([expect.objectContaining({ method: 'runs.start_turn',
    params: expect.objectContaining({ conversation_id: chat.id, prompt }) })]);
  await expect(composer(page)).toHaveValue(prompt);
  expect(await runs(page, chat)).toEqual({ runs: [] });
  await expectBinding(page, chat, deleted.id);
});

test('an unbound CEO conversation still uses its scoped ask', async ({ page, calls }) => {
  const chat = await conversation(page, 'FE-02 Loqman isolation');
  await openChat(page, chat, 'Loqman');
  const prompt = 'Hello Loqman.';
  const response = observe(page, 'ceo.ask', chat);
  await send(page, prompt);
  expect((await response).status()).toBe(200);
  expect(calls).toEqual([{ method: 'ceo.ask', params: {
    conversation_id: chat.id, engine: 'mock', model_id: expect.any(String), request_id: expect.any(String), question: prompt,
  } }]);
  await expectBinding(page, chat, null);
});
