import { expect, test, type Page } from '@playwright/test';

type Rpc = { method: string; params: Record<string, unknown> };
type Chat = { id: string; title: string; profile_id: string | null };
type Model = { id: string; name: string };
type Discovery = 'available' | 'missing' | 'engine failure' | 'catalog failure';

const prompt = 'Keep this original specialist assignment.';
const pending = 'Checking the original engine and model';
const unavailable = 'The original engine and model are currently unavailable';
const failed = 'Could not check the original engine and model';
const retry = (page: Page) => page.locator('[data-chat-retry]');
const composer = (page: Page) => page.locator('[data-tour="composer"]').getByRole('textbox');

function gate() {
  let release!: () => void;
  const promise = new Promise<void>(resolve => { release = resolve; });
  return { promise, release };
}

async function rpc<T>(page: Page, method: string, params: object = {}) {
  const response = await page.request.post('/__agentos/call', { data: { method, params } });
  expect(response.ok(), method + ' on the isolated mock worker').toBe(true);
  return (await response.json() as { result: T }).result;
}

async function recoverableChat(page: Page, discovery: Discovery, legacy = false) {
  const profile = await rpc<{ id: string; name: string }>(page, 'profiles.create', {
    name: 'UX-06 specialist', role_type: 'custom', instructions: 'Follow the specialist contract.',
  });
  const chat = await rpc<Chat>(page, 'conversations.create', {
    title: 'UX-06 saved task', ...(legacy ? {} : { profile_id: profile.id }),
  });
  const preference = await rpc<object>(page, 'preferences.get');
  const catalog = await rpc<{ models: Model[] }>(page, 'models.free');
  const enginesArrived = gate();
  const enginesReleased = gate();
  const modelsArrived = gate();
  const modelsReleased = gate();
  const calls: Rpc[] = [];
  const executions = () => calls.filter(call => ['conversations.bind_profile', 'runs.start_turn'].includes(call.method));
  let reloading = false;
  let original: Rpc | undefined;
  await page.route('**/__agentos/call', async route => {
    const call = route.request().postDataJSON() as Rpc;
    calls.push(call);
    const reject = (status: number, detail: string) => route.fulfill({ status, json: { status, detail } });
    if (reloading && call.method === 'engines.status' && !call.params.force) {
      enginesArrived.release();
      await enginesReleased.promise;
      if (discovery === 'engine failure') return reject(503, 'Provider discovery fixture is offline.');
    }
    if (reloading && call.method === 'models.free') {
      modelsArrived.release();
      await modelsReleased.promise;
      if (discovery === 'catalog failure') return reject(503, 'Model catalog fixture is offline.');
      if (discovery === 'missing') {
        return route.fulfill({ json: { result: { models: catalog.models.filter(model => model.id !== original?.params.model_id) } } });
      }
    }
    if (!reloading && call.method === 'conversations.bind_profile') return reject(500, 'Legacy binding temporarily unavailable.');
    if (call.method === 'runs.start_turn') {
      expect(call.params.engine).toBe('mock');
      if (!reloading) {
        original = call;
        return reject(502, 'Submission failed before acceptance.');
      }
    }
    return route.continue();
  });
  await page.addInitScript(({ chat, profile }) => {
    localStorage.setItem('agentos.rusebot.selected.v1', JSON.stringify(chat.id));
    localStorage.setItem('agentos.rusebot.convAgents.v1', JSON.stringify({ [chat.id]: profile.id }));
  }, { chat, profile });

  async function startReload() {
    await page.goto('/');
    await expect(composer(page)).toHaveAccessibleName('Message ' + profile.name);
    await composer(page).fill(prompt);
    await page.getByRole('button', { name: 'Send', exact: true }).click();
    await expect(retry(page)).toBeVisible();
    if (!original) throw new Error('Expected the initial specialist submission');
    const submitted = original;
    const record = () => page.evaluate(key => localStorage.getItem('agentos.rusebot.submission.v1.' + key), String(submitted.params.request_key));
    const saved = await record();
    expect(JSON.parse(saved ?? 'null')).toMatchObject({
      cid: chat.id, agentId: profile.id, prompt, requestId: submitted.params.request_key,
      execution: { engine: 'mock', model: submitted.params.model_id },
    });
    reloading = true;
    await page.reload();
    await enginesArrived.promise;
    await expect(retry(page)).toBeVisible();
    return { submitted, record, saved };
  }

  return { chat, profile, preference, calls, executions, startReload, enginesReleased, modelsArrived, modelsReleased };
}

for (const legacy of [false, true]) {
  test(legacy ? 'UX-06: legacy Retry waits for both discovery steps then binds once' : 'UX-06: startup Retry waits for the original pair and keeps saved preferences', async ({ page }) => {
    const fixture = await recoverableChat(page, 'available', legacy);
    try {
      const { submitted, record, saved } = await fixture.startReload();
      const before = fixture.executions();
      await expect(retry(page)).toBeDisabled();
      await expect(page.getByRole('status').filter({ hasText: pending })).toBeVisible();
      await expect(page.getByRole('alert').filter({ hasText: unavailable })).toHaveCount(0);
      expect(fixture.executions()).toEqual(before);
      expect(await record()).toBe(saved);

      fixture.enginesReleased.release();
      await fixture.modelsArrived.promise;
      await expect(retry(page)).toBeDisabled();
      await expect(page.getByRole('status').filter({ hasText: pending })).toBeVisible();
      expect(fixture.executions()).toEqual(before);
      fixture.modelsReleased.release();

      await expect(retry(page)).toBeEnabled();
      expect(fixture.executions()).toEqual(before);
      await retry(page).click();
      await expect.poll(record).toBeNull();
      const binding = { method: 'conversations.bind_profile', params: { conversation_id: fixture.chat.id, profile_id: fixture.profile.id } };
      expect(fixture.executions()).toEqual([...before, ...(legacy ? [binding] : []), submitted]);
      const { conversations } = await rpc<{ conversations: Chat[] }>(page, 'conversations.list');
      expect(conversations.find(chat => chat.id === fixture.chat.id)?.profile_id).toBe(fixture.profile.id);
      const { runs } = await rpc<{ runs: { engine: string; model_id: string }[] }>(page, 'runs.list', { conversation_id: fixture.chat.id });
      expect(runs).toEqual([expect.objectContaining({ engine: 'mock', model_id: submitted.params.model_id })]);
      expect(fixture.calls.filter(call => call.method === 'preferences.set_default')).toEqual([]);
      expect(await rpc<object>(page, 'preferences.get')).toEqual(fixture.preference);
    } finally {
      fixture.enginesReleased.release();
      fixture.modelsReleased.release();
    }
  });
}

for (const discovery of ['missing', 'engine failure', 'catalog failure'] as const) {
  test('UX-06: restored Retry reports ' + discovery + ' and preserves recovery', async ({ page }) => {
    const fixture = await recoverableChat(page, discovery);
    try {
      const { record, saved } = await fixture.startReload();
      const before = fixture.executions();
      fixture.enginesReleased.release();
      fixture.modelsReleased.release();
      const notice = page.getByRole('alert').filter({ hasText: discovery === 'missing' ? unavailable : failed });
      await expect(notice).toBeVisible();
      await expect(notice).toContainText('Settings');
      await expect(page.getByRole('status').filter({ hasText: pending })).toHaveCount(0);
      if (discovery !== 'missing') await expect(notice).not.toContainText(unavailable);
      await expect(retry(page)).toBeVisible();
      await expect(composer(page)).toHaveValue(prompt);
      expect(fixture.executions()).toEqual(before);
      expect(await record()).toBe(saved);
    } finally {
      fixture.enginesReleased.release();
      fixture.modelsReleased.release();
    }
  });
}
