import { expect, test, type Page, type Request } from '@playwright/test';

type SendMethod = 'ceo.ask' | 'runs.start_turn';

function sendPayload(method: SendMethod, conversationId: string | null, text: string | null) {
  return method === 'ceo.ask'
    ? { method, conversation_id: conversationId, question: text }
    : { method, conversation_id: conversationId, prompt: text };
}

type SendPayload = ReturnType<typeof sendPayload>;
type Exchange = { rpc: SendPayload; status: number | null };

function outboundSend(request: Request): SendPayload | null {
  if (request.method() !== 'POST' || new URL(request.url()).pathname !== '/__agentos/call') return null;
  const { method, params } = request.postDataJSON() as {
    method: string;
    params?: { conversation_id?: string; prompt?: string; question?: string };
  };
  if (method !== 'ceo.ask' && method !== 'runs.start_turn') return null;
  const text = method === 'ceo.ask' ? params?.question : params?.prompt;
  return sendPayload(method, params?.conversation_id ?? null, text ?? null);
}

async function call<T>(page: Page, method: string, params: Record<string, unknown>): Promise<T> {
  const response = await page.request.post('/__agentos/call', { data: { method, params } });
  await expect(response, `Mock worker fixture ${method}`).toBeOK();
  return (await response.json()).result as T;
}

async function selectChat(page: Page, chat: { id: string; title: string }) {
  const drawer = page.getByRole('button', { name: 'Open chat list' });
  if (await drawer.isVisible()) await drawer.click();
  const row = page.locator(`[data-sidebar-thread-row="${chat.id}"]`);
  await row.click();
  await expect(row).toHaveAttribute('aria-current', 'page');
  await expect(page.locator('[data-chathead-conversation]')).toHaveText(chat.title);
}

async function send(page: Page, agentName: string, text: string) {
  const composer = page.getByRole('textbox', { name: `Message ${agentName}`, exact: true });
  await composer.fill(text);
  await expect(page.getByRole('button', { name: 'Send', exact: true })).toBeEnabled();
  await composer.press('Enter');
}

async function expectCompletedSend(page: Page, text: string) {
  const transcript = page.getByRole('log');
  await expect(transcript.locator('[data-role="user"]').getByText(text, { exact: true })).toHaveCount(1);
  await expect(transcript.locator('[data-role="assistant"]')).toHaveCount(1);
  await expect(page.locator('[data-working]')).toHaveCount(0);
}

const cases = [
  {
    id: 'A',
    title: 'F-01 A: retries the specialist prompt in Chat A after Chat B succeeds',
    methodA: 'runs.start_turn',
    methodB: 'runs.start_turn',
    failB: false,
  },
  {
    id: 'B',
    title: 'F-01 B: retries the CEO question in Chat A after a specialist send fails in Chat B',
    methodA: 'ceo.ask',
    methodB: 'runs.start_turn',
    failB: true,
  },
  {
    id: 'C',
    title: 'F-01 C: retries the specialist prompt in Chat A after a CEO question fails in Chat B',
    methodA: 'runs.start_turn',
    methodB: 'ceo.ask',
    failB: true,
  },
] as const;

for (const scenario of cases) {
  test(scenario.title, async ({ page }, testInfo) => {
    const prefix = `F-01 case ${scenario.id}`;
    const specialist = `${prefix} specialist`;
    const promptA = `${prefix}: synthetic prompt for Chat A`;
    const promptB = `${prefix}: synthetic prompt for Chat B`;
    const errorA = `${prefix}: injected send failure in Chat A`;
    const errorB = `${prefix}: injected send failure in Chat B`;
    const { id: profileId } = await call<{ id: string }>(page, 'profiles.create', {
      name: specialist, role_type: 'custom', description: 'Retry test specialist', instructions: 'Answer briefly.',
    });
    const chatA = await call<{ id: string; title: string }>(page, 'conversations.create', {
      title: `${prefix} chat A`, ...(scenario.methodA === 'runs.start_turn' ? { profile_id: profileId } : {}),
    });
    const chatB = await call<{ id: string; title: string }>(page, 'conversations.create', {
      title: `${prefix} chat B`, ...(scenario.methodB === 'runs.start_turn' ? { profile_id: profileId } : {}),
    });
    expect(chatA.id).not.toBe(chatB.id);

    const rpcA = sendPayload(scenario.methodA, chatA.id, promptA);
    const rpcB = sendPayload(scenario.methodB, chatB.id, promptB);
    const expected = [
      { rpc: rpcA, status: 502 },
      { rpc: rpcB, status: scenario.failB ? 502 : 200 },
      { rpc: rpcA, status: 200 },
    ];
    const failures = new Map([[JSON.stringify(rpcA), errorA]]);
    if (scenario.failB) failures.set(JSON.stringify(rpcB), errorB);

    const exchanges = new Map<Request, Exchange>();
    const observed = () => [...exchanges.values()];
    page.on('request', (request) => {
      const rpc = outboundSend(request);
      if (rpc) exchanges.set(request, { rpc, status: null });
    });
    page.on('response', (response) => {
      const exchange = exchanges.get(response.request());
      if (exchange) exchange.status = response.status();
    });
    await page.route('**/__agentos/call', (route) => {
      const key = JSON.stringify(outboundSend(route.request()));
      const detail = failures.get(key);
      if (!detail) return route.continue();
      failures.delete(key);
      return route.fulfill({ status: 502, json: { status: 502, detail } });
    });

    const retryA = page.getByRole('alert').filter({ hasText: errorA }).getByRole('button', { name: 'Retry', exact: true });
    let retryClicked = false;
    try {
      await page.goto('/?ui=rusebot');
      await expect(page.locator('[data-chathead-pill]')).toContainText('mock', { timeout: 30000 });

      await selectChat(page, chatA);
      await send(page, scenario.methodA === 'ceo.ask' ? 'Loqman' : specialist, promptA);
      await expect(retryA).toBeVisible();
      await expect.poll(observed, { message: 'Chat A received its controlled send failure' }).toEqual(expected.slice(0, 1));

      await selectChat(page, chatB);
      await expect(retryA).toHaveCount(0);
      await send(page, scenario.methodB === 'ceo.ask' ? 'Loqman' : specialist, promptB);
      if (scenario.failB) {
        await expect(page.getByRole('alert').filter({ hasText: errorB }).getByRole('button', { name: 'Retry', exact: true })).toBeVisible();
      } else {
        await expectCompletedSend(page, promptB);
      }
      await expect.poll(observed, { message: 'Both original sends used their own chat and prompt' }).toEqual(expected.slice(0, 2));

      await selectChat(page, chatA);
      await expect(retryA).toBeVisible();
      await retryA.click();
      retryClicked = true;
      await expect.poll(() => observed().slice(2).map(({ rpc }) => rpc), {
        message: 'Retry in Chat A must emit exactly one RPC with A’s original method, conversation ID and text',
      }).toEqual([rpcA]);
      await expectCompletedSend(page, promptA);
      expect(observed(), 'Exactly three sends completed, with no duplicate send in Chat B').toEqual(expected);
    } finally {
      await testInfo.attach('f01-outbound-rpcs', {
        body: JSON.stringify({ conversations: { A: chatA.id, B: chatB.id }, retryClicked, expected, observed: observed() }, null, 2),
        contentType: 'application/json',
      });
    }
  });
}

function gate() {
  let release!: () => void;
  const pending = new Promise<void>((resolve) => { release = resolve; });
  return { pending, release };
}

function recordSends(page: Page) {
  const requests: SendPayload[] = [];
  page.on('request', (request) => {
    const rpc = outboundSend(request);
    if (rpc) requests.push(rpc);
  });
  return requests;
}

function retryButton(page: Page, message: string) {
  return page.getByRole('alert').filter({ hasText: message }).getByRole('button', { name: 'Retry', exact: true });
}

async function openMockChat(page: Page) {
  await page.goto('/?ui=rusebot');
  await expect(page.locator('[data-chathead-pill]')).toContainText('mock', { timeout: 30000 });
}

function createSpecialist(page: Page, name: string) {
  return call<{ id: string }>(page, 'profiles.create', {
    name, role_type: 'custom', description: 'F-01 regression', instructions: 'Answer briefly.',
  });
}

function createChat(page: Page, title: string, profileId?: string) {
  return call<{ id: string; title: string }>(page, 'conversations.create', { title, profile_id: profileId });
}

async function newChat(page: Page) {
  const drawer = page.getByRole('button', { name: 'Open chat list' });
  if (await drawer.isVisible()) await drawer.click();
  await page.locator('[data-sidebar-new]').click();
  await page.locator('[data-new-chat]').click();
}

for (const method of ['ceo.ask', 'runs.start_turn'] as const) {
  test(`F-01: ${method} retries once and preserves a newer draft through failure and success`, async ({ page }) => {
    const agent = method === 'ceo.ask' ? 'Loqman' : 'F-01 draft specialist';
    const profile = method === 'ceo.ask' ? undefined : await createSpecialist(page, agent);
    const chat = await createChat(page, `F-01 draft ${method}`, profile?.id);
    const original = `F-01 original ${method}`;
    const expected = sendPayload(method, chat.id, original);
    const requests = recordSends(page);
    const held = gate();
    let count = 0;
    await page.route('**/__agentos/call', async (route) => {
      if (!outboundSend(route.request())) return route.continue();
      const attempt = ++count;
      if (attempt === 2) await held.pending;
      if (attempt <= 2) return route.fulfill({ status: 502, json: { detail: `F-01 draft failure ${attempt}` } });
      return route.continue();
    });
    await openMockChat(page);
    await selectChat(page, chat);
    await send(page, agent, original);
    const firstRetry = retryButton(page, 'F-01 draft failure 1');
    await expect(firstRetry).toBeVisible();
    const box = page.getByRole('textbox', { name: `Message ${agent}`, exact: true });
    await box.fill('New unsent draft before Retry');
    try {
      // Two activations in the same browser task, before relying on a repaint to hide the banner.
      await firstRetry.evaluate((button: HTMLButtonElement) => { button.click(); button.click(); });
      await expect.poll(() => requests).toEqual([expected, expected]);
      await expect(firstRetry).toHaveCount(0);
      await expect(box).toHaveValue('New unsent draft before Retry');
      await box.fill('New unsent draft during Retry');
    } finally {
      held.release();
    }
    const secondRetry = retryButton(page, 'F-01 draft failure 2');
    await expect(secondRetry).toBeVisible();
    await expect(box).toHaveValue('New unsent draft during Retry');
    await secondRetry.click();
    await expectCompletedSend(page, original);
    expect(requests).toEqual([expected, expected, expected]);
    await expect(box).toHaveValue('New unsent draft during Retry');
    await page.reload();
    await expect(box).toHaveValue('New unsent draft during Retry');
  });
}

test('F-01: two specialist failures completing in reverse order keep independently retryable prompts', async ({ page }) => {
  const agent = 'F-01 overlapping specialist';
  const profile = await createSpecialist(page, agent);
  const a = await createChat(page, 'F-01 overlap A', profile.id);
  const b = await createChat(page, 'F-01 overlap B', profile.id);
  const promptA = 'F-01 overlapping prompt A';
  const promptB = 'F-01 overlapping prompt B';
  const rpcA = sendPayload('runs.start_turn', a.id, promptA);
  const rpcB = sendPayload('runs.start_turn', b.id, promptB);
  const requests = recordSends(page);
  const held = gate();
  let aCalls = 0;
  let bCalls = 0;
  await page.route('**/__agentos/call', async (route) => {
    const rpc = outboundSend(route.request());
    if (rpc?.conversation_id === a.id) {
      const n = ++aCalls;
      if (n === 2) await held.pending;
      if (n <= 2) return route.fulfill({ status: 502, json: { detail: `F-01 overlap A ${n}` } });
    }
    if (rpc?.conversation_id === b.id && ++bCalls === 1) {
      return route.fulfill({ status: 502, json: { detail: 'F-01 overlap B 1' } });
    }
    return route.continue();
  });
  await openMockChat(page);
  await selectChat(page, a);
  await send(page, agent, promptA);
  try {
    await retryButton(page, 'F-01 overlap A 1').click();
    await expect.poll(() => requests).toEqual([rpcA, rpcA]);
    await selectChat(page, b);
    await send(page, agent, promptB);
    await expect(retryButton(page, 'F-01 overlap B 1')).toBeVisible();
  } finally {
    held.release();
  }
  await selectChat(page, a);
  await expect(retryButton(page, 'F-01 overlap A 2')).toBeVisible();
  await selectChat(page, b);
  await retryButton(page, 'F-01 overlap B 1').click();
  await expectCompletedSend(page, promptB);
  await selectChat(page, a);
  await retryButton(page, 'F-01 overlap A 2').click();
  await expectCompletedSend(page, promptA);
  expect(requests).toEqual([rpcA, rpcA, rpcB, rpcB, rpcA]);
});

test('F-01: failed creation stays with its new-chat agent and a late creation keeps the selected chat', async ({ page }) => {
  const agent = 'F-01 new chat specialist';
  const profile = await createSpecialist(page, agent);
  const specialistChat = await createChat(page, 'F-01 specialist origin', profile.id);
  const other = await createChat(page, 'F-01 CEO destination');
  const prompt = 'F-01 original new-chat prompt';
  const requests = recordSends(page);
  const creates: unknown[] = [];
  const held = gate();
  let starts = 0;
  await page.route('**/__agentos/call', async (route) => {
    const { method, params } = route.request().postDataJSON();
    if (method === 'conversations.create') {
      creates.push(params);
      if (creates.length === 1) return route.fulfill({ status: 502, json: { detail: 'F-01 create failed' } });
      await held.pending;
    }
    if (outboundSend(route.request()) && ++starts === 1) {
      return route.fulfill({ status: 502, json: { detail: 'F-01 created chat send failed' } });
    }
    return route.continue();
  });
  await openMockChat(page);
  await selectChat(page, specialistChat);
  await newChat(page);
  await send(page, agent, prompt);
  const retryCreate = retryButton(page, 'F-01 create failed');
  await expect(retryCreate).toBeVisible();
  await selectChat(page, other);
  await expect(retryCreate).toHaveCount(0);
  await newChat(page);
  await expect(page.getByRole('textbox', { name: 'Message Loqman', exact: true })).toBeVisible();
  await expect(retryCreate).toHaveCount(0);
  await selectChat(page, specialistChat);
  await newChat(page);
  await expect(retryCreate).toBeVisible();
  const createParams = { title: prompt, profile_id: profile.id };
  try {
    await retryCreate.click();
    await expect.poll(() => creates).toEqual([createParams, createParams]);
    await selectChat(page, other);
    await page.getByRole('textbox', { name: 'Message Loqman', exact: true }).fill('Existing chat draft');
  } finally {
    held.release();
  }
  await expect.poll(() => requests.length).toBe(1);
  await expect(page.locator('[data-chathead-conversation]')).toHaveText(other.title);
  await expect(page.getByRole('textbox', { name: 'Message Loqman', exact: true })).toHaveValue('Existing chat draft');
  await expect(retryButton(page, 'F-01 created chat send failed')).toHaveCount(0);
  const { conversations } = await call<{ conversations: { id: string; title: string; profile_id: string | null }[] }>(page, 'conversations.list', {});
  const created = conversations.find((c) => c.title === prompt && c.profile_id === profile.id);
  expect(created).toBeDefined();
  const rpc = sendPayload('runs.start_turn', created!.id, prompt);
  expect(requests).toEqual([rpc]);
  await selectChat(page, created!);
  await retryButton(page, 'F-01 created chat send failed').click();
  await expectCompletedSend(page, prompt);
  expect(requests).toEqual([rpc, rpc]);
  expect(creates).toEqual([createParams, createParams]);
});

test('F-01: an edit during creation follows the new conversation and survives its send retry', async ({ page }) => {
  const agent = 'F-01 creation draft specialist';
  const profile = await createSpecialist(page, agent);
  const origin = await createChat(page, 'F-01 creation draft origin', profile.id);
  const prompt = 'F-01 submitted before creation';
  const newer = 'F-01 typed while creation was pending';
  const held = gate();
  const requests = recordSends(page);
  let creations = 0;
  let starts = 0;
  await page.route('**/__agentos/call', async (route) => {
    if (route.request().postDataJSON().method === 'conversations.create') {
      creations++;
      await held.pending;
    }
    if (outboundSend(route.request()) && ++starts === 1) {
      return route.fulfill({ status: 502, json: { detail: 'F-01 first send failed' } });
    }
    return route.continue();
  });
  await openMockChat(page);
  await selectChat(page, origin);
  await newChat(page);
  const box = page.getByRole('textbox', { name: `Message ${agent}`, exact: true });
  try {
    await send(page, agent, prompt);
    await expect.poll(() => creations).toBe(1);
    await box.fill(newer);
  } finally {
    held.release();
  }
  const retry = retryButton(page, 'F-01 first send failed');
  await expect(retry).toBeVisible();
  await expect(box).toHaveValue(newer);
  const current = page.locator('[data-sidebar-thread-row][aria-current="page"]');
  await expect(current).toContainText(prompt);
  const id = await current.getAttribute('data-sidebar-thread-row');
  expect(id).toBeTruthy();
  expect(id).not.toBe(origin.id);
  await retry.click();
  await expectCompletedSend(page, prompt);
  const expected = sendPayload('runs.start_turn', id, prompt);
  expect(requests).toEqual([expected, expected]);
  expect(creations).toBe(1);
  await expect(box).toHaveValue(newer);
  await newChat(page);
  await expect(box).toHaveValue('');
});

test('F-01: retrying Agent A’s failed creation preserves Agent B’s unsent draft', async ({ page }, testInfo) => {
  const agentA = 'F-01 draft owner A';
  const agentB = 'F-01 draft owner B';
  const profileA = await createSpecialist(page, agentA);
  const profileB = await createSpecialist(page, agentB);
  const originA = await createChat(page, 'F-01 owner A origin', profileA.id);
  const originB = await createChat(page, 'F-01 owner B origin', profileB.id);
  const control = 'F-01 unchanged creation control';
  const promptA = 'F-01 owner A submitted prompt';
  const draftB = 'F-01 owner B unsent draft';
  const failure = 'F-01 owner A creation failed';
  const composer = (agent: string) => page.getByRole('textbox', { name: 'Message ' + agent, exact: true });
  const evidence: Record<string, unknown> = {};
  const snapshot = (agent: string) => composer(agent).evaluate((box: HTMLTextAreaElement, values) => {
    const stored = JSON.parse(localStorage.getItem('agentos.rusebot.drafts.v1') ?? '{}') as Record<string, string>;
    return { composer: box.value, stored: Object.fromEntries(Object.entries(stored).filter(([, text]) => values.includes(text))) };
  }, [control, promptA, draftB]);

  const exchanges = new Map<Request, { method: string; params: Record<string, unknown>; status: number | null }>();
  const observed = () => [...exchanges.values()];
  page.on('request', (request) => {
    if (request.method() !== 'POST' || new URL(request.url()).pathname !== '/__agentos/call') return;
    const { method, params } = request.postDataJSON();
    if (method === 'conversations.create' || outboundSend(request)) {
      exchanges.set(request, { method, params, status: null });
    }
  });
  page.on('response', (response) => {
    const exchange = exchanges.get(response.request());
    if (exchange) exchange.status = response.status();
  });
  const held = gate();
  let failed = false;
  await page.route('**/__agentos/call', async (route) => {
    const { method, params } = route.request().postDataJSON();
    if (!failed && method === 'conversations.create' && params.title === promptA && params.profile_id === profileA.id) {
      failed = true;
      return route.fulfill({ status: 502, json: { status: 502, detail: failure } });
    }
    if (method === 'runs.start_turn' && params.prompt === control) await held.pending;
    return route.continue();
  });

  async function createdChat(title: string) {
    const { conversations } = await call<{ conversations: { id: string; title: string; profile_id: string | null }[] }>(
      page, 'conversations.list', {},
    );
    const matches = conversations.filter((chat) => chat.title === title);
    expect(matches).toHaveLength(1);
    expect(matches[0].profile_id).toBe(profileA.id);
    expect(matches[0].id).not.toBe(originA.id);
    expect(matches[0].id).not.toBe(originB.id);
    return matches[0];
  }

  const create = (title: string, status: number) => ({
    method: 'conversations.create', params: { title, profile_id: profileA.id }, status,
  });
  const start = (conversationId: string, prompt: string) => ({
    method: 'runs.start_turn',
    params: {
      conversation_id: conversationId, prompt, engine: 'mock', model_id: 'deepseek-v4.1-flash-free',
      request_key: expect.stringMatching(/^ui-[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/),
    },
    status: 200,
  });

  try {
    await openMockChat(page);
    await selectChat(page, originA);
    await newChat(page);
    try {
      await send(page, agentA, control);
      await expect.poll(() => observed().map(({ method }) => method)).toEqual(['conversations.create', 'runs.start_turn']);
      await expect(page.locator('[data-chathead-conversation]')).toHaveText(control);
      evidence.controlPending = await snapshot(agentA);
    } finally {
      held.release();
    }
    await expectCompletedSend(page, control);
    const controlChat = await createdChat(control);
    const controlCompleted = await snapshot(agentA);
    evidence.controlCompleted = controlCompleted;
    await newChat(page);
    await expect(composer(agentA)).toHaveValue('');
    evidence.controlReopened = await snapshot(agentA);
    expect(Object.values(controlCompleted.stored)).not.toContain(control);

    await send(page, agentA, promptA);
    const retryA = retryButton(page, failure);
    await expect(retryA).toBeVisible();
    await expect(composer(agentA)).toHaveValue(promptA);
    evidence.aAfterFailure = await snapshot(agentA);
    await selectChat(page, originB);
    await newChat(page);
    await expect(retryA).toHaveCount(0);
    evidence.bBeforeTyping = await snapshot(agentB);
    await composer(agentB).fill(draftB);
    await expect(composer(agentB)).toHaveValue(draftB);
    evidence.bAfterTyping = await snapshot(agentB);

    await selectChat(page, originA);
    await newChat(page);
    await expect(retryA).toBeVisible();
    evidence.aBeforeRetry = await snapshot(agentA);
    await retryA.click();
    await expectCompletedSend(page, promptA);
    const retriedChat = await createdChat(promptA);
    await expect(page.locator('[data-sidebar-thread-row][aria-current="page"]')).toHaveAttribute('data-sidebar-thread-row', retriedChat.id);
    evidence.retriedChat = retriedChat;
    const afterRetryA = await snapshot(agentA);
    evidence.aAfterRetry = afterRetryA;

    await selectChat(page, originB);
    await newChat(page);
    const afterRetryB = await snapshot(agentB);
    evidence.bAfterRetry = afterRetryB;

    // Reload restores a saved ID. Re-enter B through the sidebar because the unsaved agent is in memory.
    await page.reload();
    await selectChat(page, retriedChat);
    const afterReloadA = await snapshot(agentA);
    evidence.aAfterReload = afterReloadA;
    await selectChat(page, originB);
    await newChat(page);
    const afterReloadB = await snapshot(agentB);
    evidence.bAfterReload = afterReloadB;

    expect(observed(), 'Only A creates conversations and sends, using the exact retry payload').toEqual([
      create(control, 200), start(controlChat.id, control),
      create(promptA, 502), create(promptA, 200), start(retriedChat.id, promptA),
    ]);
    expect({
      aAfterRetry: afterRetryA.composer,
      persistedUnderA: afterRetryA.stored[retriedChat.id] ?? null,
      bAfterRetry: afterRetryB.composer,
      aAfterReload: afterReloadA.composer,
      persistedUnderAAfterReload: afterReloadA.stored[retriedChat.id] ?? null,
      bAfterReload: afterReloadB.composer,
    }, 'B’s unsent draft stays with B after A’s Retry and reload').toEqual({
      aAfterRetry: '',
      persistedUnderA: null,
      bAfterRetry: draftB,
      aAfterReload: '',
      persistedUnderAAfterReload: null,
      bAfterReload: draftB,
    });
  } finally {
    await testInfo.attach('f01-unsaved-draft-ownership', {
      body: JSON.stringify({ profileA: profileA.id, profileB: profileB.id, rpc: observed(), drafts: evidence }, null, 2),
      contentType: 'application/json',
    });
  }
});

test('F-01: agent and CEO drafts remain separate from saved chats and the ownerless legacy draft', async ({ page }) => {
  const agentA = 'F-01 isolated specialist A';
  const agentB = 'F-01 isolated specialist B';
  const profileA = await createSpecialist(page, agentA);
  const profileB = await createSpecialist(page, agentB);
  const savedA = await createChat(page, 'F-01 isolated saved A', profileA.id);
  const savedB = await createChat(page, 'F-01 isolated saved B', profileB.id);
  const ceo = await createChat(page, 'F-01 isolated CEO');
  const draftKey = 'agentos.rusebot.drafts.v1';
  const original = { '': 'F-01 legacy text without an owner', [savedA.id]: 'F-01 saved text A', [savedB.id]: 'F-01 saved text B' };
  await page.addInitScript(({ draftKey, original }) => {
    if (sessionStorage.getItem('f01-seeded-draft-inventory')) return;
    localStorage.setItem(draftKey, JSON.stringify(original));
    sessionStorage.setItem('f01-seeded-draft-inventory', '1');
  }, { draftKey, original });

  const contexts = [
    { agent: agentA, chat: savedA, draft: 'F-01 unsaved text A', key: 'new:' + profileA.id },
    { agent: agentB, chat: savedB, draft: 'F-01 unsaved text B', key: 'new:' + profileB.id },
    { agent: 'Loqman', chat: ceo, draft: 'F-01 unsaved CEO text', key: 'new:custom' },
  ];
  const readDrafts = () => page.evaluate((key) => JSON.parse(localStorage.getItem(key) ?? '{}'), draftKey);
  const expected = { ...original, ...Object.fromEntries(contexts.map(({ key, draft }) => [key, draft])) };
  await openMockChat(page);
  for (const { agent, chat, draft } of contexts) {
    await selectChat(page, chat);
    const box = page.getByRole('textbox', { name: 'Message ' + agent, exact: true });
    await expect(box).toHaveValue(original[chat.id] ?? '');
    await newChat(page);
    await expect(box).toHaveValue('');
    await box.fill(draft);
  }
  await expect.poll(readDrafts).toEqual(expected);

  await page.reload();
  for (const { agent, chat, draft } of contexts) {
    await selectChat(page, chat);
    const box = page.getByRole('textbox', { name: 'Message ' + agent, exact: true });
    await expect(box).toHaveValue(original[chat.id] ?? '');
    await newChat(page);
    await expect(box).toHaveValue(draft);
    await newChat(page);
    await expect(box).toHaveValue(draft);
  }
  await page.getByRole('textbox', { name: 'Message Loqman', exact: true }).fill('');
  delete expected['new:custom'];
  await expect.poll(readDrafts).toEqual(expected);
});

test('F-01: a newer edit during creation Retry moves with its agent and survives the send', async ({ page }) => {
  const agent = 'F-01 edited retry specialist';
  const profile = await createSpecialist(page, agent);
  const origin = await createChat(page, 'F-01 edited retry origin', profile.id);
  const prompt = 'F-01 original before creation Retry';
  const newer = 'F-01 newer unsent edit during Retry';
  const failure = 'F-01 edited retry create failed';
  const creates: unknown[] = [];
  const requests = recordSends(page);
  const held = gate();
  await page.route('**/__agentos/call', async (route) => {
    const { method, params } = route.request().postDataJSON();
    if (method !== 'conversations.create') return route.continue();
    creates.push(params);
    if (creates.length === 1) return route.fulfill({ status: 502, json: { detail: failure } });
    await held.pending;
    return route.continue();
  });

  await openMockChat(page);
  await selectChat(page, origin);
  await newChat(page);
  const box = page.getByRole('textbox', { name: 'Message ' + agent, exact: true });
  const create = { title: prompt, profile_id: profile.id };
  try {
    await send(page, agent, prompt);
    await retryButton(page, failure).click();
    await expect.poll(() => creates).toEqual([create, create]);
    await box.fill(newer);
    await expect(box).toHaveValue(newer);
  } finally {
    held.release();
  }

  await expectCompletedSend(page, prompt);
  const { conversations } = await call<{ conversations: { id: string; title: string; profile_id: string | null }[] }>(
    page, 'conversations.list', {},
  );
  const created = conversations.filter((chat) => chat.title === prompt);
  expect(created).toHaveLength(1);
  expect(created[0].profile_id).toBe(profile.id);
  expect(requests).toEqual([sendPayload('runs.start_turn', created[0].id, prompt)]);
  expect(creates).toEqual([create, create]);
  await expect(box).toHaveValue(newer);
  await page.reload();
  await expect(box).toHaveValue(newer);
  await newChat(page);
  await expect(box).toHaveValue('');
});

test('F-01: delayed creation Retry preserves a draft already edited in the created chat', async ({ page }) => {
  const agent = 'F-01 delayed destination specialist';
  const profile = await createSpecialist(page, agent);
  const origin = await createChat(page, 'F-01 delayed destination origin', profile.id);
  const ceo = await createChat(page, 'F-01 delayed destination CEO');
  const prompt = 'F-01 delayed creation acknowledgement';
  const newer = 'F-01 edited directly in the created chat';
  const refreshPrompt = 'F-01 refresh chat list through Loqman';
  const failure = 'F-01 delayed creation first failure';
  const requests = recordSends(page);
  const held = gate();
  let requestsToCreate = 0;
  let created: { id: string; title: string; profile_id: string } | undefined;
  await page.route('**/__agentos/call', async (route) => {
    const { method, params } = route.request().postDataJSON();
    if (method !== 'conversations.create' || params.title !== prompt || params.profile_id !== profile.id) {
      return route.continue();
    }
    requestsToCreate++;
    if (requestsToCreate === 1) return route.fulfill({ status: 502, json: { detail: failure } });
    const response = await route.fetch();
    if (response.ok()) created = (await response.json()).result;
    await held.pending;
    return route.fulfill({ response });
  });

  await openMockChat(page);
  await selectChat(page, origin);
  await newChat(page);
  const box = page.getByRole('textbox', { name: 'Message ' + agent, exact: true });
  try {
    await send(page, agent, prompt);
    await retryButton(page, failure).click();
    await expect.poll(() => created?.profile_id).toBe(profile.id);
    await selectChat(page, ceo);
    await send(page, 'Loqman', refreshPrompt);
    await expectCompletedSend(page, refreshPrompt);
    // CEO's answer refreshes the sidebar and exposes the saved chat before its create response is delivered.
    await selectChat(page, created!);
    await box.fill(newer);
    await expect(box).toHaveValue(newer);
  } finally {
    held.release();
  }

  await expectCompletedSend(page, prompt);
  expect(requests).toEqual([
    sendPayload('ceo.ask', ceo.id, refreshPrompt),
    sendPayload('runs.start_turn', created!.id, prompt),
  ]);
  expect(requestsToCreate).toBe(2);
  await expect(box).toHaveValue(newer);
  await newChat(page);
  await expect(box).toHaveValue('');
});

type HandoffRpc = { method: string; params: Record<string, unknown>; status: number | null };

async function prepareCreationHandoff(page: Page, label: string, competingSend: boolean) {
  const agent = 'F-01 handoff specialist ' + label;
  const profile = await createSpecialist(page, agent);
  const origin = await createChat(page, 'F-01 handoff origin ' + label, profile.id);
  const ceo = await createChat(page, 'F-01 handoff CEO ' + label);
  const original = 'F-01 handoff original A ' + label;
  const newer = 'F-01 handoff newer B ' + label;
  const refresh = 'F-01 handoff refresh ' + label;
  const rejected = 'F-01 handoff rejected A before worker acceptance ' + label;
  const sourceKey = 'new:' + profile.id;
  const box = page.getByRole('textbox', { name: 'Message ' + agent, exact: true });
  const creation = gate();
  const newerSend = gate();
  const rpcs = new Map<Request, HandoffRpc>();
  const creating = { created: undefined as { id: string; title: string; profile_id: string } | undefined };
  const newerResponse = { upstreamStatus: null as number | null };
  let acceptOriginal = false;
  const readDrafts = () => page.evaluate(() =>
    JSON.parse(localStorage.getItem('agentos.rusebot.drafts.v1') ?? '{}') as Record<string, string>);
  const actions = () => [...rpcs.values()].filter(({ method }) =>
    method === 'conversations.create' || method === 'runs.start_turn' || method === 'ceo.ask');

  page.on('request', (request) => {
    if (request.method() !== 'POST' || new URL(request.url()).pathname !== '/__agentos/call') return;
    const { method, params } = request.postDataJSON();
    rpcs.set(request, { method, params, status: null });
  });
  page.on('response', (response) => {
    const rpc = rpcs.get(response.request());
    if (rpc) rpc.status = response.status();
  });
  await page.route('**/__agentos/call', async (route) => {
    const { method, params } = route.request().postDataJSON();
    if (method === 'conversations.create' && params.title === original && params.profile_id === profile.id) {
      const response = await route.fetch();
      if (response.ok()) creating.created = (await response.json()).result;
      await creation.pending;
      return route.fulfill({ response });
    }
    if (!acceptOriginal && method === 'runs.start_turn' && params.conversation_id === creating.created?.id && params.prompt === original) {
      return route.fulfill({ status: 502, json: { status: 502, detail: rejected } });
    }
    if (competingSend && method === 'runs.start_turn' && params.conversation_id === creating.created?.id && params.prompt === newer) {
      const response = await route.fetch();
      newerResponse.upstreamStatus = response.status();
      await newerSend.pending;
      return route.fulfill({ response });
    }
    return route.continue();
  });

  const createRpc = {
    method: 'conversations.create', params: { title: original, profile_id: profile.id }, status: 200,
  };
  const refreshRpc = {
    method: 'ceo.ask',
    params: {
      conversation_id: ceo.id, question: refresh, engine: 'mock', model_id: 'deepseek-v4.1-flash-free',
      request_id: expect.stringMatching(/^ceo-[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/),
    },
    status: 200,
  };
  const startRpc = (id: string, prompt: string, status: number | null) => ({
    method: 'runs.start_turn',
    params: {
      conversation_id: id, prompt, engine: 'mock', model_id: 'deepseek-v4.1-flash-free',
      request_key: expect.stringMatching(/^ui-[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/),
    },
    status,
  });

  async function exposeCreatedChat() {
    await openMockChat(page);
    await selectChat(page, origin);
    await newChat(page);
    await send(page, agent, original);
    await expect.poll(() => creating.created?.profile_id).toBe(profile.id);
    await expect.poll(actions).toEqual([{ ...createRpc, status: null }]);

    // Leaving Chat unmounts the busy composer. Loqman's reply refreshes the sidebar.
    const drawer = page.getByRole('button', { name: 'Open chat list' });
    if (await drawer.isVisible()) await drawer.click();
    await page.locator('[data-sidebar-nav="team"]').click();
    await expect(box).toHaveCount(0);
    await selectChat(page, ceo);
    await send(page, 'Loqman', refresh);
    await expectCompletedSend(page, refresh);
    const destination = creating.created!;
    expect(destination.id).not.toBe(origin.id);
    expect(destination.id).not.toBe(ceo.id);
    await selectChat(page, destination);
    return destination;
  }

  return {
    profile, origin, original, newer, rejected, sourceKey, box, creation, newerSend, newerResponse,
    rpcs, actions, readDrafts, createRpc, refreshRpc, startRpc, exposeCreatedChat,
    acceptExplicitRetry: () => { acceptOriginal = true; },
  };
}

test('F-01: delayed creation keeps the submitted draft recoverable when the first send is rejected', async ({ page }, testInfo) => {
  const handoff = await prepareCreationHandoff(page, 'draft recovery', false);
  const evidence: Record<string, unknown> = {};
  try {
    const destination = await handoff.exposeCreatedChat();
    evidence.destination = destination;
    await expect(handoff.box).toHaveValue('');
    await handoff.box.fill(handoff.newer);
    const expectedDrafts = { [handoff.sourceKey]: handoff.original, [destination.id]: handoff.newer };
    await expect.poll(handoff.readDrafts).toEqual(expectedDrafts);
    evidence.beforeCreateResponse = await handoff.readDrafts();

    handoff.creation.release();
    await expect(retryButton(page, handoff.rejected)).toBeVisible();
    await expect(handoff.box).toHaveValue(handoff.newer);
    expect(handoff.actions()).toEqual([
      handoff.createRpc, handoff.refreshRpc, handoff.startRpc(destination.id, handoff.original, 502),
    ]);
    const afterFailure = await handoff.readDrafts();
    evidence.afterRejectedSend = afterFailure;

    await page.reload();
    await selectChat(page, destination);
    const destinationAfterReload = await handoff.box.inputValue();
    await selectChat(page, handoff.origin);
    await newChat(page);
    const sourceAfterReload = await handoff.box.inputValue();
    const storageAfterReload = await handoff.readDrafts();
    evidence.afterReload = { destinationAfterReload, sourceAfterReload, storageAfterReload };
    expect({
      afterFailure, destinationAfterReload, sourceAfterReload, storageAfterReload,
    }, 'Both user texts must remain recoverable after rejection and reload').toEqual({
      afterFailure: expectedDrafts,
      destinationAfterReload: handoff.newer,
      sourceAfterReload: handoff.original,
      storageAfterReload: expectedDrafts,
    });
  } finally {
    handoff.creation.release();
    await testInfo.attach('f01-handoff-draft-recovery', {
      body: JSON.stringify({ ...evidence, rpcs: [...handoff.rpcs.values()] }, null, 2), contentType: 'application/json',
    });
  }
});

test('F-01: delayed creation does not dispatch an older send while the destination send is in flight', async ({ page }, testInfo) => {
  const handoff = await prepareCreationHandoff(page, 'overlap', true);
  const evidence: Record<string, unknown> = {};
  try {
    const destination = await handoff.exposeCreatedChat();
    evidence.destination = destination;
    await send(page, 'F-01 handoff specialist overlap', handoff.newer);
    await expect.poll(() => handoff.newerResponse.upstreamStatus).toBe(200);
    const whileBIsHeld = [
      { ...handoff.createRpc, status: null }, handoff.refreshRpc, handoff.startRpc(destination.id, handoff.newer, null),
    ];
    await expect.poll(handoff.actions).toEqual(whileBIsHeld);
    evidence.beforeCreateResponse = await handoff.readDrafts();

    handoff.creation.release();
    // The saved agent binding is written by the frontend continuation after it consumes the create response.
    await expect.poll(() => page.evaluate((id) => {
      const bindings = JSON.parse(localStorage.getItem('agentos.rusebot.convAgents.v1') ?? '{}');
      return bindings[id] ?? null;
    }, destination.id)).toBe(handoff.profile.id);
    const duringHandoff = handoff.actions().map((rpc) => ({ ...rpc }));
    evidence.whileNewerResponseHeld = { rpcs: duringHandoff, drafts: await handoff.readDrafts() };

    await newChat(page);
    await expect(handoff.box).toHaveValue(handoff.original);
    const sourceRetry = page.locator('[data-chat-retry]');
    await sourceRetry.click();
    await expect(sourceRetry).toBeVisible();
    await selectChat(page, destination);
    handoff.newerSend.release();
    await expectCompletedSend(page, handoff.newer);
    const explicitRetryOffered = await page.locator('[data-chat-retry]').isVisible();
    await selectChat(page, handoff.origin);
    await newChat(page);
    const sourceAfterSend = await handoff.box.inputValue();
    evidence.recovery = { explicitRetryOffered, sourceAfterSend, drafts: await handoff.readDrafts() };

    expect.soft(duringHandoff, 'The older attempt cannot dispatch while B awaits its send response').toEqual([
      handoff.createRpc, handoff.refreshRpc, handoff.startRpc(destination.id, handoff.newer, null),
    ]);
    expect.soft(handoff.actions(), 'Only B is sent unless the user explicitly resubmits A').toEqual([
      handoff.createRpc, handoff.refreshRpc, handoff.startRpc(destination.id, handoff.newer, 200),
    ]);
    expect(explicitRetryOffered || sourceAfterSend === handoff.original, 'The user can explicitly recover A').toBe(true);
    // The held attempt can be retried explicitly, even after the source composer has a newer edit.
    const newerSource = 'F-01 independent source edit after B finished';
    await handoff.box.fill(newerSource);
    handoff.acceptExplicitRetry();
    await sourceRetry.click();
    await selectChat(page, destination);
    await expect(page.getByRole('log').locator('[data-role="user"]').getByText(handoff.original, { exact: true })).toHaveCount(1);
    await expect(page.getByRole('log').locator('[data-role="assistant"]')).toHaveCount(2);
    await expect(page.locator('[data-working]')).toHaveCount(0);
    expect(handoff.actions()).toEqual([
      handoff.createRpc, handoff.refreshRpc, handoff.startRpc(destination.id, handoff.newer, 200),
      handoff.startRpc(destination.id, handoff.original, 200),
    ]);
    await newChat(page);
    await expect(handoff.box).toHaveValue(newerSource);
    expect(await handoff.readDrafts()).toEqual({ [handoff.sourceKey]: newerSource });
  } finally {
    handoff.creation.release();
    handoff.newerSend.release();
    await testInfo.attach('f01-handoff-competing-sends', {
      body: JSON.stringify({ ...evidence, rpcs: [...handoff.rpcs.values()] }, null, 2), contentType: 'application/json',
    });
  }
});

test('F-01: acknowledging a rejected handoff on Retry clears only its retained source draft', async ({ page }) => {
  const handoff = await prepareCreationHandoff(page, 'acknowledged retry', false);
  try {
    const destination = await handoff.exposeCreatedChat();
    await handoff.box.fill(handoff.newer);
    handoff.creation.release();
    const retry = retryButton(page, handoff.rejected);
    await expect(retry).toBeVisible();
    expect(await handoff.readDrafts()).toEqual({ [handoff.sourceKey]: handoff.original, [destination.id]: handoff.newer });
    handoff.acceptExplicitRetry();
    await retry.click();
    await expectCompletedSend(page, handoff.original);
    expect(handoff.actions()).toEqual([
      handoff.createRpc, handoff.refreshRpc, handoff.startRpc(destination.id, handoff.original, 502),
      handoff.startRpc(destination.id, handoff.original, 200),
    ]);
    await expect(handoff.box).toHaveValue(handoff.newer);
    expect(await handoff.readDrafts()).toEqual({ [destination.id]: handoff.newer });
    await newChat(page);
    await expect(handoff.box).toHaveValue('');
  } finally {
    handoff.creation.release();
  }
});

test('F-01: a new CEO prompt remains recoverable after reload before worker acceptance', async ({ page }, testInfo) => {
  const prompt = 'F-01 CEO recovery check: keep this full question across reload, including the unique ending cobalt-lantern-731.';
  const title = prompt.slice(0, 60);
  const sourceKey = 'new:custom';
  const box = page.getByRole('textbox', { name: 'Message Loqman', exact: true });
  const transcript = page.getByRole('log');
  const heldAsk = gate();
  const actions: HandoffRpc[] = [];
  const browserFailures: Array<string | null> = [];
  const evidence: Record<string, unknown> = {};
  let created: { id: string; title: string; profile_id: string | null } | undefined;
  let requestId: string | undefined;

  const snapshot = () => box.evaluate((composer: HTMLTextAreaElement) => ({
    composer: composer.value,
    drafts: JSON.parse(localStorage.getItem('agentos.rusebot.drafts.v1') ?? '{}') as Record<string, string>,
  }));

  page.on('requestfailed', (request) => {
    if (outboundSend(request)?.method === 'ceo.ask') browserFailures.push(request.failure()?.errorText ?? null);
  });
  await page.route('**/__agentos/call', async (route) => {
    const { method, params } = route.request().postDataJSON();
    if (!['conversations.create', 'ceo.ask', 'runs.start_turn', 'conversations.add_message'].includes(method)) {
      return route.continue();
    }
    const action: HandoffRpc = { method, params, status: null };
    actions.push(action);
    if (method === 'ceo.ask') {
      requestId = params.request_id;
      // Hold before forwarding. Releasing this gate only disposes of the browser request.
      await heldAsk.pending;
      await route.abort('aborted').catch(() => undefined);
      return;
    }
    if (method === 'conversations.create') {
      const response = await route.fetch();
      action.status = response.status();
      if (response.ok()) created = (await response.json()).result;
      return route.fulfill({ response });
    }
    return route.continue();
  });

  try {
    await openMockChat(page);
    await newChat(page);
    await expect(box).toHaveValue('');
    expect(await snapshot()).toEqual({ composer: '', drafts: {} });
    expect(prompt.length).toBeGreaterThan(60);
    await box.fill(prompt);
    await expect.poll(snapshot).toEqual({ composer: prompt, drafts: { [sourceKey]: prompt } });
    evidence.beforeSubmit = await snapshot();
    await expect(page.getByRole('button', { name: 'Send', exact: true })).toBeEnabled();
    await box.press('Enter');

    await expect.poll(() => requestId).toMatch(/^ceo-[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/);
    expect(created).toMatchObject({ title, profile_id: null });
    const destination = created!;
    evidence.destination = destination;
    const expected = [
      { method: 'conversations.create', params: { title }, status: 200 },
      {
        method: 'ceo.ask',
        params: {
          conversation_id: destination.id, question: prompt, engine: 'mock',
          model_id: 'deepseek-v4.1-flash-free', request_id: requestId,
        },
        status: null,
      },
    ];
    expect(actions).toEqual(expected);
    await expect(page.locator('[data-chathead-conversation]')).toHaveText(title);
    await expect(transcript.locator('[data-role="user"]').getByText(prompt, { exact: true })).toBeVisible();
    const pending = await snapshot();
    evidence.whileAskHeld = pending;

    await page.reload();
    await selectChat(page, destination);
    const restoredChat = await snapshot();
    evidence.afterReloadSaved = restoredChat;
    await expect(transcript.locator('[data-role="user"], [data-role="assistant"]')).toHaveCount(0);
    await newChat(page);
    const restoredNew = await snapshot();
    evidence.afterReloadNew = restoredNew;

    // The API request context queries the worker independently of the page's held route.
    const history = await call<{ messages: unknown[] }>(page, 'conversations.messages', { conversation_id: destination.id });
    const progress = await page.request.post('/__agentos/call', {
      data: { method: 'ceo.ask_progress', params: { request_id: requestId } },
    });
    evidence.worker = { history: history.messages, progressStatus: progress.status(), progress: await progress.json() };
    expect(history.messages).toEqual([]);
    expect(progress.status()).toBe(404);
    expect(evidence.worker).toMatchObject({ progress: { detail: 'Unknown ask request id.' } });
    expect(actions, 'Reload must not create another conversation or send another message').toEqual(expected);

    expect.soft([pending.drafts[sourceKey], pending.drafts[destination.id]],
      'The full prompt must be persisted under a recoverable owner while the ask is held').toContain(prompt);
    expect([
      { persisted: restoredChat.drafts[destination.id], visible: restoredChat.composer },
      { persisted: restoredNew.drafts[sourceKey], visible: restoredNew.composer },
    ], 'After reload, the full prompt must be persisted and visible in the same CEO composer')
      .toContainEqual({ persisted: prompt, visible: prompt });
  } finally {
    heldAsk.release();
    await testInfo.attach('f01-ceo-pre-acceptance-reload', {
      body: JSON.stringify({ ...evidence, actions, browserFailures }, null, 2), contentType: 'application/json',
    });
  }
});

for (const edit of [false, true]) {
  const scenario = edit ? 'keeps an independent edit through reload' : 'cleans the acknowledged draft';
  test(`F-01: new CEO conversation ${scenario} on explicit Retry`, async ({ page }) => {
    const prompt = 'Review the synthetic cobalt inventory for the November planning meeting, including the final appendix. ' + scenario;
    const newer = 'A separate unsent question about the planning agenda';
    const box = page.getByRole('textbox', { name: 'Message Loqman', exact: true });
    const reject = gate();
    const acknowledge = gate();
    const actions: unknown[] = [];
    const drafts = () => page.evaluate(() =>
      JSON.parse(localStorage.getItem('agentos.rusebot.drafts.v1') ?? '{}') as Record<string, string>);
    let asks = 0;
    let accepted = false;
    await page.route('**/__agentos/call', async (route) => {
      const { method, params } = route.request().postDataJSON();
      if (['ceo.ask', 'conversations.create', 'runs.start_turn', 'conversations.add_message'].includes(method)) {
        actions.push({ method, params });
      }
      if (method !== 'ceo.ask') return route.continue();
      const attempt = ++asks;
      if (attempt === 1) await reject.pending;
      if (attempt <= 2) return route.fulfill({ status: 502, json: { detail: 'Test rejection ' + attempt } });
      const response = await route.fetch();
      accepted = response.ok();
      await acknowledge.pending;
      return route.fulfill({ response });
    });

    try {
      await openMockChat(page);
      await newChat(page);
      await send(page, 'Loqman', prompt);
      await expect.poll(() => asks).toBe(1);
      const id = await page.locator('[data-sidebar-thread-row][aria-current="page"]').getAttribute('data-sidebar-thread-row');
      expect(id).toBeTruthy();
      const destination = { id: id!, title: prompt.slice(0, 60) };
      await expect(box).toHaveValue('');
      await expect.poll(drafts).toEqual({ [destination.id]: prompt });
      if (edit) await box.fill(newer);
      reject.release();
      await expect(retryButton(page, 'Test rejection 1')).toBeVisible();
      await expect(box).toHaveValue(edit ? newer : prompt);
      if (edit) {
        await page.reload();
        await selectChat(page, destination);
        await expect(box).toHaveValue(newer);
        await retryButton(page, prompt).click();
      } else {
        await retryButton(page, 'Test rejection 1').click();
      }
      await expect(retryButton(page, 'Test rejection 2')).toBeVisible();
      await expect(box).toHaveValue(edit ? newer : prompt);
      await retryButton(page, 'Test rejection 2').click();
      await expect.poll(() => accepted).toBe(true);
      await newChat(page);
      await expect(box).toHaveValue('');
      await selectChat(page, destination);
      await expect(box).toHaveValue(edit ? newer : '');
      if (edit) await box.fill(newer + ' (revised)');
      expect(Object.values(await drafts())).toContain(prompt);
      acknowledge.release();
      await expectCompletedSend(page, prompt);
      await expect.poll(drafts).toEqual(edit ? { [destination.id]: newer + ' (revised)' } : {});
      await page.reload();
      await selectChat(page, destination);
      await expect(box).toHaveValue(edit ? newer + ' (revised)' : '');
      await expect(page.locator('[data-chat-retry]')).toHaveCount(0);
      const ask = {
        method: 'ceo.ask', params: {
          conversation_id: destination.id, engine: 'mock', model_id: 'deepseek-v4.1-flash-free',
          question: prompt, request_id: expect.stringMatching(/^ceo-[0-9a-f-]{36}$/),
        },
      };
      expect(actions).toEqual([{ method: 'conversations.create', params: { title: destination.title } }, ask, ask, ask]);
    } finally {
      reject.release();
      acknowledge.release();
    }
  });
}

test('F-01: a new send cannot erase a failed deferred Retry from the same composer', async ({ page }, testInfo) => {
  const handoff = await prepareCreationHandoff(page, 'deferred conflict', true);
  const promptC = 'F-01 independent prompt C for the draft conflict';
  const failureC = 'F-01 injected C creation rejection';
  const retryA = retryButton(page, handoff.rejected);
  const retryC = retryButton(page, failureC);
  const rejectA = gate();
  const rejectC = gate();
  const evidence: Record<string, unknown> = {};
  let interceptedC = false;

  try {
    const destination = await handoff.exposeCreatedChat();
    evidence.destination = destination;
    await send(page, 'F-01 handoff specialist deferred conflict', handoff.newer);
    await expect.poll(() => handoff.newerResponse.upstreamStatus).toBe(200);
    handoff.creation.release();
    await newChat(page);
    const deferred = retryButton(page, 'Another message is being sent in the created chat');
    await expect(deferred).toBeVisible();
    await expect(handoff.box).toHaveValue(handoff.original);
    const beforeRetry = [handoff.createRpc, handoff.refreshRpc, handoff.startRpc(destination.id, handoff.newer, 200)];
    expect(handoff.actions()).toEqual([beforeRetry[0], beforeRetry[1], { ...beforeRetry[2], status: null }]);
    await selectChat(page, destination);
    handoff.newerSend.release();
    await expectCompletedSend(page, handoff.newer);
    await selectChat(page, handoff.origin);
    await newChat(page);

    await page.route('**/__agentos/call', async (route) => {
      const { method, params } = route.request().postDataJSON();
      if (method === 'runs.start_turn' && params.prompt === handoff.original) {
        await rejectA.pending;
        return route.fulfill({ status: 502, json: { detail: handoff.rejected } });
      }
      if (method === 'conversations.create' && params.title === promptC) {
        interceptedC = true;
        await rejectC.pending;
        return route.fulfill({ status: 502, json: { detail: failureC } });
      }
      return route.fallback();
    });

    await handoff.box.fill(promptC);
    await expect.poll(handoff.readDrafts).toEqual({ [handoff.sourceKey]: promptC });
    await deferred.click();
    const pendingA = [...beforeRetry, handoff.startRpc(destination.id, handoff.original, null)];
    await expect.poll(handoff.actions).toEqual(pendingA);
    const sendButton = page.getByRole('button', { name: 'Send', exact: true });
    const enabledBeforeC = await sendButton.isEnabled();
    await handoff.box.press('Enter');
    if (enabledBeforeC) {
      // A composer submission stays busy while C's intercepted request is held.
      // A synchronous send guard can instead return without dispatching a request.
      await expect.poll(async () => interceptedC || await sendButton.isEnabled()).toBe(true);
    }
    const overlaps = interceptedC;
    const creationC = { method: 'conversations.create', params: { title: promptC, profile_id: handoff.profile.id } };
    await expect.poll(handoff.actions).toEqual(overlaps ? [...pendingA, { ...creationC, status: null }] : pendingA);
    evidence.beforeFailures = { enabledBeforeC, overlaps, drafts: await handoff.readDrafts() };

    rejectA.release();
    await expect(retryA).toBeVisible();
    await expect(handoff.box).toHaveValue(promptC);
    evidence.afterRejectionA = { retry: await retryA.isVisible(), drafts: await handoff.readDrafts() };
    if (overlaps) {
      rejectC.release();
      await expect(retryC).toBeVisible();
    }
    await expect(handoff.box).toHaveValue(promptC);
    const sourceRecovery = await retryA.isVisible();
    await selectChat(page, destination);
    const destinationRecovery = await retryA.isVisible() || await handoff.box.inputValue() === handoff.original;
    evidence.afterFailures = { sourceRecovery, destinationRecovery, drafts: await handoff.readDrafts() };

    await page.reload();
    await selectChat(page, destination);
    const savedComposer = await handoff.box.inputValue();
    await selectChat(page, handoff.origin);
    await newChat(page);
    await expect(handoff.box).toHaveValue(promptC);
    const restored = await handoff.readDrafts();
    expect(restored[handoff.sourceKey]).toBe(promptC);
    evidence.afterReload = { drafts: restored, savedComposer, sourceComposer: await handoff.box.inputValue(), sourceRetryA: await retryA.isVisible() };

    const history = await call<{ messages: Array<{ role: string; content: string }> }>(
      page, 'conversations.messages', { conversation_id: destination.id },
    );
    const { conversations } = await call<{ conversations: Array<{ id: string; title: string; profile_id: string | null }> }>(
      page, 'conversations.list', {},
    );
    evidence.worker = { history: history.messages, createdC: conversations.filter((chat) => chat.title === promptC) };
    expect(history.messages.filter((message) => message.role === 'user').map((message) => message.content)).toEqual([handoff.newer]);
    expect(conversations.filter((chat) => chat.title === promptC)).toEqual([]);
    expect(conversations.find((chat) => chat.id === destination.id)?.profile_id).toBe(handoff.profile.id);
    expect(handoff.actions()).toEqual([
      ...beforeRetry, handoff.startRpc(destination.id, handoff.original, 502),
      ...(overlaps ? [{ ...creationC, status: 502 }] : []),
    ]);
    expect(
      sourceRecovery || destinationRecovery || savedComposer === handoff.original,
      'Rejecting C must leave A recoverable while preserving C in the specialist New Chat composer',
    ).toBe(true);
  } finally {
    handoff.creation.release();
    handoff.newerSend.release();
    rejectA.release();
    rejectC.release();
    await testInfo.attach('f01-deferred-retry-conflict', {
      body: JSON.stringify({ ...evidence, actions: handoff.actions() }, null, 2), contentType: 'application/json',
    });
  }
});

test('F-01: sequential failures leave deferred A and new C independently retryable in their own chats', async ({ page }) => {
  const label = 'sequential recovery';
  const handoff = await prepareCreationHandoff(page, label, true);
  const promptC = 'F-01 independent C after deferred rejection';
  const savedEdit = 'F-01 separate saved chat draft';
  const failC = 'F-01 rejected independent creation';
  const firstRetry = gate();
  const sendButton = page.getByRole('button', { name: 'Send', exact: true });
  try {
    const destination = await handoff.exposeCreatedChat();
    await send(page, 'F-01 handoff specialist ' + label, handoff.newer);
    await expect.poll(() => handoff.newerResponse.upstreamStatus).toBe(200);
    handoff.creation.release();
    await newChat(page);
    const deferred = retryButton(page, 'Another message is being sent in the created chat');
    await expect(deferred).toBeVisible();
    await selectChat(page, destination);
    handoff.newerSend.release();
    await expectCompletedSend(page, handoff.newer);
    await newChat(page);

    let rejectCreateC = true;
    await page.route('**/__agentos/call', async (route) => {
      const { method, params } = route.request().postDataJSON();
      if (method === 'runs.start_turn' && params.prompt === handoff.original) await firstRetry.pending;
      if (rejectCreateC && method === 'conversations.create' && params.title === promptC) {
        rejectCreateC = false;
        return route.fulfill({ status: 502, json: { detail: failC } });
      }
      return route.fallback();
    });
    const initial = [handoff.createRpc, handoff.refreshRpc, handoff.startRpc(destination.id, handoff.newer, 200)];
    const a = (status: number | null) => handoff.startRpc(destination.id, handoff.original, status);
    const createC = (status: number) => ({
      method: 'conversations.create', params: { title: promptC, profile_id: handoff.profile.id }, status,
    });
    await deferred.click();
    await expect.poll(handoff.actions).toEqual([...initial, a(null)]);
    await handoff.box.fill(promptC);
    await expect(sendButton).toBeDisabled();
    await handoff.box.press('Enter');
    await selectChat(page, destination);
    await handoff.box.fill(savedEdit);
    await expect(sendButton).toBeDisabled();
    await selectChat(page, handoff.origin);
    await handoff.box.fill('F-01 unrelated composer remains available');
    await expect(sendButton).toBeEnabled();
    await handoff.box.fill('');
    await newChat(page);
    await expect(handoff.box).toHaveValue(promptC);
    await expect(sendButton).toBeDisabled();
    const edits = { [handoff.sourceKey]: promptC, [destination.id]: savedEdit };
    await expect.poll(handoff.readDrafts).toEqual(edits);
    expect(handoff.actions()).toEqual([...initial, a(null)]);

    firstRetry.release();
    const retryA = retryButton(page, handoff.rejected);
    await expect(retryA).toBeVisible();
    await expect(sendButton).toBeEnabled();
    await sendButton.click();
    const retryC = retryButton(page, failC);
    await expect(retryC).toBeVisible();
    await expect(handoff.box).toHaveValue(promptC);
    const failed = [...initial, a(502), createC(502)];
    expect(handoff.actions()).toEqual(failed);
    await selectChat(page, destination);
    await expect(handoff.box).toHaveValue(savedEdit);
    await expect(sendButton).toBeEnabled();
    handoff.acceptExplicitRetry();
    await retryA.click();
    await expect(page.getByRole('log').locator('[data-role="assistant"]')).toHaveCount(2);
    await expect(page.locator('[data-working]')).toHaveCount(0);
    await expect(retryA).toHaveCount(0);
    await expect(sendButton).toBeEnabled();
    expect(await handoff.readDrafts()).toEqual(edits);

    await newChat(page);
    await expect(handoff.box).toHaveValue(promptC);
    await expect(sendButton).toBeEnabled();
    await retryC.click();
    await expectCompletedSend(page, promptC);
    const { conversations } = await call<{ conversations: Array<{ id: string; title: string; profile_id: string }> }>(
      page, 'conversations.list', {},
    );
    const created = conversations.filter((c) => c.title === promptC);
    expect(created).toHaveLength(1);
    expect(created[0].profile_id).toBe(handoff.profile.id);
    expect(created[0].id).not.toBe(destination.id);
    const expected = [...failed, a(200), createC(200), handoff.startRpc(created[0].id, promptC, 200)];
    expect(handoff.actions()).toEqual(expected);
    await page.reload();
    await selectChat(page, destination);
    await expect(page.getByRole('log').locator('[data-role="user"]').getByText(handoff.original, { exact: true })).toHaveCount(1);
    await expect(handoff.box).toHaveValue(savedEdit);
    expect(await handoff.readDrafts()).toEqual({ [destination.id]: savedEdit });
    expect(handoff.actions()).toEqual(expected);
  } finally {
    handoff.creation.release();
    handoff.newerSend.release();
    firstRetry.release();
  }
});

const reloadRecoveryCases = [
  {
    title: 'F-01: a failed specialist prompt remains recoverable after replacing its draft and reloading',
    method: 'runs.start_turn',
    agent: 'F-01 reload recovery specialist',
    prompt: 'F-01 original specialist prompt A, synthetic reference copper atlas 401.',
    newer: 'F-01 unsent specialist draft B, synthetic reference amber atlas 402.',
  },
  {
    title: 'F-01: a pending prompt in a saved CEO chat remains recoverable after editing and reloading',
    method: 'ceo.ask',
    agent: 'Loqman',
    prompt: 'F-01 original CEO prompt A for an existing chat, with distinctive text beyond the first sixty characters: violet compass 731.',
    newer: 'F-01 unsent CEO draft B, synthetic reference silver compass 732.',
  },
] as const;

for (const scenario of reloadRecoveryCases) {
  test(scenario.title, async ({ page }, testInfo) => {
    const { agent, prompt, newer, method } = scenario;
    const ceo = method === 'ceo.ask';
    const agentId = ceo ? 'custom' : (await createSpecialist(page, agent)).id;
    const destination = await createChat(page, `F-01 saved recovery chat for ${agent}`, ceo ? undefined : agentId);
    const box = page.getByRole('textbox', { name: `Message ${agent}`, exact: true });
    const userMessages = page.getByRole('log').locator('[data-role="user"]');
    const retryBanners = page.getByRole('alert').filter({ has: page.locator('[data-chat-retry]') });
    const failure = 'F-01 synthetic rejection before worker acceptance';
    const ask = gate();
    const actions: HandoffRpc[] = [];
    const progressRequests: unknown[] = [];
    const evidence: Record<string, unknown> = { agent, agentId, destination, prompt, newer };
    const snapshot = async () => ({
      composer: await box.inputValue(),
      drafts: await page.evaluate(() =>
        JSON.parse(localStorage.getItem('agentos.rusebot.drafts.v1') ?? '{}') as Record<string, string>),
      userMessages: await userMessages.allTextContents(),
      retryBanners: await retryBanners.locator('p').allTextContents(),
    });

    await page.route('**/__agentos/call', async (route) => {
      const { method: rpc, params } = route.request().postDataJSON();
      if (rpc === 'ceo.ask_progress') progressRequests.push(params);
      if (!['conversations.create', 'ceo.ask', 'runs.start_turn', 'conversations.add_message'].includes(rpc)) {
        return route.continue();
      }
      const action: HandoffRpc = { method: rpc, params, status: null };
      actions.push(action);
      if (rpc === 'ceo.ask') {
        await ask.pending;
        // Discard the intercepted request on teardown. It never reaches the worker.
        await route.abort('aborted').catch(() => undefined);
        return;
      }
      await route.fulfill({ status: 502, json: { detail: failure } });
      action.status = 502;
    });

    try {
      await openMockChat(page);
      await selectChat(page, destination);
      await expect(box).toHaveValue('');
      if (ceo) expect(prompt.length).toBeGreaterThan(60);
      const before = await call<{ conversations: Array<{ id: string; profile_id: string | null }> }>(
        page, 'conversations.list', {},
      );
      expect(before.conversations.find(({ id }) => id === destination.id)?.profile_id).toBe(ceo ? null : agentId);
      await send(page, agent, prompt);

      const shared = { conversation_id: destination.id, engine: 'mock', model_id: 'deepseek-v4.1-flash-free' };
      const expected = [{
        method,
        params: ceo
          ? { ...shared, question: prompt, request_id: expect.stringMatching(/^ceo-[0-9a-f-]{36}$/) }
          : { ...shared, prompt, request_key: expect.stringMatching(/^ui-[0-9a-f-]{36}$/) },
        status: ceo ? null : 502,
      }];
      await expect.poll(() => actions).toEqual(expected);
      if (ceo) {
        await expect(userMessages.getByText(prompt, { exact: true })).toBeVisible();
        await expect(box).toHaveValue('');
      } else {
        await expect(retryButton(page, failure)).toBeVisible();
        await expect(box).toHaveValue(prompt);
      }
      evidence.beforeEdit = await snapshot();

      await box.fill(newer);
      await expect(box).toHaveValue(newer);
      await expect.poll(async () => (await snapshot()).drafts[destination.id]).toBe(newer);
      if (ceo) await expect(userMessages.getByText(prompt, { exact: true })).toBeVisible();
      else await expect(retryButton(page, failure)).toBeVisible();
      evidence.beforeReload = await snapshot();

      const historyLoaded = page.waitForResponse((response) => {
        const request = response.request();
        if (request.method() !== 'POST' || new URL(request.url()).pathname !== '/__agentos/call') return false;
        const { method: rpc, params } = request.postDataJSON();
        return rpc === 'conversations.messages' && params.conversation_id === destination.id;
      });
      const [historyResponse] = await Promise.all([historyLoaded, page.reload()]);
      await selectChat(page, destination);
      expect(historyResponse.status()).toBe(200);
      await expect(box).toHaveValue(newer);
      const restored = await snapshot();
      evidence.afterReload = restored;

      const history = await call<{ messages: unknown[] }>(page, 'conversations.messages', { conversation_id: destination.id });
      const after = await call<{ conversations: Array<{ id: string }> }>(page, 'conversations.list', {});
      const worker: Record<string, unknown> = {
        history: history.messages, before: before.conversations, after: after.conversations,
      };
      evidence.worker = worker;
      expect(history.messages).toEqual([]);
      expect(after.conversations.map(({ id }) => id).sort()).toEqual(before.conversations.map(({ id }) => id).sort());
      if (ceo) {
        const progress = await page.request.post('/__agentos/call', {
          data: { method: 'ceo.ask_progress', params: { request_id: actions[0].params.request_id } },
        });
        const lookup = { status: progress.status(), body: await progress.json() };
        worker.lookup = lookup;
        expect(lookup).toMatchObject({ status: 404, body: { detail: 'Unknown ask request id.' } });
      } else {
        const { runs } = await call<{ runs: unknown[] }>(page, 'runs.list', { conversation_id: destination.id });
        worker.runs = runs;
        expect(runs).toEqual([]);
      }
      expect(actions, 'Reload must not dispatch another send or create a conversation').toEqual(expected);
      expect(restored.drafts[destination.id]).toBe(newer);
      await expect(retryButton(page, prompt),
        'The full original prompt must be user-recoverable after reload while the newer draft stays in the composer',
      ).toBeVisible();
    } finally {
      ask.release();
      await testInfo.attach('f01-saved-chat-reload-recovery', {
        body: JSON.stringify({ ...evidence, actions, progressRequests, forwardedSends: 0 }, null, 2),
        contentType: 'application/json',
      });
    }
  });
}

for (const scenario of reloadRecoveryCases) {
  test(`F-01: ${scenario.method} acknowledges only the explicitly recovered submission`, async ({ page }) => {
    const { agent, method, prompt, newer } = scenario;
    const profile = method === 'ceo.ask' ? undefined : await createSpecialist(page, agent);
    const chat = await createChat(page, 'F-01 independent acknowledgements', profile?.id);
    const other = `Another unresolved ${method} message`;
    const box = page.getByRole('textbox', { name: 'Message ' + agent, exact: true });
    const mutations: Array<{ method: string; params: Record<string, unknown> }> = [];
    const accepted = gate();
    const deliver = gate();
    await page.route('**/__agentos/call', async (route) => {
      const rpc = route.request().postDataJSON();
      if (!['conversations.create', 'ceo.ask', 'runs.start_turn', 'conversations.add_message'].includes(rpc.method)) {
        return route.continue();
      }
      mutations.push(rpc);
      if (mutations.length <= 3) return route.fulfill({ status: 502, json: { detail: 'Injected failure ' + mutations.length } });
      const response = await route.fetch();
      expect(response.status()).toBe(200);
      accepted.release();
      await deliver.pending;
      return route.fulfill({ response });
    });
    try {
      await openMockChat(page);
      await selectChat(page, chat);
      await send(page, agent, prompt);
      await expect(retryButton(page, 'Injected failure 1')).toBeVisible();
      await send(page, agent, other);
      await expect(retryButton(page, 'Injected failure 2')).toBeVisible();
      await box.fill(newer);
      await page.reload();
      await selectChat(page, chat);
      await expect(box).toHaveValue(newer);
      await expect(page.getByRole('button', { name: 'Send', exact: true })).toBeEnabled();
      const offered = page.locator('[data-chat-retry]');
      await expect(offered).toBeVisible();
      const first = await retryButton(page, prompt).isVisible();
      const recovering = first ? prompt : other;
      const remaining = first ? other : prompt;
      const expectedRpc = (text: string) => ({
        method, params: {
          conversation_id: chat.id, engine: 'mock', model_id: 'deepseek-v4.1-flash-free',
          ...(method === 'ceo.ask' ? { question: text, request_id: expect.stringMatching(/^ceo-/) }
            : { prompt: text, request_key: expect.stringMatching(/^ui-/) }),
        },
      });
      expect(mutations).toEqual([expectedRpc(prompt), expectedRpc(other)]);
      const originalRpc = mutations[first ? 0 : 1];
      await retryButton(page, recovering).click();
      await expect(retryButton(page, 'Injected failure 3')).toBeVisible();
      await retryButton(page, recovering).click();
      await accepted.pending;
      await box.fill(newer + ' revised');
      deliver.release();
      await expectCompletedSend(page, recovering);
      await expect(box).toHaveValue(newer + ' revised');
      await page.reload();
      await selectChat(page, chat);
      await expect(box).toHaveValue(newer + ' revised');
      await expect(retryButton(page, remaining)).toBeVisible();
      await page.getByRole('alert').filter({ hasText: remaining }).getByRole('button', { name: 'Dismiss' }).click();
      await page.reload();
      await selectChat(page, chat);
      await expect(box).toHaveValue(newer + ' revised');
      await expect(offered).toHaveCount(0);
      expect(mutations).toEqual([expectedRpc(prompt), expectedRpc(other), originalRpc, originalRpc]);
    } finally {
      deliver.release();
    }
  });
}

test('F-01: a saved CEO reply acknowledged by history clears recovery after reload', async ({ page }) => {
  const { prompt, newer } = reloadRecoveryCases[1];
  const chat = await createChat(page, 'F-01 accepted CEO question');
  const accepted = gate();
  const discardResponse = gate();
  const actions: unknown[] = [];
  await page.route('**/__agentos/call', async (route) => {
    const rpc = route.request().postDataJSON();
    if (!['ceo.ask', 'conversations.create', 'runs.start_turn'].includes(rpc.method)) return route.continue();
    actions.push(rpc);
    const response = await route.fetch();
    expect(response.status()).toBe(200);
    accepted.release();
    await discardResponse.pending;
    await route.abort('aborted').catch(() => undefined);
  });
  try {
    await openMockChat(page);
    await selectChat(page, chat);
    await send(page, 'Loqman', prompt);
    await accepted.pending;
    const box = page.getByRole('textbox', { name: 'Message Loqman', exact: true });
    await box.fill(newer);
    await page.reload();
    await selectChat(page, chat);
    await expectCompletedSend(page, prompt);
    await expect(box).toHaveValue(newer);
    await expect(page.locator('[data-chat-retry]')).toHaveCount(0);
    expect(await page.evaluate(() => Object.keys(localStorage).filter(key => key.startsWith('agentos.rusebot.submission.v1.')))).toEqual([]);
    expect(actions).toEqual([{ method: 'ceo.ask', params: {
      conversation_id: chat.id, question: prompt, request_id: expect.stringMatching(/^ceo-/),
      engine: 'mock', model_id: 'deepseek-v4.1-flash-free',
    } }]);
  } finally {
    discardResponse.release();
  }
});

for (const method of ['ceo.ask', 'runs.start_turn'] as const) {
  test(`F-01: ${method} recovers an edited New Chat submission after reload during creation`, async ({ page }, testInfo) => {
    const isCeo = method === 'ceo.ask';
    const agent = isCeo ? 'Loqman' : 'F-01 creation recovery specialist';
    const agentId = isCeo ? 'custom' : (await createSpecialist(page, agent)).id;
    const origin = await createChat(page, 'F-01 creation recovery origin', isCeo ? undefined : agentId);
    const prompt = `F-01 unsaved ${method} original prompt A. Preserve this exact ending beyond the chat title, blue atlas 891.`;
    const draft = `F-01 independent New Chat draft B for ${method}`;
    const box = page.getByRole('textbox', { name: 'Message ' + agent, exact: true });
    const held = gate();
    const intercepted = gate();
    const submitted = gate();
    const acknowledge = gate();
    const creates: Array<{ method: string; params: Record<string, unknown> }> = [];
    const sends = recordSends(page);
    const evidence: Record<string, unknown> = {};
    let acceptedCreates = 0;
    let acceptedSends = 0;
    const storage = () => page.evaluate(() => ({
      drafts: JSON.parse(localStorage.getItem('agentos.rusebot.drafts.v1') ?? '{}'),
      submissions: Object.keys(localStorage).filter(key => key.startsWith('agentos.rusebot.submission.v1.'))
        .map(key => JSON.parse(localStorage.getItem(key)!)),
    }));
    await page.route('**/__agentos/call', async (route) => {
      const rpc = route.request().postDataJSON();
      if (rpc.method === 'conversations.create') {
        creates.push(rpc);
        if (creates.length === 1) {
          intercepted.release();
          await held.pending;
          return route.abort('aborted').catch(() => undefined);
        }
        const response = await route.fetch();
        if (response.ok()) acceptedCreates++;
        return route.fulfill({ response });
      }
      if (!outboundSend(route.request())) return route.continue();
      const response = await route.fetch();
      if (response.ok()) acceptedSends++;
      submitted.release();
      await acknowledge.pending;
      return route.fulfill({ response });
    });
    const createRpc = {
      method: 'conversations.create',
      params: { title: prompt.slice(0, 60), ...(isCeo ? {} : { profile_id: agentId }) },
    };
    try {
      await openMockChat(page);
      await selectChat(page, origin);
      const before = await call<{ conversations: Array<{ id: string }> }>(page, 'conversations.list', {});
      await newChat(page);
      await send(page, agent, prompt);
      await intercepted.pending;
      evidence.beforeEdit = await storage();
      await box.fill(draft);
      await expect(box).toHaveValue(draft);
      evidence.beforeReload = await storage();
      expect(creates).toEqual([createRpc]);
      expect(sends).toEqual([]);

      await page.reload();
      await selectChat(page, origin);
      await newChat(page);
      await expect(box).toHaveValue(draft);
      const worker = await call<{ conversations: Array<{ id: string }> }>(page, 'conversations.list', {});
      evidence.afterReload = { storage: await storage(), composer: await box.inputValue(), worker,
        recoveryVisible: await retryButton(page, prompt).isVisible() };
      expect(worker.conversations.map(c => c.id).sort()).toEqual(before.conversations.map(c => c.id).sort());
      expect(acceptedCreates).toBe(0);
      expect(acceptedSends).toBe(0);
      expect(creates).toEqual([createRpc]);
      expect(sends).toEqual([]);
      await expect(retryButton(page, prompt), 'The original submitted text must be recoverable in its agent’s New Chat after reload').toBeVisible();

      await retryButton(page, prompt).click();
      await submitted.pending;
      const { conversations } = await call<{ conversations: Array<{ id: string; title: string; profile_id: string | null }> }>(
        page, 'conversations.list', {},
      );
      const saved = conversations.filter(c => c.title === createRpc.params.title);
      expect(saved).toHaveLength(1);
      expect(saved[0].profile_id).toBe(isCeo ? null : agentId);
      expect(sends).toEqual([sendPayload(method, saved[0].id, prompt)]);
      expect(creates).toEqual([createRpc, createRpc]);
      expect(acceptedCreates).toBe(1);
      expect(acceptedSends).toBe(1);
      await expect(box).toHaveValue(draft);
      evidence.afterHandoff = await storage();
      expect((await storage()).submissions).toEqual([expect.objectContaining({ cid: saved[0].id, agentId, prompt })]);
      acknowledge.release();
      await expectCompletedSend(page, prompt);
      await expect(box).toHaveValue(draft);
      expect((await storage()).submissions).toEqual([]);
      expect(creates).toEqual([createRpc, createRpc]);
      expect(sends).toEqual([sendPayload(method, saved[0].id, prompt)]);
    } finally {
      held.release();
      acknowledge.release();
      await testInfo.attach('f01-new-chat-creation-recovery', {
        body: JSON.stringify({ agentId, prompt, draft, creates, sends, acceptedCreates, acceptedSends, ...evidence }, null, 2),
        contentType: 'application/json',
      });
    }
  });
}

test('F-01: a lost creation acknowledgement warns without sending or creating again', async ({ page }) => {
  const origin = await createChat(page, 'F-01 ambiguous creation origin');
  const prompt = 'F-01 a worker-created conversation with its acknowledgement held';
  const draft = 'F-01 independent draft after an ambiguous creation';
  const box = page.getByRole('textbox', { name: 'Message Loqman', exact: true });
  const accepted = gate();
  const held = gate();
  const creates: unknown[] = [];
  const sends = recordSends(page);
  let created!: { id: string; title: string };
  await page.route('**/__agentos/call', async route => {
    const { method, params } = route.request().postDataJSON();
    if (method !== 'conversations.create') return route.continue();
    creates.push(params);
    const response = await route.fetch();
    expect(response.status()).toBe(200);
    created = (await response.json()).result;
    accepted.release();
    await held.pending;
    return route.abort('aborted').catch(() => undefined);
  });
  try {
    await openMockChat(page);
    await selectChat(page, origin);
    await newChat(page);
    await send(page, 'Loqman', prompt);
    await accepted.pending;
    await box.fill(draft);
    await page.reload();
    await selectChat(page, created);
    await expect(retryButton(page, prompt)).toHaveCount(0);
    await newChat(page);
    await expect(box).toHaveValue(draft);
    const banner = page.getByRole('alert').filter({ hasText: prompt });
    await expect(banner).toContainText('Conversation creation was not confirmed. Check your chat list before retrying');
    await expect(retryButton(page, prompt)).toBeVisible();
    await banner.getByRole('button', { name: 'Dismiss' }).click();
    await page.reload();
    await selectChat(page, origin);
    await newChat(page);
    await expect(box).toHaveValue(draft);
    await expect(retryButton(page, prompt)).toHaveCount(0);
    expect(creates).toEqual([{ title: prompt.slice(0, 60) }]);
    expect(sends).toEqual([]);
  } finally {
    held.release();
  }
});

test('F-01: a New Chat storage failure prevents an unprotected create', async ({ page }) => {
  const prompt = 'F-01 text awaiting durable New Chat storage';
  const creates: unknown[] = [];
  const sends = recordSends(page);
  page.on('request', request => {
    if (new URL(request.url()).pathname !== '/__agentos/call' || request.method() !== 'POST') return;
    const { method, params } = request.postDataJSON();
    if (method === 'conversations.create') creates.push(params);
  });
  await page.addInitScript(() => {
    const write = Storage.prototype.setItem;
    Storage.prototype.setItem = function (key, value) {
      if (key.startsWith('agentos.rusebot.submission.v1.')) throw new DOMException('Test storage full', 'QuotaExceededError');
      return write.call(this, key, value);
    };
  });
  await openMockChat(page);
  await newChat(page);
  await send(page, 'Loqman', prompt);
  await expect(retryButton(page, 'Could not save this submission for recovery')).toBeVisible();
  await expect(page.getByRole('textbox', { name: 'Message Loqman', exact: true })).toHaveValue(prompt);
  expect(creates).toEqual([]);
  expect(sends).toEqual([]);
});
