import { expect, test, type Locator, type Page } from '@playwright/test';

type Payload = Record<string, unknown> & { engine?: string; default_model_id?: string };
type Profile = { id: string; engine: string | null; default_model_id: string };
type Save = { payload: Payload; result?: Profile };
const original = 'fixture-opencode-original';
const replacement = 'fixture-opencode-replacement';
const codexModel = 'fixture-codex-available';

function deferred() {
  let release!: () => void;
  const promise = new Promise<void>(resolve => { release = resolve; });
  return { promise, release };
}

async function fixture(page: Page, options: { codexFailure?: boolean; inherit?: boolean } = {}) {
  let removed = false;
  const requested = deferred();
  const release = deferred();
  const saves: Save[] = [];
  const preferenceWrites: Payload[] = [];
  await page.route('**/__agentos/call', async route => {
    const { method, params } = route.request().postDataJSON() as { method: string; params: Payload };
    const reply = (result: unknown) => route.fulfill({ json: { result } });
    if (method === 'engines.capabilities') {
      return reply(options.inherit ? {} : Object.fromEntries(['mock', 'opencode', 'codex'].map(id => [id, { conversation_runs: true }])));
    }
    if (method === 'engines.status') {
      return reply({ engines: ['opencode', 'codex'].map(engine_id => ({
        engine_id, title: engine_id, state: 'ready', install_state: 'installed', auth_state: 'authenticated',
      })) });
    }
    if (method === 'engines.models') {
      if (params.engine_id === 'codex') {
        requested.release();
        await release.promise;
        if (options.codexFailure) return route.fulfill({ status: 503, json: { detail: 'Catalog unavailable' } });
        return reply({ models: [{ id: codexModel, name: codexModel }] });
      }
      const ids = removed ? [replacement] : [original, replacement];
      return reply({ models: ids.map(id => ({ id, name: id })) });
    }
    if (method === 'profiles.create') {
      const save: Save = { payload: params };
      saves.push(save);
      const response = await route.fetch();
      if (response.ok()) save.result = (await response.json() as { result: Profile }).result;
      return route.fulfill({ response });
    }
    if (method === 'preferences.set_default') preferenceWrites.push(params);
    return route.continue();
  });
  return { saves, preferenceWrites, requested, release, removeModel: () => { removed = true; } };
}

async function pick(trigger: Locator, value: string) {
  const page = trigger.page();
  await trigger.click();
  await page.getByRole('option').and(page.locator('[data-value="' + value + '"]')).click();
  await expect(page.getByRole('listbox')).toHaveCount(0);
}

const dialog = (page: Page) => page.getByRole('dialog', { name: 'New agent', exact: true });
const engine = (page: Page) => page.locator('#na-engine');
const model = (page: Page) => page.locator('#na-model');
const createButton = (page: Page) => dialog(page).getByRole('button', { name: 'Create agent', exact: true });

async function openForm(page: Page, name: string) {
  await page.locator('[data-sidebar-new]').click();
  await page.locator('[data-new-agent]').click();
  await expect(dialog(page)).toBeVisible();
  await page.locator('#na-name').fill(name);
  await page.locator('#na-role').fill('Catalog fixture');
  await page.locator('#na-instructions').fill('Use the selected execution settings.');
}

async function explicitlySelectOriginal(page: Page) {
  await pick(engine(page), 'opencode');
  await expect(engine(page)).toHaveAttribute('data-value', 'opencode');
  await expect(model(page)).toHaveAttribute('data-value', original);
  await expect(model(page)).toBeEnabled();
}

async function persisted(page: Page, save: Save) {
  await expect.poll(() => save.result?.id, { message: 'Wait for the isolated worker to accept profiles.create' }).toBeTruthy();
  const request = await page.request.post('/__agentos/call', {
    data: { method: 'profiles.get', params: { profile_id: save.result!.id } },
  });
  expect(request.ok()).toBe(true);
  const { result } = await request.json() as { result: Profile };
  return { payload: save.payload, stored: { engine: result.engine, default_model_id: result.default_model_id } };
}

async function attemptCreate(page: Page, saves: Save[], name: string) {
  if (await createButton(page).isEnabled()) {
    await createButton(page).click();
    // Let the form event and its request reach Playwright. A guarded submit may send nothing.
    await page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
  }
  const save = saves.find(entry => entry.payload.name === name);
  return save ? persisted(page, save) : null;
}

async function preferences(page: Page) {
  const response = await page.request.post('/__agentos/call', { data: { method: 'preferences.get', params: {} } });
  expect(response.ok()).toBe(true);
  return (await response.json() as { result: unknown }).result;
}

test('A: Create while switching to a delayed catalog never persists the previous explicit pair', async ({ page }, info) => {
  const worker = await fixture(page);
  const savedDefault = await preferences(page);
  await page.goto('/?ui=rusebot');
  const chatChoice = page.locator('[data-chathead-pill] [title^="mock · "]');
  await expect(chatChoice).toBeVisible();
  const originalChatChoice = await chatChoice.getAttribute('title');
  const earlyName = 'FE-06 A pending catalog';
  await openForm(page, earlyName);
  await explicitlySelectOriginal(page);
  let early: Awaited<ReturnType<typeof attemptCreate>>;
  try {
    await pick(engine(page), 'codex');
    await worker.requested.promise;
    await expect(engine(page)).toHaveAttribute('data-value', 'codex');
    await expect(dialog(page).getByRole('status')).toContainText('Wait for');
    early = await attemptCreate(page, worker.saves, earlyName);
  } finally {
    worker.release.release();
  }

  // If the first submission closed the dialog, repeat the intended choice with the catalog ready.
  const readyName = 'FE-06 A ready catalog';
  if (early) await expect(dialog(page)).toHaveCount(0);
  if (!(await dialog(page).isVisible())) {
    await openForm(page, readyName);
    await pick(engine(page), 'codex');
  } else {
    await page.locator('#na-name').fill(readyName);
  }
  await expect(engine(page)).toHaveAttribute('data-value', 'codex');
  await expect(model(page)).toHaveAttribute('data-value', codexModel);
  await createButton(page).click();
  await expect.poll(() => worker.saves.find(entry => entry.payload.name === readyName)?.result?.id).toBeTruthy();
  const ready = await persisted(page, worker.saves.find(entry => entry.payload.name === readyName)!);
  await info.attach('profile-create-evidence', { body: JSON.stringify({ early, ready }, null, 2), contentType: 'application/json' });
  expect(ready.payload).toMatchObject({ engine: 'codex', default_model_id: codexModel });
  expect(ready.stored).toEqual({ engine: 'codex', default_model_id: codexModel });
  if (early) {
    if (early.payload.engine === undefined && early.payload.default_model_id === undefined) {
      expect(early.stored.engine).toBeNull();
    } else {
      expect(early, 'The previous explicit engine/model must not be saved after choosing Codex').toMatchObject({
        payload: { engine: 'codex', default_model_id: codexModel },
        stored: { engine: 'codex', default_model_id: codexModel },
      });
    }
  }
  expect(early).toBeNull();
  await expect(chatChoice).toHaveAttribute('title', originalChatChoice!);
  expect(worker.preferenceWrites).toEqual([]);
  expect(await preferences(page)).toEqual(savedDefault);
});

test('B: Create after a catalog refresh never persists the removed explicit model', async ({ page }, info) => {
  const worker = await fixture(page);
  await page.goto('/?ui=rusebot');
  const name = 'FE-06 B removed model';
  await openForm(page, name);
  await explicitlySelectOriginal(page);
  worker.removeModel();
  const refreshed = await page.evaluate(async () => {
    const path = '/src/rusebot/state/useProviders.ts';
    const store = await import(/* @vite-ignore */ path);
    return (await store.loadProviderModels('opencode', true)).map((model: { id: string }) => model.id);
  });
  expect(refreshed).toEqual([replacement]);
  const saved = await attemptCreate(page, worker.saves, name);
  await info.attach('profile-create-evidence', { body: JSON.stringify({ refreshed, saved }, null, 2), contentType: 'application/json' });
  if (saved) {
    expect(saved.payload.default_model_id, 'Removed model sent in profiles.create').not.toBe(original);
    expect(saved.stored.default_model_id, 'Removed model persisted by the worker').not.toBe(original);
  }
  expect(saved).toBeNull();
  await expect(dialog(page).getByRole('status')).toContainText('Choose an available provider and model');
  // Exercise submit even when the visible button is disabled.
  await dialog(page).locator('form').dispatchEvent('submit', { bubbles: true, cancelable: true });
  await pick(model(page), replacement);
  await createButton(page).click();
  await expect.poll(() => worker.saves.length).toBe(1);
  const recovered = await persisted(page, worker.saves[0]);
  await info.attach('replacement-profile', { body: JSON.stringify(recovered, null, 2), contentType: 'application/json' });
  expect(recovered.payload).toMatchObject({ engine: 'opencode', default_model_id: replacement });
  expect(recovered.stored).toEqual({ engine: 'opencode', default_model_id: replacement });
});

test('a failed engine catalog keeps the intended engine and blocks Create', async ({ page }) => {
  const worker = await fixture(page, { codexFailure: true });
  await page.goto('/?ui=rusebot');
  const name = 'FE-06 failed catalog';
  await openForm(page, name);
  await explicitlySelectOriginal(page);
  try {
    await pick(engine(page), 'codex');
    await worker.requested.promise;
  } finally {
    worker.release.release();
  }
  await expect(dialog(page).getByRole('status')).toContainText('Could not load models');
  await expect(engine(page)).toHaveAttribute('data-value', 'codex');
  await expect(model(page)).toHaveAttribute('data-value', '');
  await expect(createButton(page)).toBeDisabled();
  expect(await attemptCreate(page, worker.saves, name)).toBeNull();
  expect(worker.saves).toEqual([]);
});

for (const inherit of [true, false]) {
  const mode = inherit ? 'worker defaults' : 'the supported mock model';
  test(`Create preserves ${mode}`, async ({ page }, info) => {
    const worker = await fixture(page, { inherit });
    await page.goto('/?ui=rusebot');
    await openForm(page, 'FE-06 ' + mode);
    if (inherit) {
      await expect(engine(page)).toContainText('No providers found');
    } else {
      await expect(engine(page)).toHaveAttribute('data-value', 'mock');
      await expect(model(page)).toBeEnabled();
    }
    const visibleModel = await model(page).getAttribute('data-value');
    await createButton(page).click();
    await expect.poll(() => worker.saves.length).toBe(1);
    const saved = await persisted(page, worker.saves[0]);
    await info.attach('profile-create-evidence', { body: JSON.stringify(saved, null, 2), contentType: 'application/json' });
    expect(saved.payload).not.toHaveProperty('engine');
    expect(saved.stored.engine).toBeNull();
    if (inherit) {
      expect(saved.payload).not.toHaveProperty('default_model_id');
      expect(saved.stored.default_model_id).toBeTruthy();
    } else {
      expect(visibleModel).toBeTruthy();
      expect(saved.payload.default_model_id).toBe(visibleModel);
      expect(saved.stored.default_model_id).toBe(visibleModel);
    }
  });
}
