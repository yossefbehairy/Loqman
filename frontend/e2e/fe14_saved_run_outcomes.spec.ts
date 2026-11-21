import { expect, test, type Page, type Route } from '@playwright/test';

type Profile = { id: string; name: string };
type Chat = { id: string; title: string };
type ConvMessage = { seq: number; role: 'user' | 'assistant'; content: string };
type RunSnapshot = {
  id: string;
  conversation_id: string;
  status: 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled' | 'interrupted';
  user_message_seq: number | null;
  assistant_message_seq: number | null;
  error: string | null;
};
type Rpc = { method: string; params: Record<string, unknown> };
type Intercept = (route: Route, call: Rpc) => Promise<void> | undefined;

const api = '**/__agentos/call';
const schemaFailure = 'Output did not match the profile schema.';
const restartError = 'Run interrupted by server restart or shutdown; it was not re-executed.';
const mutation = new Set(['runs.start_turn', 'runs.steer', 'conversations.create', 'ceo.ask']);
const composer = (page: Page) => page.locator('[data-tour="composer"]').getByRole('textbox');
const transcript = (page: Page) => page.getByRole('log');
const row = (page: Page, seq: number) => transcript(page).locator('[data-mid="' + seq + '"]');
const marker = '[data-saved-run-outcome]';
const stop = (page: Page) => page.getByRole('button', { name: 'Stop', exact: true });

function gate() {
  let release!: () => void;
  const promise = new Promise<void>(resolve => { release = resolve; });
  return { promise, release };
}

async function rpc<T>(page: Page, method: string, params: object) {
  const response = await page.request.post('/__agentos/call', { data: { method, params } });
  expect(response.ok(), method + ' on the isolated mock worker').toBe(true);
  return (await response.json() as { result: T }).result;
}

async function seed(page: Page, name: string, rejectsOutput = false) {
  const profile = await rpc<Profile>(page, 'profiles.create', {
    name, role_type: 'custom', instructions: 'Reply briefly.',
    ...(rejectsOutput ? {
      output_schema_json: JSON.stringify({ type: 'object', required: ['fe14_required_output'] }),
    } : {}),
  });
  const chat = await rpc<Chat>(page, 'conversations.create', {
    title: name + ' transcript', profile_id: profile.id,
  });
  return { profile, chat };
}

const start = (page: Page, chat: Chat, prompt: string) => rpc<RunSnapshot>(page, 'runs.start_turn', {
  conversation_id: chat.id, engine: 'mock', model_id: 'deepseek-v4.1-flash-free', prompt,
});

const get = (page: Page, id: string) => rpc<RunSnapshot>(page, 'runs.get', { run_id: id });
const history = (page: Page, chat: Chat) =>
  rpc<{ messages: ConvMessage[] }>(page, 'conversations.messages', { conversation_id: chat.id });

async function settle(page: Page, chat: Chat, prompt: string, status: 'failed' | 'succeeded') {
  const started = await start(page, chat, prompt);
  await expect.poll(async () => (await get(page, started.id)).status, { timeout: 15_000 }).toBe(status);
  const snapshot = await get(page, started.id);
  expect(snapshot).toMatchObject({ conversation_id: chat.id, user_message_seq: expect.any(Number) });
  expect((await history(page, chat)).messages).toContainEqual(expect.objectContaining({
    seq: snapshot.user_message_seq, role: 'user', content: prompt,
  }));
  return { ...snapshot, user_message_seq: snapshot.user_message_seq! };
}

async function trace(page: Page, intercept?: Intercept) {
  const mutations: Rpc[] = [];
  await page.route(api, route => {
    const call = route.request().postDataJSON() as Rpc;
    if (mutation.has(call.method)) mutations.push(call);
    return intercept?.(route, call) ?? route.continue();
  });
  return mutations;
}

async function reopen(page: Page, chat: Chat, draft?: string) {
  await page.addInitScript(({ id, draft }) => {
    localStorage.setItem('agentos.rusebot.selected.v1', JSON.stringify(id));
    localStorage.setItem('agentos.rusebot.drafts.v1', JSON.stringify(draft === undefined ? {} : { [id]: draft }));
  }, { id: chat.id, draft });
  await page.goto('/?ui=rusebot');
  await expect(page.locator('[data-chathead-conversation]')).toHaveText(chat.title);
}

function overrideRun(snapshot: RunSnapshot, changes: Partial<RunSnapshot>): Intercept {
  return (route, call) => {
    if (call.method === 'runs.get' && call.params.run_id === snapshot.id) return route.fulfill({ json: { result: { ...snapshot, ...changes } } });
    if (call.method !== 'runs.list' || call.params.conversation_id !== snapshot.conversation_id) return;
    return (async () => {
      const response = await route.fetch();
      expect(response.ok()).toBe(true);
      const { result } = await response.json() as { result: { runs: RunSnapshot[] } };
      return route.fulfill({ json: { result: { runs: result.runs.map(run => run.id === snapshot.id ? { ...run, ...changes } : run) } } });
    })();
  };
}

async function expectOutcome(page: Page, seq: number, status: 'failed' | 'interrupted', detail?: string) {
  const outcome = row(page, seq).locator('[data-saved-run-outcome="' + status + '"]');
  await expect(outcome).toBeVisible();
  await expect(outcome).toContainText(status === 'failed' ? 'Run failed' : 'Run interrupted');
  if (detail) await expect(outcome).toContainText(detail);
}

async function noRecovery(page: Page, mutations: Rpc[]) {
  await expect(page.locator('[data-chat-retry]')).toHaveCount(0);
  expect(mutations).toEqual([]);
}

test('reopening a persisted failed specialist turn shows its status and worker reason on the original prompt', async ({ page }) => {
  const { chat } = await seed(page, 'FE-14 schema failure', true);
  const failed = await settle(page, chat, 'This saved prompt failed its output schema.', 'failed');
  expect(failed).toMatchObject({ error: schemaFailure, assistant_message_seq: null });
  expect((await history(page, chat)).messages.map(message => message.role)).toEqual(['user']);
  const calls = await trace(page);

  await reopen(page, chat);
  await expectOutcome(page, failed.user_message_seq, 'failed', schemaFailure);
  await expect(transcript(page).locator('[data-role="assistant"]')).toHaveCount(0);
  await expect(page.locator('[data-working]')).toHaveCount(0);
  await expect(stop(page)).toHaveCount(0);
  await noRecovery(page, calls);
  const { runs } = await rpc<{ runs: RunSnapshot[] }>(page, 'runs.list', { conversation_id: chat.id });
  expect(runs.map(run => [run.id, run.status])).toEqual([[failed.id, 'failed']]);
});

test('a worker restart snapshot displays Interrupted without work, Stop, or Retry', async ({ page }) => {
  const { chat } = await seed(page, 'FE-14 interrupted', true);
  const fixture = await settle(page, chat, 'The persisted prompt before the simulated restart.', 'failed');
  const calls = await trace(page, overrideRun(fixture, { status: 'interrupted', error: restartError }));
  await reopen(page, chat);

  await expectOutcome(page, fixture.user_message_seq, 'interrupted', restartError);
  await expect(transcript(page).locator('[data-role="assistant"]')).toHaveCount(0);
  await expect(page.locator('[data-working]')).toHaveCount(0);
  await expect(stop(page)).toHaveCount(0);
  await noRecovery(page, calls);
});

test('matching prompt text does not attach failure to a different user message or expose raw diagnostics', async ({ page }) => {
  const { profile, chat } = await seed(page, 'FE-14 repeated prompt', true);
  const prompt = 'Identical prompt in separate accepted turns.';
  const failed = await settle(page, chat, prompt, 'failed');
  await rpc(page, 'profiles.update', { profile_id: profile.id, output_schema_json: '' });
  const succeeded = await settle(page, chat, prompt, 'succeeded');
  const unsubmitted = await rpc<ConvMessage>(page, 'conversations.add_message', {
    conversation_id: chat.id, role: 'user', content: 'Saved text with no associated run.',
  });
  expect(failed.user_message_seq).not.toBe(succeeded.user_message_seq);
  expect(succeeded.assistant_message_seq).not.toBeNull();
  const diagnostic = 'Local trace at D:\\private\\run.log, token=FE14_TEST_SECRET';
  const calls = await trace(page, overrideRun(failed, { error: diagnostic }));

  await reopen(page, chat);
  await expect(transcript(page).getByText(prompt, { exact: true })).toHaveCount(2);
  await expectOutcome(page, failed.user_message_seq, 'failed');
  await expect(row(page, succeeded.user_message_seq).locator(marker)).toHaveCount(0);
  await expect(row(page, succeeded.assistant_message_seq!).locator('[data-role="assistant"]')).toBeVisible();
  await expect(row(page, unsubmitted.seq).locator(marker)).toHaveCount(0);
  await expect(transcript(page).locator(marker)).toHaveCount(1);
  await expect(transcript(page)).not.toContainText('FE14_TEST_SECRET');
  await noRecovery(page, calls);
});

test('delayed run history from a previous chat never appears in a healthy empty conversation', async ({ page }) => {
  const from = await seed(page, 'FE-14 delayed history', true);
  const to = await seed(page, 'FE-14 empty destination');
  const failed = await settle(page, from.chat, 'A failing prompt in the previous chat.', 'failed');
  const requested = gate();
  const release = gate();
  const delivered = gate();
  let held = false;
  const calls = await trace(page, (route, call) => {
    if (held || call.method !== 'runs.list' || call.params.conversation_id !== from.chat.id || call.params.limit !== 30) return;
    held = true;
    return (async () => {
      const response = await route.fetch();
      requested.release();
      await release.promise;
      await route.fulfill({ response });
      delivered.release();
    })();
  });

  try {
    await reopen(page, from.chat);
    await requested.promise;
    await expect(row(page, failed.user_message_seq)).toBeVisible();
    await page.locator('[data-sidebar-thread-row="' + to.chat.id + '"]').click();
    await expect(page.locator('[data-chathead-conversation]')).toHaveText(to.chat.title);
    await expect(transcript(page)).toContainText('Send a message to start the conversation.');
  } finally {
    release.release();
  }
  await delivered.promise;
  // Let the response and its React updates settle before checking the new view.
  await page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
  await expect(transcript(page).locator(marker)).toHaveCount(0);
  await expect(transcript(page)).not.toContainText(schemaFailure);
  await expect(transcript(page)).toContainText('Send a message to start the conversation.');

  await page.locator('[data-sidebar-thread-row="' + from.chat.id + '"]').click();
  await expectOutcome(page, failed.user_message_seq, 'failed', schemaFailure);
  await noRecovery(page, calls);
});

test('a historical failure preserves the current draft and a newer accepted run', async ({ page }) => {
  const { profile, chat } = await seed(page, 'FE-14 newer active turn', true);
  const old = await settle(page, chat, 'The earlier failed user message.', 'failed');
  await rpc(page, 'profiles.update', { profile_id: profile.id, output_schema_json: '' });
  const draft = 'A separate unsent draft.';
  const active = await start(page, chat, 'The newer accepted turn [mock:delay=30000]');
  try {
    await expect.poll(async () => (await get(page, active.id)).status).toBe('running');
    const calls = await trace(page);
    await reopen(page, chat, draft);
    await expect(stop(page)).toBeVisible();
    await expect(page.getByTestId('transcript-announcer')).toHaveText('Working');
    await expectOutcome(page, old.user_message_seq, 'failed', schemaFailure);
    await expect(row(page, active.user_message_seq!).locator(marker)).toHaveCount(0);
    await expect(composer(page)).toHaveValue(draft);
    await expect(stop(page)).toBeEnabled();
    await expect(page.getByRole('button', { name: 'Send', exact: true })).toHaveCount(0);
    await noRecovery(page, calls);
    const { runs } = await rpc<{ runs: RunSnapshot[] }>(page, 'runs.list', { conversation_id: chat.id });
    expect(runs.map(run => [run.id, run.status])).toEqual([[old.id, 'failed'], [active.id, 'running']]);
  } finally {
    const final = await get(page, active.id);
    if (final.status === 'queued' || final.status === 'running') await rpc(page, 'runs.cancel', { run_id: active.id });
  }
});
