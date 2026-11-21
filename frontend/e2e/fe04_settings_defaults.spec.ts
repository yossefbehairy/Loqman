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
  await openHome(page);
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
