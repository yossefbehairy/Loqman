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
      name: 'F-02.2 specialist', role_type: 'custom', instructions: 'Answer briefly.',
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
    localStorage.setItem('agentos.rusebot.engine.v1', JSON.stringify({ engine: 'mock', model: 'deepseek-v4.1-flash-free' }));
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
