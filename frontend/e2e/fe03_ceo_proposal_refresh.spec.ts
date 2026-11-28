import { expect, test, type Page, type Request, type Route } from '@playwright/test';

type Action = { id: string; kind: string; summary: string; status?: string };
type Answer = { actions_proposed: Action[]; agents_created: Array<{ id: string; name: string }> };
type Chat = { id: string; title: string };
type Rpc = { method: string; params: Record<string, unknown> };
type Read = { request: Request; snapshot: Action[]; failed?: boolean };

const api = '**/__agentos/call';
const proposalQuestion = 'Update the employee speed';
const selectedKey = 'agentos.rusebot.selected.v1';
const nav = (page: Page, view: string) => page.locator('[data-sidebar-nav="' + view + '"]');
const badge = (page: Page) => page.locator('[data-needs-you]');
const results = (page: Page) => page.locator('main[data-view="results"]');
const cards = (page: Page) => results(page).locator('[data-approval-card="manager"]');
const banner = (page: Page) => page.locator('[data-load-error]');
const ownedActions = new WeakMap<Page, Set<string>>();

function gate() {
  let release!: () => void;
  const promise = new Promise<void>(resolve => { release = resolve; });
  return { promise, release };
}

async function rpc<T>(page: Page, method: string, params: object = {}): Promise<T> {
  const reply = await page.request.post('/__agentos/call', { data: { method, params } });
  expect(reply.ok(), method + ' on the isolated worker').toBe(true);
  return (await reply.json() as { result: T }).result;
}

async function ensurePlanner(page: Page) {
  const { profiles } = await rpc<{ profiles: Array<{ id: string }> }>(page, 'profiles.list');
  if (!profiles.some(profile => profile.id === 'planner')) {
    await rpc(page, 'profiles.create', { id: 'planner', name: 'Planner', role_type: 'planner', instructions: 'Plan work.' });
  }
}

async function makeProposal(page: Page) {
  const chat = await rpc<Chat>(page, 'conversations.create', { title: 'FE-03 proposal seed' });
  const answer = await rpc<Answer>(page, 'ceo.ask', {
    question: proposalQuestion, engine: 'mock', conversation_id: chat.id,
  });
  expect(answer.agents_created).toEqual([]);
  expect(answer.actions_proposed).toHaveLength(1);
  return answer.actions_proposed[0];
}

const failList = (route: Route, detail: string) => route.fulfill({ status: 503, json: { status: 503, detail } });

async function fixture(page: Page, own: Action[] = []) {
  let nextRead: { entered: ReturnType<typeof gate>; release: ReturnType<typeof gate>; error?: string } | undefined;
  let holdAsk: ReturnType<typeof gate> | undefined;
  const ids = new Set(own.map(action => action.id));
  ownedActions.set(page, ids);
  const reads: Read[] = [];
  const calls: Rpc[] = [];
  const answers: Answer[] = [];
  const asks: Rpc[] = [];
  await page.route(api, async route => {
    const call = route.request().postDataJSON() as Rpc;
    calls.push(call);
    if (call.method === 'ceo.ask') {
      asks.push(call);
      if (holdAsk) await holdAsk.promise;
      // Every browser ask must stay on the isolated mock engine.
      expect(call.params.engine).toBe('mock');
      const reply = await route.fetch();
      expect(reply.ok(), 'browser CEO ask accepted by mock worker').toBe(true);
      const { result } = await reply.json() as { result: Answer };
      for (const action of result.actions_proposed) ids.add(action.id);
      answers.push(result);
      return route.fulfill({ response: reply });
    }
    if (call.method !== 'ceo.actions' || call.params.status !== 'pending') return route.continue();
    const held = nextRead;
    nextRead = undefined;
    // Capture what the worker returned now. Release it later without re-reading the worker.
    const reply = await route.fetch();
    expect(reply.ok()).toBe(true);
    const { result } = await reply.json() as { result: { actions: Action[] } };
    const snapshot = result.actions.filter(action => ids.has(action.id));
    const read = { request: route.request(), snapshot, failed: Boolean(held?.error) };
    reads.push(read);
    if (held) {
      held.entered.release();
      await held.release.promise;
      if (held.error) return failList(route, held.error);
    }
    return route.fulfill({ json: { result: { actions: snapshot } } });
  });
  return {
    reads, calls, asks, answers,
    pauseAsk: () => (holdAsk = gate()),
    pauseRead: (error?: string) => (nextRead = { entered: gate(), release: gate(), error }),
  };
}

test.afterEach(async ({ page }) => {
  const ids = ownedActions.get(page);
  if (!ids?.size) return;
  // The isolated worker is shared by the specs in one run. Leave other tests' pending lists alone.
  const { actions } = await rpc<{ actions: Action[] }>(page, 'ceo.actions', { status: 'pending' });
  for (const action of actions) {
    if (ids.has(action.id)) await rpc(page, 'ceo.deny', { action_id: action.id, confirm: true });
  }
});

async function finishRead(page: Page, read: Read) {
  const response = await read.request.response();
  expect(response).not.toBeNull();
  await response!.finished();
  // Flush response-driven React work without sleeping for an arbitrary interval.
  await page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
}

async function home(page: Page) {
  const chat = await rpc<Chat>(page, 'conversations.create', { title: 'FE-03 preserve this CEO chat' });
  await page.addInitScript(({ key, id }) => localStorage.setItem(key, JSON.stringify(id)), { key: selectedKey, id: chat.id });
  await page.goto('/?ui=rusebot');
  await expect(page.locator('[data-chathead-conversation]')).toHaveText(chat.title);
  return chat;
}

async function send(page: Page, question: string) {
  await page.getByRole('textbox', { name: 'Message Loqman', exact: true }).fill(question);
  const button = page.getByRole('button', { name: 'Send', exact: true });
  await expect(button).toBeEnabled();
  await button.click();
}

async function keepChat(page: Page, chat: Chat) {
  expect(await page.evaluate(key => JSON.parse(localStorage.getItem(key) ?? 'null') as string | null, selectedKey)).toBe(chat.id);
}

async function count(page: Page, value: number) {
  await expect(results(page).getByRole('heading', { name: 'Needs you (' + value + ')', exact: true })).toBeVisible();
  if (value === 0) await expect(badge(page)).toHaveCount(0);
  else await expect(badge(page)).toHaveAccessibleName('Needs you: ' + value);
}

async function expectProposal(page: Page, action: Action) {
  await count(page, 1);
  await expect(cards(page)).toHaveCount(1);
  await expect(cards(page)).toContainText(action.summary);
}

test('a proposal-only CEO acknowledgement refreshes Needs You before leaving Chat', async ({ page }) => {
  await ensurePlanner(page);
  const worker = await fixture(page);
  const chat = await home(page);
  await expect.poll(() => worker.reads.length).toBeGreaterThan(0);
  await finishRead(page, worker.reads[0]);
  await expect(badge(page)).toHaveCount(0);
  const initialReads = worker.reads.length;

  await send(page, proposalQuestion);
  await expect.poll(() => worker.answers.length).toBe(1);
  const answer = worker.answers[0];
  expect(answer.agents_created).toEqual([]);
  expect(answer.actions_proposed).toHaveLength(1);
  const action = answer.actions_proposed[0];
  await expect(badge(page)).toHaveAccessibleName('Needs you: 1');
  await expect(page.locator('[data-chathead-conversation]')).toHaveText(chat.title);
  expect(worker.reads.slice(initialReads).some(read => read.snapshot.some(item => item.id === action.id))).toBe(true);

  await badge(page).click();
  await expectProposal(page, action);
  await keepChat(page, chat);
  expect(worker.asks).toHaveLength(1);
  expect(worker.asks[0].params.conversation_id).toBe(chat.id);
});

test('an acknowledged CEO ask updates Results that was opened while the ask was pending', async ({ page }) => {
  await ensurePlanner(page);
  const worker = await fixture(page);
  const chat = await home(page);
  await expect.poll(() => worker.reads.length).toBeGreaterThan(0);
  const held = worker.pauseAsk();
  try {
    await send(page, proposalQuestion);
    await expect.poll(() => worker.asks.length).toBe(1);
    const beforeResults = worker.reads.length;
    await nav(page, 'results').click();
    await expect.poll(() => worker.reads.length).toBeGreaterThan(beforeResults);
    await finishRead(page, worker.reads.at(-1)!);
    await count(page, 0);
  } finally {
    held.release();
  }
  await expect.poll(() => worker.answers.length).toBe(1);
  expect(worker.answers[0].agents_created).toEqual([]);
  expect(worker.answers[0].actions_proposed).toHaveLength(1);
  await expectProposal(page, worker.answers[0].actions_proposed[0]);
  await keepChat(page, chat);
  expect(worker.asks).toHaveLength(1);
});

test('a late successful pending list cannot restore an older proposal', async ({ page }) => {
  await ensurePlanner(page);
  const action = await makeProposal(page);
  const worker = await fixture(page, [action]);
  await home(page);
  await expect(badge(page)).toHaveAccessibleName('Needs you: 1');
  const stale = worker.pauseRead();
  let oldRead: Read;
  try {
    await nav(page, 'team').click();
    await stale.entered.promise;
    oldRead = worker.reads.at(-1)!;
    expect(oldRead.snapshot.map(item => item.id)).toEqual([action.id]);

    await rpc(page, 'ceo.deny', { action_id: action.id, confirm: true });
    await nav(page, 'results').click();
    await count(page, 0);
    await expect(cards(page)).toHaveCount(0);
  } finally {
    stale.release.release();
  }
  await finishRead(page, oldRead);
  await count(page, 0);
  await expect(cards(page)).toHaveCount(0);
});

test('a late pending-list failure cannot show an error after a newer success', async ({ page }) => {
  await ensurePlanner(page);
  const action = await makeProposal(page);
  const worker = await fixture(page, [action]);
  await home(page);
  await expect(badge(page)).toHaveAccessibleName('Needs you: 1');

  const stale = worker.pauseRead('FE-03 superseded pending read failed');
  let oldRead: Read;
  try {
    await nav(page, 'team').click();
    await stale.entered.promise;
    oldRead = worker.reads.at(-1)!;
    const before = worker.reads.length;
    await nav(page, 'results').click();
    await expect.poll(() => worker.reads.length).toBeGreaterThan(before);
    await finishRead(page, worker.reads.at(-1)!);
    await expectProposal(page, action);
  } finally {
    stale.release.release();
  }
  await finishRead(page, oldRead);
  await expectProposal(page, action);
  await expect(banner(page)).toHaveCount(0);
});

test('a current pending-list failure retains data with an honest error and Retry', async ({ page }) => {
  await ensurePlanner(page);
  const action = await makeProposal(page);
  const worker = await fixture(page, [action]);
  await home(page);
  await expect(badge(page)).toHaveAccessibleName('Needs you: 1');

  const current = worker.pauseRead('FE-03 current pending read failed');
  try {
    await nav(page, 'results').click();
    await current.entered.promise;
  } finally {
    current.release.release();
  }
  await expect(banner(page)).toContainText('Could not refresh proposals');
  await expect(banner(page)).toContainText('Showing the last data that loaded');
  await expect(banner(page)).toContainText('FE-03 current pending read failed');
  await expectProposal(page, action);

  await rpc(page, 'ceo.deny', { action_id: action.id, confirm: true });
  await banner(page).getByRole('button', { name: 'Retry', exact: true }).click();
  await expect(banner(page)).toHaveCount(0);
  await count(page, 0);
  await expect(cards(page)).toHaveCount(0);
});

for (const decision of ['approve', 'deny'] as const) {
  test(decision + ' refreshes the authoritative pending list and ignores an older read', async ({ page }) => {
    await ensurePlanner(page);
    const action = await makeProposal(page);
    const worker = await fixture(page, [action]);
    await home(page);
    await expect(badge(page)).toHaveAccessibleName('Needs you: 1');

    const stale = worker.pauseRead();
    const outcome = decision === 'approve' ? 'Approved' : 'Denied';
    let oldRead: Read;
    try {
      await badge(page).click();
      await stale.entered.promise;
      oldRead = worker.reads.at(-1)!;
      expect(oldRead.snapshot.map(item => item.id)).toEqual([action.id]);
      await expectProposal(page, action);
      await cards(page).getByRole('button', { name: decision === 'approve' ? 'Approve' : 'Deny', exact: true }).click();
      await expect(cards(page)).toContainText(outcome);
      await count(page, 0);
      const decisions = worker.calls.filter(call => call.method === 'ceo.approve' || call.method === 'ceo.deny');
      expect(decisions).toEqual([{ method: 'ceo.' + decision, params: { action_id: action.id, confirm: true } }]);
      const { actions } = await rpc<{ actions: Action[] }>(page, 'ceo.actions', { status: outcome.toLowerCase() });
      expect(actions.some(item => item.id === action.id)).toBe(true);
    } finally {
      stale.release.release();
    }
    await finishRead(page, oldRead);
    await count(page, 0);
    await expect(cards(page)).toHaveCount(1);
    await expect(cards(page)).toContainText(outcome);
    await expect(cards(page).getByRole('button', { name: 'Approve', exact: true })).toHaveCount(0);
    await expect(cards(page).getByRole('button', { name: 'Deny', exact: true })).toHaveCount(0);
    expect(worker.asks).toEqual([]);
  });
}

test('automatically applied creation refreshes the Agent without adding a pending proposal or another ask', async ({ page }) => {
  const worker = await fixture(page);
  const chat = await home(page);
  await expect.poll(() => worker.reads.length).toBeGreaterThan(0);
  await finishRead(page, worker.reads[0]);

  // Record approved IDs so the automatic result is attributable to this ask even in a shared run database.
  const before = await rpc<{ actions: Action[] }>(page, 'ceo.actions', { status: 'approved' });
  const readCount = worker.reads.length;
  await send(page, 'Add a new employee');
  await expect.poll(() => worker.answers.length).toBe(1);
  const answer = worker.answers[0];
  expect(answer.actions_proposed).toEqual([]);
  expect(answer.agents_created).toHaveLength(1);
  const created = answer.agents_created[0];
  await expect(page.locator('[data-sidebar-agent-row="' + created.id + '"]')).toContainText(created.name);
  const after = await rpc<{ actions: Action[] }>(page, 'ceo.actions', { status: 'approved' });
  const automatic = after.actions.find(action => action.kind === 'new_profile' && !before.actions.some(old => old.id === action.id));
  expect(automatic).toBeDefined();
  await expect.poll(() => worker.reads.length).toBeGreaterThan(readCount);
  await finishRead(page, worker.reads.at(-1)!);
  await expect(badge(page)).toHaveCount(0);
  await nav(page, 'results').click();
  await count(page, 0);
  await expect(cards(page)).toHaveCount(0);
  expect(worker.asks).toHaveLength(1);
  await keepChat(page, chat);
});
