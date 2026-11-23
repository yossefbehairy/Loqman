import { expect, test, type Locator, type Page } from '@playwright/test';

type Preference = { engine: string; model_id: string };
type Rpc = { method: string; params: Record<string, unknown> };

const alpha = { engine: 'opencode', model_id: 'fixture-a1' };
const alternate = { engine: 'opencode', model_id: 'fixture-a2' };
const latest = { engine: 'opencode', model_id: 'fixture-a3' };
const beta = { engine: 'codex', model_id: 'fixture-b1' };
const catalogs = { opencode: [alpha, alternate, latest], codex: [beta] };

function deferred() {
  let release!: () => void;
  const promise = new Promise<void>(resolve => { release = resolve; });
  return { promise, release };
}

async function fixture(page: Page) {
  let saved = alpha;
  let rejectSave = false;
  let heldSave: { received: ReturnType<typeof deferred>; reply: ReturnType<typeof deferred> } | undefined;
  let heldCodexCatalog: { received: ReturnType<typeof deferred>; reply: ReturnType<typeof deferred> } | undefined;
  const writes: Preference[] = [];
  const reads: Preference[] = [];
  const executions: Rpc[] = [];
  const acknowledge = (pref: Preference) => ({
    ...pref, effective_engine: pref.engine, effective_model_id: pref.model_id,
  });
  await page.route('**/__agentos/call', async route => {
    const call = route.request().postDataJSON() as Rpc;
    const reply = (result: unknown) => route.fulfill({ json: { result } });
    if (call.method === 'engines.capabilities') {
      return reply({ opencode: { conversation_runs: true }, codex: { conversation_runs: true } });
    }
    if (call.method === 'engines.status') {
      return reply({ engines: Object.keys(catalogs).map(engine_id => ({
        engine_id, title: engine_id, state: 'ready', install_state: 'installed', auth_state: 'authenticated',
      })) });
    }
    if (call.method === 'engines.models') {
      if (call.params.engine_id === beta.engine && heldCodexCatalog) {
        heldCodexCatalog.received.release();
        await heldCodexCatalog.reply.promise;
      }
      const models = catalogs[call.params.engine_id as keyof typeof catalogs] ?? [];
      return reply({ models: models.map(({ model_id }) => ({ id: model_id, name: model_id })) });
    }
    if (call.method === 'preferences.get') {
      reads.push(saved);
      return reply(acknowledge(saved));
    }
    if (call.method === 'preferences.set_default') {
      const next = { engine: String(call.params.engine), model_id: String(call.params.model_id) };
      writes.push(next);
      const hold = heldSave;
      heldSave = undefined;
      if (hold) {
        hold.received.release();
        await hold.reply.promise;
      }
      if (rejectSave) return route.fulfill({ status: 500, json: { status: 500, detail: 'The default could not be saved.' } });
      saved = next;
      return reply(acknowledge(saved));
    }
    if (call.method === 'ceo.ask' || call.method === 'runs.start_turn' || call.method === 'missions.delegate') {
      executions.push(call);
      return route.fulfill({ status: 502, json: { status: 502, detail: 'Fixture rejected execution before dispatch.' } });
    }
    return route.continue();
  });
  return {
    writes, reads, executions,
    saved: () => saved,
    failSave: () => { rejectSave = true; },
    holdSave: () => (heldSave = { received: deferred(), reply: deferred() }),
    holdCodexCatalog: () => (heldCodexCatalog = { received: deferred(), reply: deferred() }),
  };
}

async function rpc<T>(page: Page, method: string, params: object): Promise<T> {
  const response = await page.request.post('/__agentos/call', { data: { method, params } });
  expect(response.ok(), method + ' on the isolated worker').toBe(true);
  return (await response.json()).result as T;
}

async function pick(trigger: Locator, value: string) {
  const page = trigger.page();
  await trigger.click();
  await page.getByRole('option').and(page.locator(`[data-value="${value}"]`)).click();
  await expect(page.getByRole('listbox')).toHaveCount(0);
}

const add = (page: Page) => page.getByRole('button', { name: 'Add', exact: true });
const pill = (page: Page, pref: Preference) =>
  page.locator(`[data-chathead-pill] [title="${pref.engine} · ${pref.model_id}"]`);
const defaults = (page: Page) => page.locator('[data-providers-default]');

async function openGeneral(page: Page) {
  await page.getByRole('button', { name: 'Settings', exact: true }).click();
  await expect(defaults(page)).toBeVisible();
}

const closeGeneral = (page: Page) => page.getByRole('button', { name: 'Close settings' }).click();

async function displayedDefault(page: Page, pref: Preference) {
  await expect(defaults(page).locator('#ai-default-engine')).toHaveAttribute('data-value', pref.engine);
  await expect(defaults(page).locator('#ai-default-model')).toHaveAttribute('data-value', pref.model_id);
}

async function openHome(page: Page) {
  await page.goto('/?ui=rusebot');
  await expect(pill(page, alpha)).toBeVisible();
}

async function overrideChat(page: Page, pref: Preference) {
  await add(page).click();
  const engine = page.locator('#rb-engine');
  if (await engine.getAttribute('data-value') !== pref.engine) await pick(engine, pref.engine);
  const model = page.locator('#rb-model');
  await expect(model).toBeEnabled();
  if (await model.getAttribute('data-value') !== pref.model_id) await pick(model, pref.model_id);
  await expect(model).toHaveAttribute('data-value', pref.model_id);
  await add(page).click();
  await expect(pill(page, pref)).toBeVisible();
}

test('AUD-03: Settings and Agent defaults update inheritance while preserving explicit overrides', async ({ page }) => {
  const worker = await fixture(page);
  const profile = await rpc<{ id: string }>(page, 'profiles.create', {
    name: 'AUD-03 configured Agent', role_type: 'custom', instructions: 'Reply briefly.',
    engine: alpha.engine, default_model_id: latest.model_id,
  });
  const a = await rpc<{ id: string }>(page, 'conversations.create', { title: 'AUD-03 overrides CEO' });
  const b = await rpc<{ id: string }>(page, 'conversations.create', { title: 'AUD-03 inherits CEO' });
  const specialist = await rpc<{ id: string }>(page, 'conversations.create', { title: 'AUD-03 inherits Agent', profile_id: profile.id });
  const specialistOverride = await rpc<{ id: string }>(page, 'conversations.create', {
    title: 'AUD-03 overrides Agent', profile_id: profile.id,
  });
  const select = (id: string) => page.locator(`[data-sidebar-thread-row="${id}"]`).click();
  await openHome(page);
  await select(a.id);
  await overrideChat(page, beta);
  await openGeneral(page);
  await pick(page.locator('#ai-default-model'), alternate.model_id);
  await displayedDefault(page, alternate);
  await closeGeneral(page);
  await expect(pill(page, beta)).toBeVisible();
  await select(b.id);
  await expect(pill(page, alternate)).toBeVisible();
  await select(specialist.id);
  await expect(pill(page, latest)).toBeVisible();
  await page.locator('[data-sidebar-new]').click();
  await page.locator('[data-new-chat]').click();
  await expect(pill(page, latest)).toBeVisible();
  await select(specialistOverride.id);
  await overrideChat(page, beta);
  await rpc(page, 'profiles.update', { profile_id: profile.id, default_model_id: alternate.model_id });
  await page.locator('[data-sidebar-nav=team]').click();
  await select(specialist.id);
  await expect(pill(page, alternate)).toBeVisible();
  await page.locator('[data-sidebar-new]').click();
  await page.locator('[data-new-chat]').click();
  await expect(pill(page, alternate)).toBeVisible();
  await select(specialistOverride.id);
  await expect(pill(page, beta)).toBeVisible();
  await select(a.id);
  await expect(pill(page, beta)).toBeVisible();
  expect(worker.writes).toEqual([alternate]);
});

test.describe('AUD03-F2: specialist profile discovery', () => {
  const composer = (page: Page) => page.locator('[data-tour="composer"]').getByRole('textbox');
  const send = (page: Page) => page.getByRole('button', { name: 'Send', exact: true });
  const failure = (page: Page) => page.locator('[data-load-error]');
  const profileError = 'Agent discovery is temporarily unavailable.';
  const rejectProfiles = { status: 503, json: { status: 503, detail: profileError } };
  const createSpecialist = (page: Page, name: string, configuration: { engine: string | null; default_model_id: string }) => rpc<{ id: string }>(
    page, 'profiles.create', { name, role_type: 'custom', instructions: 'Reply briefly.', ...configuration },
  );
  const createChat = (page: Page, title: string, profile_id?: string) => rpc<{ id: string; profile_id: string | null }>(
    page, 'conversations.create', { title, profile_id },
  );
  const expectedTurn = (id: string, pair: Preference, prompt: string) => ({
    method: 'runs.start_turn',
    params: { conversation_id: id, ...pair, prompt, request_key: expect.any(String) },
  });
  const selectionKey = 'agentos.rusebot.selected.v1';
  const overridePrefix = 'agentos.rusebot.chatExecution.v1.';

  test('A/B: blocks workspace fallback until the Agent read is retried and sends the configured pair once', async ({ page }) => {
    const worker = await fixture(page);
    const name = 'AUD03-F2 configured specialist';
    const agent = await createSpecialist(page, name, { engine: beta.engine, default_model_id: beta.model_id });
    const chat = await createChat(page, 'AUD03-F2 bound specialist', agent.id);
    expect(chat.profile_id).toBe(agent.id);
    const retryStarted = deferred();
    const releaseRetry = deferred();
    let recovering = false;
    await page.route('**/__agentos/call', async route => {
      if ((route.request().postDataJSON() as Rpc).method !== 'profiles.list') return route.fallback();
      if (!recovering) return route.fulfill(rejectProfiles);
      retryStarted.release();
      await releaseRetry.promise;
      return route.continue();
    });
    await page.addInitScript(({ key, id }) => localStorage.setItem(key, JSON.stringify(id)), { key: selectionKey, id: chat.id });

    const rejectedPrompt = 'Keep this draft while Agent discovery is unavailable.';
    const recoveredPrompt = 'Use the configured specialist pair after recovery.';
    try {
      await page.goto('/');
      await expect(page.locator('[data-chathead-conversation]')).toHaveText('AUD03-F2 bound specialist');
      await expect(failure(page)).toContainText(profileError);
      const agentNotice = page.locator('[data-composer-no-provider]');
      await expect(agentNotice).toContainText('Could not load this Agent’s Engine and Model');
      await composer(page).fill(rejectedPrompt);
      await expect(send(page)).toBeDisabled();
      await expect(pill(page, alpha)).toHaveCount(0);
      await composer(page).press('Enter');

      recovering = true;
      await failure(page).getByRole('button', { name: 'Retry', exact: true }).click();
      await retryStarted.promise;
      await expect(failure(page)).toBeVisible();
      expect(worker.executions).toEqual([]);
      releaseRetry.release();
      await expect(failure(page)).toHaveCount(0);
      await expect(agentNotice).toHaveCount(0);
      await expect(composer(page)).toHaveAccessibleName('Message ' + name);
      await expect(pill(page, beta)).toBeVisible();
      await expect(composer(page)).toHaveValue(rejectedPrompt);
      await composer(page).fill(recoveredPrompt);
      await send(page).click();
      await expect(page.locator('[data-chat-retry]')).toBeVisible();
      expect(worker.executions).toEqual([expectedTurn(chat.id, beta, recoveredPrompt)]);
      expect(worker.writes).toEqual([]);
      expect(await page.evaluate(key => localStorage.getItem(key), overridePrefix + chat.id)).toBeNull();
    } finally {
      releaseRetry.release();
    }
  });

  test('C: a discovered Agent with inherited settings sends the workspace pair', async ({ page }) => {
    const worker = await fixture(page);
    const name = 'AUD03-F2 inheriting specialist';
    const agent = await createSpecialist(page, name, { engine: null, default_model_id: 'not-in-workspace-catalog' });
    const chat = await createChat(page, 'AUD03-F2 inherited specialist', agent.id);
    const discovery = deferred();
    const releaseDiscovery = deferred();
    await page.route('**/__agentos/call', async route => {
      if ((route.request().postDataJSON() as Rpc).method !== 'profiles.list') return route.fallback();
      discovery.release();
      await releaseDiscovery.promise;
      return route.continue();
    });
    await page.addInitScript(({ key, id }) => localStorage.setItem(key, JSON.stringify(id)), { key: selectionKey, id: chat.id });
    try {
      await page.goto('/');
      await discovery.promise;
      await expect(composer(page)).toHaveAccessibleName('Loading…');
      await expect(send(page)).toBeDisabled();
      releaseDiscovery.release();
      await expect(composer(page)).toHaveAccessibleName('Message ' + name);
      await expect(pill(page, alpha)).toBeVisible();
      await composer(page).fill('Use the inherited workspace pair.');
      await send(page).click();
      await expect(page.locator('[data-chat-retry]')).toBeVisible();
      expect(worker.executions).toEqual([expectedTurn(chat.id, alpha, 'Use the inherited workspace pair.')]);
      expect(await page.evaluate(key => localStorage.getItem(key), overridePrefix + chat.id)).toBeNull();
    } finally {
      releaseDiscovery.release();
    }
  });

  test('D: profile discovery failure preserves CEO and a saved specialist override', async ({ page }) => {
    const worker = await fixture(page);
    const agent = await createSpecialist(page, 'AUD03-F2 overridden specialist', { engine: alpha.engine, default_model_id: latest.model_id });
    const specialist = await createChat(page, 'AUD03-F2 explicit override', agent.id);
    const ceo = await createChat(page, 'AUD03-F2 CEO');
    const override = JSON.stringify({ engine: beta.engine, model: beta.model_id });
    await page.addInitScript(({ selection, id, key, value }) => {
      localStorage.setItem(selection, JSON.stringify(id));
      localStorage.setItem(key, value);
    }, { selection: selectionKey, id: specialist.id, key: overridePrefix + specialist.id, value: override });
    await page.route('**/__agentos/call', route => (route.request().postDataJSON() as Rpc).method === 'profiles.list'
      ? route.fulfill(rejectProfiles) : route.fallback());

    await page.goto('/');
    await expect(failure(page)).toContainText(profileError);
    await expect(page.locator('[data-chathead-conversation]')).toHaveText('AUD03-F2 explicit override');
    await expect(pill(page, beta)).toBeVisible();
    await composer(page).fill('Use this conversation override.');
    await send(page).click();
    await expect(page.locator('[data-chat-retry]')).toBeVisible();
    await page.locator(`[data-sidebar-thread-row="${ceo.id}"]`).click();
    await expect(composer(page)).toHaveAccessibleName('Message Loqman');
    await expect(pill(page, alpha)).toBeVisible();
    await composer(page).fill('Use the CEO workspace pair.');
    await send(page).click();
    await expect(page.locator('[data-chat-retry]')).toBeVisible();
    expect(worker.executions).toEqual([
      expectedTurn(specialist.id, beta, 'Use this conversation override.'),
      { method: 'ceo.ask', params: { conversation_id: ceo.id, ...alpha, question: 'Use the CEO workspace pair.', request_id: expect.any(String) } },
    ]);
    expect(await page.evaluate(key => localStorage.getItem(key), overridePrefix + specialist.id)).toBe(override);
  });
});

test('a chat override writes no preferences and General shows the acknowledged default', async ({ page }) => {
  const worker = await fixture(page);
  await openHome(page);
  await openGeneral(page);
  await pick(page.locator('#ai-default-model'), alternate.model_id);
  await displayedDefault(page, alternate);
  await closeGeneral(page);

  await overrideChat(page, beta);
  await openGeneral(page);
  expect(worker.writes).toEqual([alternate]);
  expect(worker.saved()).toEqual(alternate);
  await displayedDefault(page, alternate);
});

test('a rejected save keeps the acknowledged default and the active selection', async ({ page }) => {
  const worker = await fixture(page);
  worker.failSave();
  await openHome(page);
  await overrideChat(page, latest);
  await openGeneral(page);
  await pick(page.locator('#ai-default-model'), alternate.model_id);
  await expect(defaults(page).getByRole('alert')).toContainText('The default could not be saved.');
  expect(worker.writes).toEqual([alternate]);
  expect(worker.saved()).toEqual(alpha);
  await displayedDefault(page, alpha);
  await closeGeneral(page);
  await expect(pill(page, latest)).toBeVisible();
});

test('two save intents made while a response is pending settle to the latest worker acknowledgement', async ({ page }) => {
  const worker = await fixture(page);
  await openHome(page);
  await openGeneral(page);
  const first = worker.holdSave();
  try {
    await pick(page.locator('#ai-default-model'), alternate.model_id);
    await first.received.promise;
    await pick(page.locator('#ai-default-model'), latest.model_id);
    await displayedDefault(page, alpha);
    await expect(defaults(page).getByRole('status')).toContainText(/saving/i);
    expect(worker.writes).toEqual([alternate]);
    expect(worker.saved()).toEqual(alpha);
  } finally {
    first.reply.release();
  }
  await displayedDefault(page, latest);
  expect(worker.writes).toEqual([alternate, latest]);
  expect(worker.saved()).toEqual(latest);
});

test('an acknowledged default survives reopening and is read back from worker preferences', async ({ page }) => {
  const worker = await fixture(page);
  await openHome(page);
  await openGeneral(page);
  await pick(page.locator('#ai-default-engine'), beta.engine);
  await displayedDefault(page, beta);
  await closeGeneral(page);
  await openGeneral(page);
  await displayedDefault(page, beta);
  await page.evaluate(() => {
    localStorage.removeItem('agentos.rusebot.engine.v1');
    localStorage.removeItem('agentos.rusebot.preferredModels.v1');
  });
  await page.reload();
  await openGeneral(page);
  await displayedDefault(page, beta);
  expect(worker.writes).toEqual([beta]);
  expect(worker.reads).toEqual([alpha, beta]);
});

test('UX-01: a pending provider choice cannot overwrite a newer default after Settings reopens', async ({ page }) => {
  const worker = await fixture(page);
  await openHome(page);
  await openGeneral(page);
  const oldCatalog = worker.holdCodexCatalog();
  try {
    await pick(defaults(page).locator('#ai-default-engine'), beta.engine);
    await oldCatalog.received.promise;
    await closeGeneral(page);
    await openGeneral(page);
    await pick(defaults(page).locator('#ai-default-model'), alternate.model_id);
    await displayedDefault(page, alternate);
    expect(worker.saved()).toEqual(alternate);

    const catalogResponse = page.waitForResponse(response => {
      const request = response.request();
      if (!request.url().endsWith('/__agentos/call') || request.method() !== 'POST') return false;
      const call = request.postDataJSON() as Rpc;
      return call.method === 'engines.models' && call.params.engine_id === beta.engine;
    });
    oldCatalog.reply.release();
    expect(await (await catalogResponse).finished()).toBeNull();

    // Choosing B in Chat confirms its catalog is now available without saving a default.
    await closeGeneral(page);
    await overrideChat(page, beta);
    await openGeneral(page);
    expect(worker.saved()).toEqual(alternate);
    await displayedDefault(page, alternate);
    await page.reload();
    await openGeneral(page);
    await displayedDefault(page, alternate);
    expect(worker.reads.at(-1)).toEqual(alternate);
    expect(worker.writes).toEqual([alternate]);
  } finally {
    oldCatalog.reply.release();
  }
});

test('agent labels use the worker default for inheritance and keep explicit settings', async ({ page }) => {
  const worker = await fixture(page);
  const inherited = 'FE-04 Inherited specialist';
  const explicit = 'FE-04 Explicit specialist';
  const inheritedProfile = await rpc<{ id: string }>(page, 'profiles.create', {
    name: inherited, role_type: 'custom', instructions: 'Inherit the worker engine.', default_model_id: 'removed-model',
  });
  const explicitProfile = await rpc<{ id: string }>(page, 'profiles.create', {
    name: explicit, role_type: 'custom', instructions: 'Use an explicit engine and model.',
    engine: alpha.engine, default_model_id: latest.model_id,
  });
  await openHome(page);
  await overrideChat(page, beta);
  await page.getByRole('button', { name: 'Team', exact: true }).click();
  const panel = page.locator('[data-agent-panel]');
  await page.locator(`[data-bot-id="${inheritedProfile.id}"]`).click();
  await expect(panel).toContainText('Default · ' + alpha.model_id);
  await panel.getByRole('button', { name: 'Close details' }).click();
  await page.locator(`[data-bot-id="${explicitProfile.id}"]`).click();
  await expect(panel).toContainText(latest.model_id);
  await expect(panel).not.toContainText('Default · ' + latest.model_id);
  expect(worker.writes).toEqual([]);
});

test('specialist Retry keeps its original execution settings after a default save and a chat override', async ({ page }) => {
  const worker = await fixture(page);
  const agent = await rpc<{ id: string }>(page, 'profiles.create', {
    name: 'FE-04 Retry specialist', role_type: 'custom', instructions: 'Reply briefly.',
    engine: alpha.engine, default_model_id: latest.model_id,
  });
  const chat = await rpc<{ id: string; title: string }>(page, 'conversations.create', { title: 'FE-04 Retry chat', profile_id: agent.id });
  await page.addInitScript(id => localStorage.setItem('agentos.rusebot.selected.v1', JSON.stringify(id)), chat.id);
  await page.goto('/?ui=rusebot');
  await expect(pill(page, latest)).toBeVisible();
  await expect(page.locator('[data-chathead-conversation]')).toHaveText(chat.title);
  await overrideChat(page, alternate);
  const composer = page.locator('[data-tour="composer"]').getByRole('textbox');
  await composer.fill('Use the original chat execution settings');
  await page.getByRole('button', { name: 'Send', exact: true }).click();
  const retry = page.locator('[data-chat-retry]');
  await expect(retry).toBeVisible();
  expect(worker.executions).toEqual([{
    method: 'runs.start_turn',
    params: { conversation_id: chat.id, ...alternate, prompt: 'Use the original chat execution settings', request_key: expect.any(String) },
  }]);

  await openGeneral(page);
  await pick(page.locator('#ai-default-model'), latest.model_id);
  await displayedDefault(page, latest);
  await closeGeneral(page);
  await expect(pill(page, alternate)).toBeVisible();
  await overrideChat(page, beta);
  await retry.click();
  await expect.poll(() => worker.executions.length).toBe(2);
  expect(worker.executions[1]).toEqual(worker.executions[0]);
  expect(worker.writes).toEqual([latest]);
  expect(worker.saved()).toEqual(latest);
});
