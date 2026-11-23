import { expect, test as base, type Locator, type Page, type Request } from '@playwright/test';

type Model = { id: string; name: string };
type Conversation = { id: string; title: string; profile_id: string | null };
type Rpc = { method: string; params: Record<string, unknown> };
type Execution = Rpc & { status: number | null };
type CatalogResponse = { models: Model[]; error?: string };

const rpcUrl = '**/__agentos/call';
const mockModels = [
  { id: 'deepseek-v4.1-flash-free', name: 'DeepSeek V4.1 Flash Free (Mock)' },
  { id: 'deepseek-ai/deepseek-v4.1-flash', name: 'DeepSeek V4.1 Flash (Mock)' },
] as const;
const providers = [
  { engine_id: 'opencode', title: 'Fixture OpenCode', model: { id: 'fixture-open', name: 'OpenCode model' } },
  { engine_id: 'codex', title: 'Fixture Codex', model: { id: 'fixture-codex', name: 'Codex model' } },
  { engine_id: 'claude-code', title: 'Fixture Claude', model: { id: 'fixture-claude', name: 'Claude model' } },
] as const;
const [openCode, codex, claude] = providers;

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}

function browserRpc(request: Request): Rpc | undefined {
  if (request.method() !== 'POST' || new URL(request.url()).pathname !== '/__agentos/call') return;
  return request.postDataJSON() as Rpc;
}

async function rpc<T>(page: Page, method: string, params: Record<string, unknown> = {}): Promise<T> {
  const response = await page.request.post('/__agentos/call', { data: { method, params } });
  expect(response.ok(), 'Mock worker fixture ' + method).toBe(true);
  return (await response.json()).result as T;
}

async function savedChat(page: Page, recipient: 'CEO' | 'specialist') {
  const profile = recipient === 'specialist'
    ? await rpc<{ id: string }>(page, 'profiles.create', {
      name: 'F-02.2 specialist', role_type: 'custom', instructions: 'Answer briefly.', default_model_id: mockModels[0].id,
    })
    : null;
  return rpc<Conversation>(page, 'conversations.create', {
    title: 'F-02.2 saved ' + recipient,
    ...(profile ? { profile_id: profile.id } : {}),
  });
}

function installFixture(page: Page) {
  const executions: Execution[] = [];
  const traced = new Map<Request, Execution>();
  const snapshots: Array<{ stage: string; engine: string | null; model: string | null; pill: string | null }> = [];
  const catalogGates = new Map<string, ReturnType<typeof catalogGate>>();
  function catalogGate() {
    return { requested: deferred<void>(), reply: deferred<CatalogResponse>() };
  }
  let lostAck: {
    forward: boolean;
    accepted: ReturnType<typeof deferred<number | null>>;
    reply: ReturnType<typeof deferred<void>>;
  } | undefined;
  page.on('response', (response) => {
    const execution = traced.get(response.request());
    if (execution) execution.status = response.status();
  });

  const ready = page.route(rpcUrl, async (route) => {
    const rpc = browserRpc(route.request());
    if (!rpc) return route.continue();
    const reply = (result: unknown) => route.fulfill({ json: { result } });
    if (rpc.method === 'engines.capabilities') {
      return reply({
        mock: { conversation_runs: true },
        ...Object.fromEntries(providers.map((provider) => [provider.engine_id, { conversation_runs: true }])),
      });
    }
    if (rpc.method === 'engines.status') {
      return reply({ engines: providers.map(({ engine_id, title }) => ({
        engine_id, title, display_name: title, install_state: 'installed', auth_state: 'authenticated', state: 'ready',
      })) });
    }
    if (rpc.method === 'engines.models' || rpc.method === 'models.free') {
      const engine = rpc.method === 'models.free' ? 'mock' : String(rpc.params.engine_id);
      const gate = catalogGates.get(engine);
      if (gate) {
        catalogGates.delete(engine);
        gate.requested.resolve();
        const { models, error } = await gate.reply.promise;
        return error ? route.fulfill({ status: 503, json: { detail: error } }) : reply({ models });
      }
      if (engine === 'mock') return route.continue();
      return reply({ models: providers.filter((provider) => provider.engine_id === engine).map(({ model }) => model) });
    }
    if (rpc.method === 'ceo.ask' || rpc.method === 'runs.start_turn') {
      const execution = { ...rpc, status: null };
      traced.set(route.request(), execution);
      executions.push(execution);
      // Record non-Mock payloads at the browser boundary and keep them off the wire.
      if (rpc.params.engine !== 'mock') {
        return route.fulfill({ status: 422, json: { detail: 'Fixture intercepted non-Mock execution.' } });
      }
      if (lostAck && rpc.method === 'runs.start_turn') {
        const held = lostAck;
        lostAck = undefined;
        const upstream = held.forward ? await route.fetch() : null;
        held.accepted.resolve(upstream?.status() ?? null);
        await held.reply.promise;
        return route.fulfill({ status: 502, json: { detail: 'Fixture lost the acknowledgement.' } });
      }
    }
    if (['engines.check', 'engines.test', 'engines.open_setup', 'preferences.set_default'].includes(rpc.method)) {
      return route.fulfill({ status: 422, json: { detail: 'Fixture blocked provider side effects.' } });
    }
    return route.continue();
  });

  return {
    ready,
    executions,
    snapshots,
    holdCatalog(engine: string) {
      const gate = catalogGate();
      catalogGates.set(engine, gate);
      return { requested: gate.requested.promise, release: gate.reply.resolve };
    },
    holdAcknowledgement(forward = true) {
      const accepted = deferred<number | null>();
      const reply = deferred<void>();
      lostAck = { forward, accepted, reply };
      return { accepted: accepted.promise, release: reply.resolve };
    },
    async snapshot(stage: string) {
      snapshots.push({
        stage,
        engine: await page.locator('#rb-engine').getAttribute('data-value'),
        model: await page.locator('#rb-model').getAttribute('data-value'),
        pill: await page.locator('[data-chathead-pill]').evaluate(
          (pill) => pill.querySelector('[title]')?.getAttribute('title') ?? null,
        ),
      });
    },
  };
}

const test = base.extend<{ worker: ReturnType<typeof installFixture> }>({
  worker: async ({ page }, provideWorker, testInfo) => {
    const worker = installFixture(page);
    await worker.ready;
    try {
      await provideWorker(worker);
    } finally {
      await testInfo.attach('execution-evidence', {
        body: JSON.stringify({ selections: worker.snapshots, executions: worker.executions }, null, 2),
        contentType: 'application/json',
      });
    }
  },
});

const add = (page: Page) => page.getByRole('button', { name: 'Add', exact: true });
const composer = (page: Page) => page.locator('[data-tour="composer"]').getByRole('textbox');
const enginePicker = (page: Page) => page.locator('#rb-engine');
const modelPicker = (page: Page) => page.locator('#rb-model');
const send = (page: Page) => page.getByRole('button', { name: 'Send', exact: true });
const retry = (page: Page) => page.locator('[data-chat-retry]');

async function pick(trigger: Locator, value: string) {
  const page = trigger.page();
  await trigger.click();
  await page.getByRole('option').and(page.locator('[data-value="' + value + '"]')).click();
  await expect(page.getByRole('listbox')).toHaveCount(0);
}

async function selection(page: Page, engine: string, model: string) {
  await expect(enginePicker(page)).toHaveAttribute('data-value', engine);
  await expect(modelPicker(page)).toHaveAttribute('data-value', model);
}

async function openSaved(page: Page, chat: Conversation) {
  await page.addInitScript((id) => {
    localStorage.setItem('agentos.rusebot.selected.v1', JSON.stringify(id));
  }, chat.id);
  await page.goto('/?ui=rusebot');
  await expect(page.locator('[data-chathead-conversation]')).toHaveText(chat.title);
  await add(page).click();
  await selection(page, 'mock', mockModels[0].id);
}

async function tryEnter(page: Page) {
  const observed = page.waitForRequest((request) => {
    const call = browserRpc(request);
    return call?.method === 'ceo.ask' || call?.method === 'runs.start_turn';
  }, { timeout: 800 }).catch((error: unknown) => {
    if (error instanceof Error && error.name === 'TimeoutError') return null;
    throw error;
  });
  await composer(page).press('Enter');
  return observed;
}

function expectedCall(chat: Conversation, engine: string, model: string, prompt: string, status = 200): Execution {
  const ceo = chat.profile_id === null;
  return {
    method: ceo ? 'ceo.ask' : 'runs.start_turn',
    params: {
      conversation_id: chat.id, engine, model_id: model,
      ...(ceo ? { question: prompt, request_id: expect.any(String) } : { prompt, request_key: expect.any(String) }),
    },
    status,
  };
}

const sidebarChat = (page: Page, chat: Conversation) => page.locator(`[data-sidebar-thread-row="${chat.id}"]`);
const chatChoice = (page: Page, engine: string, model: string) =>
  page.locator('[data-chathead-pill]').locator(`[title="${engine} · ${model}"]`);

async function visitChat(page: Page, chat: Conversation) {
  await sidebarChat(page, chat).click();
  await expect(page.locator('[data-chathead-conversation]')).toHaveText(chat.title);
}

async function chooseChatPair(page: Page, engine: string, model: string) {
  await add(page).click();
  if (await enginePicker(page).getAttribute('data-value') !== engine) await pick(enginePicker(page), engine);
  if (await modelPicker(page).getAttribute('data-value') !== model) await pick(modelPicker(page), model);
  await selection(page, engine, model);
  await add(page).click();
}

test.describe('AUD03-F3: model selection after an Engine switch', () => {
  const preferredKey = 'agentos.rusebot.preferredModels.v1';
  const alternate = { id: 'fixture-open-alternate', name: 'OpenCode alternate' };

  async function openCodeCatalog(page: Page, models: Model[]) {
    await page.route(rpcUrl, route => {
      const call = browserRpc(route.request());
      return call?.method === 'engines.models' && call.params.engine_id === openCode.engine_id
        ? route.fulfill({ json: { result: { models } } })
        : route.fallback();
    });
  }

  async function switchEngine(page: Page, engine: string) {
    await add(page).click();
    await pick(enginePicker(page), engine);
  }

  test('a fresh chat ignores legacy model memory while provider diagnostics still use it', async ({ page, worker }) => {
    const legacy = JSON.stringify({ [openCode.engine_id]: alternate.id });
    await page.addInitScript(({ key, value }) => localStorage.setItem(key, value), { key: preferredKey, value: legacy });
    await openCodeCatalog(page, [openCode.model, alternate]);
    const diagnosticCalls: Rpc[] = [];
    await page.route(rpcUrl, route => {
      const call = browserRpc(route.request());
      if (call?.method !== 'engines.test') return route.fallback();
      diagnosticCalls.push(call);
      return route.fulfill({ json: { result: {
        ...call.params, ok: true, duration_ms: 10, reply_preview: 'Fixture provider test succeeded.',
      } } });
    });
    await page.goto('/');
    await expect(composer(page)).toHaveAccessibleName('Message Loqman');
    await expect(chatChoice(page, 'mock', mockModels[0].id)).toBeVisible();
    await switchEngine(page, openCode.engine_id);
    await expect.soft(modelPicker(page)).toHaveAttribute('data-value', openCode.model.id, { timeout: 1500 });
    await add(page).click();
    const prompt = 'Use the first catalog model in this fresh chat.';
    await composer(page).fill(prompt);
    await send(page).click();
    await expect.poll(() => worker.executions.length).toBe(1);
    const { conversations } = await rpc<{ conversations: Conversation[] }>(page, 'conversations.list');
    const created = conversations.find(chat => chat.title === prompt);
    expect(created).toMatchObject({ profile_id: null });
    await expect(page.locator('[data-chat-retry]')).toBeVisible();
    expect.soft(worker.executions).toEqual([
      expectedCall(created!, openCode.engine_id, openCode.model.id, prompt, 422),
    ]);
    const ownerKey = 'agentos.rusebot.chatExecution.v1.' + created!.id;
    expect.soft(await page.evaluate(key => JSON.parse(localStorage.getItem(key) ?? 'null'), ownerKey))
      .toMatchObject({ engine: openCode.engine_id, model: openCode.model.id });

    await add(page).click();
    await page.getByRole('menuitem', { name: 'Manage providers…' }).click();
    const card = page.locator(`[data-engine-card="${openCode.engine_id}"]`);
    await card.locator('[data-engine-primary]').click();
    await expect(card.locator('[data-engine-test-result=ok]')).toContainText('Fixture provider test succeeded.');
    await expect(card.locator(`[data-engine-model="${alternate.id}"]`)).toHaveAttribute('aria-pressed', 'true');
    expect(diagnosticCalls).toEqual([{ method: 'engines.test', params: { engine_id: openCode.engine_id, model_id: alternate.id } }]);
    expect(await page.evaluate(key => localStorage.getItem(key), preferredKey)).toBe(legacy);
  });

  test('same-Agent and different-Agent chats restore their own remembered models after reload', async ({ page, worker }) => {
    const a = await savedChat(page, 'specialist');
    const b = await rpc<Conversation>(page, 'conversations.create', { title: 'F3 same Agent', profile_id: a.profile_id });
    const c = await savedChat(page, 'specialist');
    const cModel = { id: 'fixture-open-third', name: 'OpenCode third' };
    await openCodeCatalog(page, [openCode.model, alternate, cModel]);
    await page.goto('/');
    const choices = [[a, alternate], [b, openCode.model], [c, cModel]] as const;
    for (const [chat, model] of choices) {
      await visitChat(page, chat);
      await chooseChatPair(page, openCode.engine_id, model.id);
      await chooseChatPair(page, codex.engine_id, codex.model.id);
    }
    await page.reload();
    const expected: Execution[] = [];
    for (const [chat, model] of choices) {
      await visitChat(page, chat);
      await expect(chatChoice(page, codex.engine_id, codex.model.id)).toBeVisible();
      await switchEngine(page, openCode.engine_id);
      await selection(page, openCode.engine_id, model.id);
      await add(page).click();
      const prompt = 'Use this chat’s remembered model: ' + model.id;
      await composer(page).fill(prompt);
      await send(page).click();
      expected.push(expectedCall(chat, openCode.engine_id, model.id, prompt, 422));
      await expect.poll(() => worker.executions).toEqual(expected);
    }
  });

  test('returning to an Engine without owner memory uses the matching Agent or workspace default', async ({ page, worker }) => {
    const workspaceModel = { id: 'fixture-open-workspace', name: 'OpenCode workspace default' };
    const profile = await rpc<{ id: string }>(page, 'profiles.create', {
      name: 'F3 configured Agent', role_type: 'custom', instructions: 'Answer briefly.',
      engine: openCode.engine_id, default_model_id: alternate.id,
    });
    const agentChat = await rpc<Conversation>(page, 'conversations.create', { title: 'F3 Agent default', profile_id: profile.id });
    const ceoChat = await savedChat(page, 'CEO');
    await openCodeCatalog(page, [openCode.model, alternate, workspaceModel]);
    await page.route(rpcUrl, route => browserRpc(route.request())?.method === 'preferences.get'
      ? route.fulfill({ json: { result: { engine: openCode.engine_id, model_id: workspaceModel.id } } })
      : route.fallback());
    await page.goto('/');
    const expected: Execution[] = [];
    for (const [chat, model] of [[agentChat, alternate], [ceoChat, workspaceModel]] as const) {
      await visitChat(page, chat);
      await expect(chatChoice(page, openCode.engine_id, model.id)).toBeVisible();
      await chooseChatPair(page, codex.engine_id, codex.model.id);
      await switchEngine(page, openCode.engine_id);
      await expect.soft(modelPicker(page)).toHaveAttribute('data-value', model.id, { timeout: 1500 });
      await add(page).click();
      const prompt = 'Use the applicable default: ' + model.id;
      await composer(page).fill(prompt);
      await send(page).click();
      expected.push(expectedCall(chat, openCode.engine_id, model.id, prompt, 422));
      await expect.poll(() => worker.executions.length).toBe(expected.length);
      await expect(page.locator('[data-chat-retry]')).toBeVisible();
    }
    expect(worker.executions).toEqual(expected);
  });

  test('late catalogs retain their owner and Engine even when different catalogs share a model ID', async ({ page, worker }) => {
    const a = await savedChat(page, 'CEO');
    const b = await savedChat(page, 'specialist');
    const shared = { id: 'fixture-shared-id', name: 'Shared display name' };
    const modelsA = [openCode.model, shared];
    const modelsB = [codex.model, shared];
    const heldA = worker.holdCatalog(openCode.engine_id);
    const heldB = worker.holdCatalog(codex.engine_id);
    try {
      await page.goto('/');
      await visitChat(page, a);
      await switchEngine(page, openCode.engine_id);
      await heldA.requested;
      await add(page).click();
      await visitChat(page, b);
      await expect(chatChoice(page, 'mock', mockModels[0].id)).toBeVisible();
      await switchEngine(page, codex.engine_id);
      await heldB.requested;
      await pick(enginePicker(page), claude.engine_id);
      await selection(page, claude.engine_id, claude.model.id);
      await add(page).click();

      const replyA = page.waitForResponse(response => browserRpc(response.request())?.params.engine_id === openCode.engine_id);
      heldA.release({ models: modelsA });
      await (await replyA).finished();
      await expect(chatChoice(page, claude.engine_id, claude.model.id)).toBeVisible();
      await visitChat(page, a);
      await expect(chatChoice(page, openCode.engine_id, openCode.model.id)).toBeVisible();
      await chooseChatPair(page, openCode.engine_id, shared.id);

      const replyB = page.waitForResponse(response => browserRpc(response.request())?.params.engine_id === codex.engine_id);
      heldB.release({ models: modelsB });
      await (await replyB).finished();
      await expect(chatChoice(page, openCode.engine_id, shared.id)).toBeVisible();
      await visitChat(page, b);
      await expect(chatChoice(page, claude.engine_id, claude.model.id)).toBeVisible();
      await switchEngine(page, codex.engine_id);
      await selection(page, codex.engine_id, codex.model.id);
      await pick(modelPicker(page), shared.id);
      await add(page).click();
      await composer(page).fill('Use the shared ID from the selected Engine.');
      await send(page).click();
      await expect.poll(() => worker.executions).toEqual([
        expectedCall(b, codex.engine_id, shared.id, 'Use the shared ID from the selected Engine.', 422),
      ]);
      await visitChat(page, a);
      await expect(chatChoice(page, openCode.engine_id, shared.id)).toBeVisible();
    } finally {
      heldA.release({ models: modelsA });
      heldB.release({ models: modelsB });
    }
  });
});

test.describe('AUD03-F1: active unsaved draft ownership', () => {
  const selectionKey = 'agentos.rusebot.selected.v1';
  const specialist = (page: Page, name: string) => rpc<{ id: string }>(page, 'profiles.create', {
    name, role_type: 'custom', instructions: 'Answer briefly.', default_model_id: mockModels[0].id,
  });

  async function newChat(page: Page) {
    await page.locator('[data-sidebar-new]').click();
    await page.locator('[data-new-chat]').click();
    await expect(page.locator('[data-chathead-conversation]')).toHaveText('New chat');
  }

  function observeCreations(page: Page) {
    const calls: Rpc[] = [];
    page.on('request', request => {
      const call = browserRpc(request);
      if (call?.method === 'conversations.create') calls.push(call);
    });
    return calls;
  }

  test('A/B/E: restores the last specialist draft and pair while keeping other drafts and saved chats independent', async ({ page, worker }) => {
    const a = await specialist(page, 'AUD03-F1 Specialist A');
    const b = await specialist(page, 'AUD03-F1 Specialist B');
    const savedA = await rpc<Conversation>(page, 'conversations.create', { title: 'AUD03-F1 Saved A', profile_id: a.id });
    const savedB = await rpc<Conversation>(page, 'conversations.create', { title: 'AUD03-F1 Saved B', profile_id: b.id });
    const ceo = await rpc<Conversation>(page, 'conversations.create', { title: 'AUD03-F1 Saved CEO' });
    const creates = observeCreations(page);

    await page.goto('/');
    await expect(composer(page)).toHaveAccessibleName('Message Loqman');
    await composer(page).fill('Independent CEO draft');
    await page.reload();
    await expect(composer(page)).toHaveAccessibleName('Message Loqman');
    await expect(composer(page)).toHaveValue('Independent CEO draft');

    await visitChat(page, savedA);
    await newChat(page);
    await chooseChatPair(page, codex.engine_id, codex.model.id);
    await composer(page).fill('Unsent draft A');
    await visitChat(page, savedB);
    await newChat(page);
    await chooseChatPair(page, 'mock', mockModels[1].id);
    await composer(page).fill('Unsent draft B');
    await page.reload();
    await expect(composer(page)).toHaveAccessibleName('Message AUD03-F1 Specialist B');
    await expect(composer(page)).toHaveValue('Unsent draft B');
    await expect(chatChoice(page, 'mock', mockModels[1].id)).toBeVisible();

    await visitChat(page, savedA);
    await newChat(page);
    await expect(composer(page)).toHaveValue('Unsent draft A');
    await page.reload();
    await expect(composer(page)).toHaveAccessibleName('Message AUD03-F1 Specialist A');
    await expect(composer(page)).toHaveValue('Unsent draft A');
    await expect(chatChoice(page, codex.engine_id, codex.model.id)).toBeVisible();

    await visitChat(page, ceo);
    await newChat(page);
    await expect(composer(page)).toHaveValue('Independent CEO draft');
    await visitChat(page, savedB);
    await page.reload();
    await expect(sidebarChat(page, savedB)).toHaveAttribute('aria-current', 'page');
    await expect(composer(page)).toHaveValue('');
    await expect(chatChoice(page, 'mock', mockModels[0].id)).toBeVisible();
    expect(creates).toEqual([]);
    expect(worker.executions).toEqual([]);
  });

  test('C: restores a failed first send and retries the original specialist pair and request key once', async ({ page, worker }) => {
    const a = await specialist(page, 'AUD03-F1 recovering specialist');
    const origin = await rpc<Conversation>(page, 'conversations.create', { title: 'AUD03-F1 recovery origin', profile_id: a.id });
    const creates = observeCreations(page);
    let shouldReject = true;
    await page.route(rpcUrl, route => {
      if (browserRpc(route.request())?.method !== 'conversations.create' || !shouldReject) return route.fallback();
      return route.fulfill({ status: 503, json: { detail: 'Fixture rejected the first creation.' } });
    });
    await page.goto('/');
    await visitChat(page, origin);
    await newChat(page);
    await chooseChatPair(page, 'mock', mockModels[1].id);
    const prompt = 'Retry the original specialist draft.';
    await composer(page).fill(prompt);
    await send(page).click();
    await expect(retry(page)).toBeVisible();
    const record = await page.evaluate(() => {
      const key = Object.keys(localStorage).find(key => key.startsWith('agentos.rusebot.submission.v1.'));
      return key ? JSON.parse(localStorage.getItem(key) ?? 'null') : null;
    });
    expect(record).toMatchObject({
      cid: null, agentId: a.id, prompt, requestId: expect.stringMatching(/^ui-/),
      execution: { engine: 'mock', model: mockModels[1].id },
    });
    await page.reload();
    await expect(composer(page)).toHaveAccessibleName('Message AUD03-F1 recovering specialist');
    await expect(composer(page)).toHaveValue(prompt);
    await expect(chatChoice(page, 'mock', mockModels[1].id)).toBeVisible();
    await expect(retry(page)).toBeVisible();
    expect(await recoveryRecord(page, record.requestId)).toEqual(record);
    const create = { method: 'conversations.create', params: { title: prompt, profile_id: a.id } };
    expect(creates).toEqual([create]);
    expect(worker.executions).toEqual([]);

    shouldReject = false;
    await retry(page).click();
    await expect.poll(() => worker.executions.length).toBe(1);
    const conversationId = String(worker.executions[0].params.conversation_id);
    const created = (await rpc<{ conversations: Conversation[] }>(page, 'conversations.list')).conversations
      .find(chat => chat.id === conversationId);
    expect(created).toMatchObject({ id: conversationId, profile_id: a.id, title: prompt });
    await expect.poll(() => worker.executions).toEqual([{
      ...expectedCall(created!, 'mock', mockModels[1].id, prompt),
      params: { conversation_id: conversationId, engine: 'mock', model_id: mockModels[1].id, prompt, request_key: record.requestId },
    }]);
    await expect(sidebarChat(page, created!)).toHaveAttribute('aria-current', 'page');
    await page.reload();
    await expect(sidebarChat(page, created!)).toHaveAttribute('aria-current', 'page');
    await expect(chatChoice(page, 'mock', mockModels[1].id)).toBeVisible();
    expect(creates).toEqual([create, create]);
    expect(worker.executions).toHaveLength(1);
  });

  test('D: keeps an unsaved deleted Agent draft unavailable without treating a failed discovery as deletion', async ({ page, worker }) => {
    const a = await specialist(page, 'AUD03-F1 deleted specialist');
    const origin = await rpc<Conversation>(page, 'conversations.create', { title: 'AUD03-F1 deleted origin', profile_id: a.id });
    const creates = observeCreations(page);
    let rejectProfiles = false;
    await page.route(rpcUrl, route => rejectProfiles && browserRpc(route.request())?.method === 'profiles.list'
      ? route.fulfill({ status: 503, json: { detail: 'Fixture could not read Agents.' } }) : route.fallback());
    await page.goto('/');
    await visitChat(page, origin);
    await newChat(page);
    await chooseChatPair(page, 'mock', mockModels[1].id);
    await composer(page).fill('Keep the deleted Agent draft for recovery.');
    await rpc(page, 'profiles.delete', { profile_id: a.id });

    rejectProfiles = true;
    await page.reload();
    const error = page.locator('[data-load-error]');
    await expect(error).toContainText('Fixture could not read Agents.');
    await expect(composer(page)).toHaveAccessibleName('Message Specialist');
    await expect(composer(page)).toHaveValue('Keep the deleted Agent draft for recovery.');
    await expect(send(page)).toBeDisabled();
    await composer(page).press('Enter');
    rejectProfiles = false;
    await error.getByRole('button', { name: 'Retry' }).click();
    await expect(error).toHaveCount(0);
    await expect(composer(page)).toHaveAccessibleName('Recipient unavailable');
    await expect(page.getByText('This Agent is missing from the Agent list.', { exact: true })).toBeVisible();
    await expect(composer(page)).toHaveValue('Keep the deleted Agent draft for recovery.');
    await expect(chatChoice(page, 'mock', mockModels[1].id)).toBeVisible();
    await expect(send(page)).toBeDisabled();
    await composer(page).press('Enter');
    expect(creates).toEqual([]);
    expect(worker.executions).toEqual([]);
  });

  test('F: accepts legacy saved navigation and safely handles malformed records and denied storage', async ({ page, worker }) => {
    const a = await specialist(page, 'AUD03-F1 storage specialist');
    const origin = await rpc<Conversation>(page, 'conversations.create', { title: 'AUD03-F1 storage origin', profile_id: a.id });
    const creates = observeCreations(page);
    await page.goto('/');
    await page.evaluate(({ key, id }) => localStorage.setItem(key, JSON.stringify(id)), { key: selectionKey, id: origin.id });
    await page.reload();
    await expect(sidebarChat(page, origin)).toHaveAttribute('aria-current', 'page');

    await page.evaluate(key => {
      const setItem = Storage.prototype.setItem;
      Storage.prototype.setItem = function(name, value) {
        if (name === key) throw new DOMException('Fixture denied navigation storage.', 'QuotaExceededError');
        setItem.call(this, name, value);
      };
    }, selectionKey);
    await newChat(page);
    await composer(page).fill('Keep this draft even if navigation storage is denied.');
    await expect(composer(page)).toHaveAccessibleName('Message AUD03-F1 storage specialist');
    await page.reload();
    await expect(sidebarChat(page, origin)).toHaveAttribute('aria-current', 'page');
    await newChat(page);
    await expect(composer(page)).toHaveValue('Keep this draft even if navigation storage is denied.');

    for (const malformed of ['{bad json', JSON.stringify(`new:${a.id}`), '{"draftOwner":"new:"}', '{"draftOwner":"wrong:custom"}']) {
      await page.evaluate(({ key, value }) => localStorage.setItem(key, value), { key: selectionKey, value: malformed });
      await page.reload();
      await expect(composer(page)).toHaveAccessibleName('Message Loqman');
      await expect(composer(page)).toHaveValue('');
    }

    await visitChat(page, origin);
    await newChat(page);
    await expect(composer(page)).toHaveValue('Keep this draft even if navigation storage is denied.');
    await page.addInitScript(key => {
      const getItem = Storage.prototype.getItem;
      Storage.prototype.getItem = function(name) {
        if (name === key) throw new DOMException('Fixture denied navigation storage.', 'SecurityError');
        return getItem.call(this, name);
      };
    }, selectionKey);
    await page.reload();
    await expect(composer(page)).toHaveAccessibleName('Message Loqman');
    await expect(composer(page)).toHaveValue('');
    expect(creates).toEqual([]);
    expect(worker.executions).toEqual([]);
  });
});

test('AUD-03: same-Agent, different-Agent and CEO choices survive switching, workspaces and reload', async ({ page, worker }) => {
  const a = await savedChat(page, 'specialist');
  const b = await rpc<Conversation>(page, 'conversations.create', { title: 'Same Agent, second chat', profile_id: a.profile_id });
  const c = await savedChat(page, 'specialist');
  const ceoA = await savedChat(page, 'CEO');
  const ceoB = await rpc<Conversation>(page, 'conversations.create', { title: 'Another CEO chat' });
  await openSaved(page, a);
  // Change a model using the existing keyboard-operated dropdown.
  await modelPicker(page).press('Enter');
  await page.getByRole('option', { name: mockModels[1].name, exact: true }).press('Enter');
  await selection(page, 'mock', mockModels[1].id);
  await add(page).click();
  await visitChat(page, b);
  await expect(chatChoice(page, 'mock', mockModels[0].id)).toBeVisible();
  await chooseChatPair(page, codex.engine_id, codex.model.id);
  await visitChat(page, c);
  await expect(chatChoice(page, 'mock', mockModels[0].id)).toBeVisible();
  await chooseChatPair(page, claude.engine_id, claude.model.id);
  await visitChat(page, ceoA);
  await chooseChatPair(page, openCode.engine_id, openCode.model.id);
  await visitChat(page, ceoB);
  await expect(chatChoice(page, 'mock', mockModels[0].id)).toBeVisible();
  await page.locator('[data-sidebar-nav=team]').click();
  await page.locator('[data-sidebar-nav=contracts]').click();
  await page.locator('[data-sidebar-nav=results]').click();
  await visitChat(page, a);
  await page.reload();
  for (const [chat, engine, model] of [
    [a, 'mock', mockModels[1].id],
    [b, codex.engine_id, codex.model.id],
    [c, claude.engine_id, claude.model.id],
    [ceoA, openCode.engine_id, openCode.model.id],
    [ceoB, 'mock', mockModels[0].id],
    [a, 'mock', mockModels[1].id],
  ] as const) {
    await visitChat(page, chat);
    await expect(chatChoice(page, engine, model)).toBeVisible();
  }
  await chooseChatPair(page, codex.engine_id, codex.model.id);
  await add(page).click();
  await pick(enginePicker(page), 'mock');
  await selection(page, 'mock', mockModels[1].id);
  await add(page).click();
  const prompt = 'Use the model owned by this conversation.';
  await composer(page).fill(prompt);
  await send(page).click();
  await expect.poll(() => worker.executions).toEqual([expectedCall(a, 'mock', mockModels[1].id, prompt)]);
});

test('AUD-03: delayed catalogs cannot change another chat or replace newer intent with identical model IDs', async ({ page, worker }) => {
  const a = await savedChat(page, 'CEO');
  const b = await savedChat(page, 'specialist');
  const heldX = worker.holdCatalog(openCode.engine_id);
  const heldY = worker.holdCatalog(codex.engine_id);
  const shared = { id: 'same-model-id', name: 'Same display name' };
  try {
    await openSaved(page, a);
    await pick(enginePicker(page), openCode.engine_id);
    await heldX.requested;
    await visitChat(page, b);
    await expect(chatChoice(page, 'mock', mockModels[0].id)).toBeVisible();
    await visitChat(page, a);
    if (!(await enginePicker(page).isVisible())) await add(page).click();
    await selection(page, openCode.engine_id, '');
    await pick(enginePicker(page), codex.engine_id);
    await heldY.requested;
    heldY.release({ models: [shared] });
    await selection(page, codex.engine_id, shared.id);
    await visitChat(page, b);
    const oldReply = page.waitForResponse(response => browserRpc(response.request())?.params.engine_id === openCode.engine_id);
    heldX.release({ models: [shared] });
    await (await oldReply).finished();
    await expect(chatChoice(page, 'mock', mockModels[0].id)).toBeVisible();
    await chooseChatPair(page, openCode.engine_id, shared.id);
    await visitChat(page, a);
    await expect(chatChoice(page, codex.engine_id, shared.id)).toBeVisible();
    await composer(page).fill('The model ID exists in both catalogs.');
    await send(page).click();
    await expect.poll(() => worker.executions).toEqual([
      expectedCall(a, codex.engine_id, shared.id, 'The model ID exists in both catalogs.', 422),
    ]);
  } finally {
    heldX.release({ models: [shared] });
    heldY.release({ models: [shared] });
  }
});

test('AUD-03: a failed draft creation preserves its choice and a late success transfers it to the worker ID', async ({ page, worker }) => {
  const a = await savedChat(page, 'specialist');
  const b = await savedChat(page, 'CEO');
  const arrived = deferred<void>();
  const release = deferred<void>();
  const created = deferred<Conversation>();
  let creates = 0;
  await page.route(rpcUrl, async route => {
    if (browserRpc(route.request())?.method !== 'conversations.create') return route.fallback();
    if (++creates === 1) return route.fulfill({ status: 503, json: { detail: 'Fixture rejected creation.' } });
    const response = await route.fetch();
    expect(response.ok()).toBe(true);
    created.resolve((await response.json()).result as Conversation);
    arrived.resolve();
    await release.promise;
    return route.fulfill({ response });
  });
  try {
    await openSaved(page, a);
    await add(page).click();
    await chooseChatPair(page, 'mock', mockModels[1].id);
    await page.locator('[data-sidebar-new]').click();
    await page.locator('[data-new-chat]').click();
    await expect(chatChoice(page, 'mock', mockModels[0].id)).toBeVisible();
    await chooseChatPair(page, 'mock', mockModels[1].id);
    const prompt = 'Keep the exact draft pair through creation.';
    await composer(page).fill(prompt);
    await send(page).click();
    await expect(retry(page)).toBeVisible();
    await expect(chatChoice(page, 'mock', mockModels[1].id)).toBeVisible();
    await expect(composer(page)).toHaveValue(prompt);
    await retry(page).click();
    await arrived.promise;
    await visitChat(page, b);
    release.resolve();
    const saved = await created.promise;
    expect(saved.profile_id).toBe(a.profile_id);
    await expect(chatChoice(page, 'mock', mockModels[0].id)).toBeVisible();
    await expect.poll(() => worker.executions).toEqual([expectedCall(saved, 'mock', mockModels[1].id, prompt)]);
    await visitChat(page, saved);
    await expect(chatChoice(page, 'mock', mockModels[1].id)).toBeVisible();
    await page.locator('[data-sidebar-new]').click();
    await page.locator('[data-new-chat]').click();
    await expect(chatChoice(page, 'mock', mockModels[0].id)).toBeVisible();
    expect(creates).toBe(2);
  } finally {
    release.resolve();
  }
});

test('AUD-03: a removed explicit model stays selected, blocks sending and recovers when restored', async ({ page, worker }) => {
  const chat = await savedChat(page, 'CEO');
  let catalog: readonly Model[] = mockModels;
  await page.route(rpcUrl, route => browserRpc(route.request())?.method === 'models.free'
    ? route.fulfill({ json: { result: { models: catalog } } })
    : route.fallback());
  await openSaved(page, chat);
  await pick(modelPicker(page), mockModels[1].id);
  await add(page).click();
  catalog = [mockModels[0]];
  await page.reload();
  await expect(chatChoice(page, 'mock', mockModels[1].id)).toBeVisible();
  await composer(page).fill('Must wait for the chosen model.');
  await expect(send(page)).toBeDisabled();
  expect(worker.executions).toEqual([]);
  catalog = mockModels;
  await page.reload();
  await expect(chatChoice(page, 'mock', mockModels[1].id)).toBeVisible();
  await expect(send(page)).toBeEnabled();
  await send(page).click();
  await expect.poll(() => worker.executions).toEqual([
    expectedCall(chat, 'mock', mockModels[1].id, 'Must wait for the chosen model.'),
  ]);
});

test('AUD-03: unattributed legacy values and corrupt or missing conversation overrides do not leak', async ({ page }) => {
  const a = await savedChat(page, 'CEO');
  const b = await savedChat(page, 'specialist');
  await page.addInitScript(({ cid, valid, missing }) => {
    localStorage.setItem('agentos.rusebot.selected.v1', JSON.stringify(cid));
    localStorage.setItem('agentos.rusebot.engine.v1', JSON.stringify(valid));
    localStorage.setItem('agentos.rusebot.chatExecution.v1.' + cid, '{invalid json');
    localStorage.setItem('agentos.rusebot.chatExecution.v1.' + missing, JSON.stringify(valid));
  }, { cid: a.id, valid: { engine: 'mock', model: mockModels[1].id }, missing: 'missing-conversation-id' });
  await page.goto('/');
  await expect(sidebarChat(page, a)).toHaveAttribute('aria-current', 'page');
  await expect(chatChoice(page, 'mock', mockModels[0].id)).toBeVisible();
  await visitChat(page, b);
  await expect(chatChoice(page, 'mock', mockModels[0].id)).toBeVisible();
  await page.locator('[data-sidebar-new]').click();
  await page.locator('[data-new-chat]').click();
  await expect(chatChoice(page, 'mock', mockModels[0].id)).toBeVisible();
  expect(await page.evaluate(() => localStorage.getItem('agentos.rusebot.engine.v1'))).toContain(mockModels[1].id);
  expect(await page.evaluate(() => localStorage.getItem('agentos.rusebot.chatExecution.v1.missing-conversation-id'))).toContain(mockModels[1].id);
});

test('AUD-03: reload restores the next-send choice while Retry uses the original accepted pair', async ({ page, worker }) => {
  const chat = await savedChat(page, 'specialist');
  const held = worker.holdAcknowledgement();
  const prompt = 'Keep the accepted execution and the next choice independent.';
  try {
    await openSaved(page, chat);
    await add(page).click();
    await composer(page).fill(prompt);
    await send(page).click();
    expect(await held.accepted).toBe(200);
    await chooseChatPair(page, 'mock', mockModels[1].id);
    held.release();
    await expect(retry(page)).toBeVisible();
    await page.reload();
    await expect(chatChoice(page, 'mock', mockModels[1].id)).toBeVisible();
    await retry(page).click();
    await expect.poll(() => worker.executions.map(({ status }) => status)).toEqual([502, 200]);
    expect(worker.executions[0]).toEqual(expectedCall(chat, 'mock', mockModels[0].id, prompt, 502));
    expect(worker.executions[1].params).toEqual(worker.executions[0].params);
    expect(await userMessages(page, chat)).toEqual([prompt]);
    await expect(page.locator('[data-role="assistant"] [data-chat-bubble]')).toHaveAttribute('title', /mock · deepseek-v4\.1-flash-free$/);
  } finally {
    held.release();
  }
});

test('A: Enter after selecting a cold provider must not send to the previous one', async ({ page, worker }) => {
  const chat = await savedChat(page, 'CEO');
  const next = worker.holdCatalog(codex.engine_id);
  try {
    await openSaved(page, chat);
    await composer(page).fill('A pending provider must not receive this draft through the old route.');
    await expect(send(page)).toBeEnabled();
    await pick(enginePicker(page), codex.engine_id);
    await next.requested;
    await selection(page, codex.engine_id, '');
    await expect(page.locator('[data-composer-no-provider]')).toContainText('Loading models');
    await worker.snapshot('Codex option selected, catalog response held');
    await add(page).click();
    await tryEnter(page);
    await expect.poll(() => worker.executions.every((call) => call.status !== null)).toBe(true);
    expect(worker.executions, 'No previous-engine execution after selecting Codex').toEqual([]);
  } finally {
    next.release({ models: [codex.model] });
  }
});

test('B/C: rapid picks keep the latest provider and model after both earlier replies arrive', async ({ page, worker }) => {
  const chat = await savedChat(page, 'specialist');
  const first = worker.holdCatalog(openCode.engine_id);
  const second = worker.holdCatalog(codex.engine_id);
  try {
    await openSaved(page, chat);
    await pick(enginePicker(page), openCode.engine_id);
    await first.requested;
    await pick(enginePicker(page), codex.engine_id);
    await second.requested;
    await pick(enginePicker(page), claude.engine_id);
    await selection(page, claude.engine_id, claude.model.id);
    const released = (engine: string) => page.waitForResponse((response) => {
      const call = browserRpc(response.request());
      return call?.method === 'engines.models' && call.params.engine_id === engine;
    });
    const responses = Promise.all([released(openCode.engine_id), released(codex.engine_id)]);
    second.release({ models: [codex.model] });
    first.release({ models: [openCode.model] });
    await Promise.all((await responses).map((response) => response.finished()));
    await expect(composer(page)).toHaveAccessibleName('Message F-02.2 specialist');
    await worker.snapshot('Claude confirmed after earlier OpenCode and Codex replies');
    await selection(page, claude.engine_id, claude.model.id);
    const prompt = 'Submit with the last provider picked.';
    await composer(page).fill(prompt);
    await add(page).click();
    await composer(page).press('Enter');
    await expect.poll(() => worker.executions).toEqual([expectedCall(chat, claude.engine_id, claude.model.id, prompt, 422)]);
    await expect(page.getByRole('alert').filter({ hasText: 'Fixture intercepted non-Mock execution.' })).toBeVisible();
  } finally {
    first.release({ models: [openCode.model] });
    second.release({ models: [codex.model] });
  }
});

test('D: a failed catalog keeps the target provider visible and blocks execution', async ({ page, worker }) => {
  const chat = await savedChat(page, 'CEO');
  const next = worker.holdCatalog(codex.engine_id);
  try {
    await openSaved(page, chat);
    await pick(enginePicker(page), codex.engine_id);
    await next.requested;
    next.release({ models: [], error: 'Fixture catalog unavailable.' });
    await selection(page, codex.engine_id, '');
    await worker.snapshot('Codex catalog failed');
    await add(page).click();
    const prompt = 'Keep the draft when the catalog fails.';
    await composer(page).fill(prompt);
    await expect(page.locator('[data-composer-no-provider]')).toBeVisible();
    await expect(send(page)).toBeDisabled();
    await tryEnter(page);
    expect(worker.executions).toEqual([]);
    await expect(composer(page)).toHaveValue(prompt);
  } finally {
    next.release({ models: [] });
  }
});

test('E: a model removed by Refresh cannot be sent while another catalog is pending', async ({ page, worker }) => {
  const chat = await savedChat(page, 'CEO');
  await openSaved(page, chat);
  await pick(enginePicker(page), openCode.engine_id);
  await selection(page, openCode.engine_id, openCode.model.id);
  await pick(enginePicker(page), 'mock');
  await selection(page, 'mock', mockModels[0].id);
  await page.getByRole('menuitem', { name: 'Manage providers…' }).click();
  const dialog = page.getByRole('dialog');
  const refresh = dialog.locator('[data-providers-refresh]');
  await expect(refresh).toBeEnabled();
  const slow = worker.holdCatalog(openCode.engine_id);
  const changed = worker.holdCatalog('mock');
  try {
    await refresh.click();
    await Promise.all([slow.requested, changed.requested]);
    changed.release({ models: [mockModels[1]] });
    await dialog.getByRole('button', { name: 'Close settings' }).click();
    await expect(dialog).toHaveCount(0);
    await add(page).click();
    await modelPicker(page).click();
    await expect(page.getByRole('option', { name: mockModels[1].name, exact: true })).toBeVisible();
    await expect(page.getByRole('option', { name: mockModels[0].name, exact: true })).toHaveCount(0);
    await page.keyboard.press('Escape');
    await worker.snapshot('Mock catalog removed old model, OpenCode refresh still held');
    await add(page).click();
    await composer(page).fill('Only use a model still in this catalog.');
    await tryEnter(page);
    await expect.poll(() => worker.executions.every((call) => call.status !== null)).toBe(true);
    for (const call of worker.executions) {
      expect(call.params).toMatchObject({ engine: 'mock', model_id: mockModels[1].id });
    }
    if (!worker.executions.length) {
      await add(page).click();
      await pick(modelPicker(page), mockModels[1].id);
      await add(page).click();
      await expect(send(page)).toBeEnabled();
      await composer(page).press('Enter');
      await expect.poll(() => worker.executions).toEqual([
        expectedCall(chat, 'mock', mockModels[1].id, 'Only use a model still in this catalog.'),
      ]);
    }
  } finally {
    changed.release({ models: [mockModels[1]] });
    slow.release({ models: [openCode.model] });
  }
});

for (const recipient of ['CEO', 'specialist'] as const) {
  test('F/G: ' + recipient + ' keeps its saved route and uses the model just selected', async ({ page, worker }) => {
    const chat = await savedChat(page, recipient);
    await openSaved(page, chat);
    await pick(enginePicker(page), codex.engine_id);
    await selection(page, codex.engine_id, codex.model.id);
    await pick(enginePicker(page), 'mock');
    const prompt = 'Answer using the newly selected Mock model.';
    await composer(page).fill(prompt);
    await pick(modelPicker(page), mockModels[1].id);
    await add(page).click();
    await composer(page).press('Enter');
    await expect.poll(() => worker.executions).toEqual([expectedCall(chat, 'mock', mockModels[1].id, prompt)]);
    await add(page).click();
    await worker.snapshot('Selection after immediate send');
    await selection(page, 'mock', mockModels[1].id);
    const listed = await rpc<{ conversations: Conversation[] }>(page, 'conversations.list');
    expect(listed.conversations.find(({ id }) => id === chat.id)?.profile_id).toBe(chat.profile_id);
  });
}

async function recoverAcceptedTurn(page: Page, worker: ReturnType<typeof installFixture>, reload: boolean) {
  const chat = await savedChat(page, 'specialist');
  const ack = worker.holdAcknowledgement();
  const prompt = 'Recover this already accepted specialist turn.';
  try {
    await openSaved(page, chat);
    await add(page).click();
    await composer(page).fill(prompt);
    await composer(page).press('Enter');
    expect(await ack.accepted).toBe(200);
    await add(page).click();
    await pick(modelPicker(page), mockModels[1].id);
    await selection(page, 'mock', mockModels[1].id);
    await worker.snapshot('Selected new Mock model while first response held');
    await add(page).click();
    ack.release();
    await expect(retry(page)).toBeVisible();
    if (reload) {
      await page.reload();
      await expect(page.locator('[data-chathead-conversation]')).toHaveText(chat.title);
      await add(page).click();
      await pick(modelPicker(page), mockModels[1].id);
      await selection(page, 'mock', mockModels[1].id);
      await add(page).click();
    }
    await retry(page).click();
    await expect.poll(() => worker.executions.map(({ status }) => status)).toEqual([502, expect.any(Number)]);
    const [original, recovered] = worker.executions;
    expect(original).toEqual(expectedCall(chat, 'mock', mockModels[0].id, prompt, 502));
    const history = await rpc<{ messages: Array<{ role: string; content: string }> }>(page, 'conversations.messages', { conversation_id: chat.id });
    expect(history.messages.filter((message) => message.role === 'user' && message.content === prompt)).toHaveLength(1);
    expect.soft(recovered.params, 'Retry must reuse the accepted request payload').toEqual(original.params);
    expect(recovered.status, 'The mock worker should acknowledge the existing run').toBe(200);
  } finally {
    ack.release();
  }
}

for (const reload of [false, true]) {
  test('H: Retry of an accepted specialist turn preserves the original model and request key' + (reload ? ' after reload' : ''),
    async ({ page, worker }) => recoverAcceptedTurn(page, worker, reload));
}

type SpecialistRun = {
  id: string;
  status: string;
  conversation_id: string;
  engine: string;
  model_id: string;
};

async function specialistRuns(page: Page, chat: Conversation) {
  const result = await rpc<{ runs: SpecialistRun[] }>(page, 'runs.list', { conversation_id: chat.id });
  return result.runs;
}

async function userMessages(page: Page, chat: Conversation) {
  const result = await rpc<{ messages: Array<{ role: string; content: string }> }>(
    page, 'conversations.messages', { conversation_id: chat.id },
  );
  return result.messages.filter(({ role }) => role === 'user').map(({ content }) => content);
}

function recoveryRecord(page: Page, requestKey: unknown) {
  expect(requestKey).toEqual(expect.any(String));
  return page.evaluate((key) => {
    const stored = localStorage.getItem('agentos.rusebot.submission.v1.' + key);
    return stored ? JSON.parse(stored) : null;
  }, String(requestKey));
}

async function submitWithLostReply(
  page: Page, worker: ReturnType<typeof installFixture>, chat: Conversation, prompt: string, forward = true,
) {
  const held = worker.holdAcknowledgement(forward);
  let durable: SpecialistRun[];
  try {
    await openSaved(page, chat);
    await add(page).click();
    await composer(page).fill(prompt);
    await composer(page).press('Enter');
    expect(await held.accepted).toBe(forward ? 200 : null);
    durable = await specialistRuns(page, chat);
    expect(durable).toHaveLength(forward ? 1 : 0);
  } finally {
    held.release();
  }
  await expect(retry(page)).toBeVisible();
  await expect.poll(() => worker.executions).toEqual([expectedCall(chat, 'mock', mockModels[0].id, prompt, 502)]);
  return { original: worker.executions[0], acceptedRun: durable[0] };
}

async function removeOriginalModel(page: Page, worker: ReturnType<typeof installFixture>) {
  await add(page).click();
  await page.getByRole('menuitem', { name: 'Manage providers…' }).click();
  const dialog = page.getByRole('dialog');
  const catalog = worker.holdCatalog('mock');
  try {
    await dialog.locator('[data-providers-refresh]').click();
    await catalog.requested;
    catalog.release({ models: [mockModels[1]] });
    await dialog.getByRole('button', { name: 'Close settings' }).click();
    await expect(dialog).toHaveCount(0);
    await add(page).click();
    await selection(page, 'mock', mockModels[1].id);
    await modelPicker(page).click();
    await expect(page.getByRole('option', { name: mockModels[1].name, exact: true })).toBeVisible();
    await expect(page.getByRole('option', { name: mockModels[0].name, exact: true })).toHaveCount(0);
    await page.keyboard.press('Escape');
    await page.keyboard.press('Escape');
  } finally {
    catalog.release({ models: [mockModels[1]] });
  }
}

const unavailableRecovery = (page: Page) => page.getByRole('alert').filter({
  hasText: 'The original engine and model are currently unavailable',
});

test('R1: recover an accepted specialist turn after its original model leaves the catalog', async ({ page, worker }) => {
  const chat = await savedChat(page, 'specialist');
  const prompt = 'An accepted turn whose original model is removed.';
  const { original, acceptedRun } = await submitWithLostReply(page, worker, chat, prompt);
  const key = original.params.request_key;
  await removeOriginalModel(page, worker);
  expect(await recoveryRecord(page, key)).toMatchObject({
    cid: chat.id, agentId: chat.profile_id, execution: { engine: 'mock', model: mockModels[0].id },
    prompt, requestId: key,
  });

  await retry(page).click();
  await expect.poll(async () => (await recoveryRecord(page, key)) === null || (await unavailableRecovery(page).count()) > 0,
    { message: 'Wait for Retry to reconcile or report that it is blocked' }).toBe(true);
  const remainingRecovery = await recoveryRecord(page, key);
  expect((await specialistRuns(page, chat)).map(({ id }) => id)).toEqual([acceptedRun.id]);
  expect(await userMessages(page, chat)).toEqual([prompt]);
  for (const replay of worker.executions.slice(1)) expect(replay.params).toEqual(original.params);

  // This probe has positive evidence of acceptance. Its result cannot acknowledge the browser's Retry.
  const workerReplay = await rpc<SpecialistRun>(page, 'runs.start_turn', original.params);
  expect(workerReplay).toMatchObject({
    id: acceptedRun.id, conversation_id: chat.id, engine: 'mock', model_id: mockModels[0].id,
  });
  expect((await specialistRuns(page, chat)).map(({ id }) => id)).toEqual([acceptedRun.id]);
  expect(await userMessages(page, chat)).toEqual([prompt]);
  await test.info().attach('accepted-run-replay', {
    body: JSON.stringify({ acceptedRun, workerReplay, requestKey: key, browserRecoveryCleared: remainingRecovery === null }),
    contentType: 'application/json',
  });
  // Deferred until the worker supports lookup or replay-only recovery. Blind replay with an
  // unavailable model could start new work if the original request never reached the worker.
  test.fail();
  expect(remainingRecovery, 'The browser should recover the known accepted run without creating another one').toBeNull();
  await expect(retry(page)).toHaveCount(0);
});

test('R1 safety: a lost request never accepted by the worker is not started after model removal', async ({ page, worker }) => {
  const chat = await savedChat(page, 'specialist');
  const prompt = 'This submission never reached the worker.';
  const { original } = await submitWithLostReply(page, worker, chat, prompt, false);
  await removeOriginalModel(page, worker);
  await retry(page).click();
  await expect(unavailableRecovery(page)).toBeVisible();
  expect(worker.executions).toEqual([original]);
  expect(await specialistRuns(page, chat)).toEqual([]);
  expect(await userMessages(page, chat)).toEqual([]);
  expect(await recoveryRecord(page, original.params.request_key)).toMatchObject({ prompt });
  await expect(composer(page)).toHaveValue(prompt);
});

test('R2: replaying a terminal specialist turn leaves the newer active turn in control of the composer', async ({ page, worker }) => {
  const chat = await savedChat(page, 'specialist');
  const first = 'Turn A completes after its acknowledgement is lost.';
  const second = 'Turn B stays active during the replay [mock:delay=30000]';
  const unsent = 'Keep this draft while Turn B is active.';
  const { original, acceptedRun } = await submitWithLostReply(page, worker, chat, first);
  const getRun = (id: string) => rpc<SpecialistRun>(page, 'runs.get', { run_id: id });
  await expect.poll(async () => (await getRun(acceptedRun.id)).status).toBe('succeeded');
  await composer(page).fill(second);
  await composer(page).press('Enter');
  await expect.poll(() => worker.executions[1]).toEqual(expectedCall(chat, 'mock', mockModels[0].id, second));
  const fresh = worker.executions[1];
  expect(fresh.params.request_key).not.toBe(original.params.request_key);
  const active = (await specialistRuns(page, chat)).find(({ id }) => id !== acceptedRun.id);
  expect(active).toBeDefined();
  const activeId = active!.id;
  const stop = page.getByRole('button', { name: 'Stop', exact: true });
  let controls: { stop: boolean; sendVisible: boolean; sendEnabled: boolean };
  try {
    await expect(page.locator('[data-working]')).toHaveText('Working');
    await expect(stop).toBeEnabled();
    await expect(send(page)).toHaveCount(0);
    await composer(page).fill(unsent);
    const response = page.waitForResponse((reply) => {
      const request = browserRpc(reply.request());
      return request?.method === 'runs.start_turn' && request.params.request_key === original.params.request_key;
    });
    await retry(page).click();
    const replay = await response;
    expect(replay.status()).toBe(200);
    expect(browserRpc(replay.request())).toEqual({ method: original.method, params: original.params });
    const terminal = (await replay.json()).result as SpecialistRun;
    expect(terminal).toMatchObject({ id: acceptedRun.id, status: 'succeeded' });
    await expect.poll(() => recoveryRecord(page, original.params.request_key)).toBeNull();
    // Let React commit the acknowledged replay before reading the controls.
    await page.evaluate(() => new Promise<void>((done) => requestAnimationFrame(() => requestAnimationFrame(() => done()))));
    const current = await getRun(activeId);
    expect(current.status).toBe('running');
    expect((await specialistRuns(page, chat)).map(({ id }) => id).sort()).toEqual([acceptedRun.id, activeId].sort());
    expect(await userMessages(page, chat)).toEqual([first, second]);
    expect(worker.executions).toHaveLength(3);
    await expect(composer(page)).toHaveValue(unsent);
    const sendVisible = await send(page).isVisible();
    controls = {
      stop: await stop.isVisible() && await stop.isEnabled(),
      sendVisible,
      sendEnabled: sendVisible && await send(page).isEnabled(),
    };
    await test.info().attach('replay-with-active-run', {
      body: JSON.stringify({ replayed: terminal, active: current, controls }), contentType: 'application/json',
    });
    if (controls.stop) {
      const cancelled = page.waitForResponse((reply) => browserRpc(reply.request())?.method === 'runs.cancel');
      await stop.click();
      const acknowledgement = await cancelled;
      expect(browserRpc(acknowledgement.request())?.params).toEqual({ run_id: activeId });
      expect(acknowledgement.status()).toBe(200);
    }
  } finally {
    const { status } = await getRun(activeId);
    if (status === 'queued' || status === 'running') await rpc(page, 'runs.cancel', { run_id: activeId });
  }
  expect((await getRun(activeId)).status).toBe('cancelled');
  await expect(send(page)).toBeEnabled();
  expect(controls, 'Turn B was still running and must own the composer controls').toEqual({
    stop: true, sendVisible: false, sendEnabled: false,
  });
});

for (const changeModel of [true, false]) {
  test('Stop followed by a fresh Send ' + (changeModel ? 'with a changed model' : 'with the same model'), async ({ page, worker }) => {
    const chat = await savedChat(page, 'specialist');
    const first = 'Cancelable turn [mock:delay=30000]';
    const fresh = 'Fresh user-authored turn';
    await openSaved(page, chat);
    await add(page).click();
    await composer(page).fill(first);
    await composer(page).press('Enter');
    await expect.poll(() => worker.executions).toEqual([expectedCall(chat, 'mock', mockModels[0].id, first)]);
    const runs = await rpc<{ runs: Array<{ id: string }> }>(page, 'runs.list', { conversation_id: chat.id });
    const runId = runs.runs[0].id;
    const requested = deferred<void>();
    const release = deferred<void>();
    await page.route(rpcUrl, async (route) => {
      const call = browserRpc(route.request());
      if (call?.method !== 'runs.cancel') return route.fallback();
      expect(call.params).toEqual({ run_id: runId });
      requested.resolve();
      await release.promise;
      return route.continue();
    });
    const stop = page.getByRole('button', { name: 'Stop', exact: true });
    try {
      await stop.click();
      await requested.promise;
      await expect(stop).toBeDisabled();
      await composer(page).fill(fresh);
      expect(await tryEnter(page)).toBeNull();
      expect(worker.executions).toHaveLength(1);
    } finally {
      release.resolve();
    }
    await expect(send(page)).toBeEnabled();
    const cancelled = await rpc<{ id: string; status: string }>(page, 'runs.start_turn', worker.executions[0].params);
    expect(cancelled).toMatchObject({ id: runId, status: 'cancelled' });
    const selected = changeModel ? mockModels[1] : mockModels[0];
    await add(page).click();
    if (changeModel) await pick(modelPicker(page), selected.id);
    await selection(page, 'mock', selected.id);
    await add(page).click();
    await expect(composer(page)).toHaveValue(fresh);
    await composer(page).press('Enter');
    await expect.poll(() => worker.executions[1]).toEqual(expectedCall(chat, 'mock', selected.id, fresh));
    expect(worker.executions).toHaveLength(2);
    expect(worker.executions[1].params.request_key).not.toBe(worker.executions[0].params.request_key);
  });
}

test('An active-run conflict keeps the draft and does not dispatch a replacement', async ({ page, worker }) => {
  const chat = await savedChat(page, 'specialist');
  await openSaved(page, chat);
  await add(page).click();
  const active = await rpc<{ id: string }>(page, 'runs.start_turn', {
    conversation_id: chat.id, engine: 'mock', model_id: mockModels[0].id, prompt: '[mock:delay=30000]',
  });
  try {
    const prompt = 'Keep my draft while the conversation is busy.';
    await composer(page).fill(prompt);
    await composer(page).press('Enter');
    await expect(page.getByRole('alert')).toContainText('Conversation already has an active run');
    await expect(composer(page)).toHaveValue(prompt);
    expect(worker.executions).toEqual([expectedCall(chat, 'mock', mockModels[0].id, prompt, 409)]);
  } finally {
    await rpc(page, 'runs.cancel', { run_id: active.id });
  }
});

test('An older recovery record retains the prompt and cannot replay with guessed settings', async ({ page, worker }) => {
  const chat = await savedChat(page, 'specialist');
  const prompt = 'Recover text from before execution settings were saved.';
  const id = 'ui-0f969925-6daa-4ddc-b824-408e09e877a4';
  await page.addInitScript(({ chat, prompt, id }) => {
    localStorage.setItem('agentos.rusebot.drafts.v1', JSON.stringify({ [chat.id]: prompt }));
    localStorage.setItem('agentos.rusebot.submission.v1.' + id, JSON.stringify({
      cid: chat.id, agentId: chat.profile_id, prompt, requestId: id, ownsDraft: true,
    }));
  }, { chat, prompt, id });
  await openSaved(page, chat);
  await add(page).click();
  await expect(page.getByRole('alert')).toContainText('original execution settings were not saved');
  await expect(retry(page)).toHaveCount(0);
  await expect(composer(page)).toHaveValue(prompt);
  expect(await tryEnter(page)).toBeNull();
  await page.getByRole('button', { name: 'Dismiss', exact: true }).click();
  await expect(composer(page)).toHaveValue(prompt);
  expect(await page.evaluate((id) => localStorage.getItem('agentos.rusebot.submission.v1.' + id), id)).toBeNull();
  expect(worker.executions).toEqual([]);
});
