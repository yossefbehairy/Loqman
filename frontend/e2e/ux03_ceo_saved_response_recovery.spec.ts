import { expect, test, type Page } from '@playwright/test';

type Conversation = { id: string; title: string };
type Message = { seq: number; role: 'user' | 'assistant'; client_message_id: string | null; content: string };
type Rpc = { method: string; params: Record<string, unknown> };

const question = 'Can you give me a short update on the project?';
const model = 'deepseek-v4.1-flash-free';
const retry = (page: Page) => page.locator('[data-chat-retry]');
const transcript = (page: Page) => page.getByRole('log');
const row = (page: Page, seq: number) => transcript(page).locator(`[data-mid="${seq}"]`);
const switchTo = (page: Page, chat: Conversation) => page.locator(`[data-sidebar-thread-row="${chat.id}"]`).click();
const send = (page: Page) => page.getByRole('button', { name: 'Send', exact: true });
const settleRender = (page: Page) => page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));

function gate() {
  let release!: () => void;
  const wait = new Promise<void>(resolve => { release = resolve; });
  return { wait, release };
}

async function rpc<T>(page: Page, method: string, params: object) {
  const response = await page.request.post('/__agentos/call', { data: { method, params } });
  expect(response.ok(), method + ' on the isolated worker').toBe(true);
  return (await response.json() as { result: T }).result;
}

async function reopen(page: Page, mode: 'saved' | 'switching' | 'incomplete') {
  const original = await rpc<Conversation>(page, 'conversations.create', { title: 'UX-03 original CEO chat' });
  const other = await rpc<Conversation>(page, 'conversations.create', { title: 'UX-03 other CEO chat' });
  const earlier = await rpc<Message>(page, 'conversations.add_message', {
    conversation_id: original.id, role: 'assistant', content: 'Earlier project update',
  });
  const otherMessage = await rpc<Message>(page, 'conversations.add_message', {
    conversation_id: other.id, role: 'assistant', content: 'An independent conversation',
  });
  const requestId = `ceo-${crypto.randomUUID()}`;
  const recordKey = 'agentos.rusebot.submission.v1.' + requestId;
  const history = () => rpc<{ messages: Message[] }>(page, 'conversations.messages', { conversation_id: original.id });
  const beforeCompletion = await history();
  const calls: Rpc[] = [];
  const firstRead = gate();
  const releaseFirstRead = gate();
  const retryRead = gate();
  const releaseRetryRead = gate();
  const retryDelivered = gate();
  let opening = true;
  let heldRetry = false;
  let nextHistory: { captured: ReturnType<typeof gate>; release: ReturnType<typeof gate>; delivered: ReturnType<typeof gate> } | undefined;
  let rejectDuplicate = false;
  await page.route('**/__agentos/call', async route => {
    const call = route.request().postDataJSON() as Rpc;
    calls.push(call);
    if (rejectDuplicate && call.method === 'ceo.ask' && call.params.question === question) {
      return route.fulfill({ status: 409, json: { detail: 'Fixture stopped a duplicate ask before worker execution.' } });
    }
    if (call.method === 'ceo.ask_progress' && call.params.request_id === requestId && mode === 'incomplete') {
      return route.fulfill({ json: { result: { stage: 'thinking', detail: '', iteration: 1, done: false } } });
    }
    if (call.method === 'conversations.messages' && call.params.conversation_id === original.id) {
      if (opening) {
        firstRead.release();
        await releaseFirstRead.wait;
        return route.fulfill({ json: { result: beforeCompletion } });
      }
      if (mode === 'switching' && !heldRetry) {
        heldRetry = true;
        const response = await route.fetch();
        expect(response.ok()).toBe(true);
        retryRead.release();
        await releaseRetryRead.wait;
        await route.fulfill({ response });
        retryDelivered.release();
        return;
      }
      if (nextHistory) {
        const held = nextHistory;
        nextHistory = undefined;
        const response = await route.fetch();
        expect(response.ok()).toBe(true);
        held.captured.release();
        await held.release.wait;
        await route.fulfill({ response });
        held.delivered.release();
        return;
      }
    }
    return route.continue();
  });
  await page.addInitScript(({ cid, requestId, recordKey, question, model }) => {
    localStorage.setItem('agentos.rusebot.selected.v1', JSON.stringify(cid));
    localStorage.setItem('agentos.rusebot.drafts.v1', JSON.stringify({ [cid]: question }));
    localStorage.setItem(recordKey, JSON.stringify({
      cid, agentId: 'custom', prompt: question, requestId, ownsDraft: true, execution: { engine: 'mock', model },
    }));
  }, { cid: original.id, requestId, recordKey, question, model });
  try {
    await page.goto('/');
    await firstRead.wait;
    // Let the real worker complete after the reopening history read has taken its snapshot.
    if (mode !== 'incomplete') await rpc(page, 'ceo.ask', {
      conversation_id: original.id, request_id: requestId, question, engine: 'mock', model_id: model,
    });
  } finally {
    releaseFirstRead.release();
  }
  await expect(row(page, earlier.seq)).toBeVisible();
  await expect(retry(page)).toBeVisible();
  opening = false;
  const record = () => page.evaluate(key => localStorage.getItem(key), recordKey);
  const asks = () => calls.filter(call => call.method === 'ceo.ask');
  const holdNextHistory = () => (nextHistory = { captured: gate(), release: gate(), delivered: gate() });
  return { original, other, otherMessage, earlier, retryRead, releaseRetryRead, retryDelivered, requestId, history, record, asks, calls,
    holdNextHistory, stopDuplicateBeforeExecution: () => { rejectDuplicate = true; } };
}

test('AUD-02: transcript Retry recovers an acknowledged CEO exchange without sending again', async ({ page }) => {
  const original = await rpc<Conversation>(page, 'conversations.create', { title: 'AUD-02 acknowledged CEO chat' });
  const other = await rpc<Conversation>(page, 'conversations.create', { title: 'AUD-02 independent chat' });
  const earlier = await rpc<Message>(page, 'conversations.add_message', {
    conversation_id: original.id, role: 'assistant', content: 'Earlier AUD-02 project update',
  });
  const otherMessage = await rpc<Message>(page, 'conversations.add_message', {
    conversation_id: other.id, role: 'assistant', content: 'AUD-02 independent update',
  });
  const asks: Rpc[] = [];
  const historyFailure = 'Fixture lost the saved CEO history refresh.';
  let rejectHistory = false;
  await page.route('**/__agentos/call', async route => {
    const call = route.request().postDataJSON() as Rpc;
    if (call.method === 'ceo.ask') {
      asks.push(call);
      const response = await route.fetch();
      expect(response.ok()).toBe(true);
      rejectHistory = true;
      return route.fulfill({ response });
    }
    if (rejectHistory && call.method === 'conversations.messages' && call.params.conversation_id === original.id) {
      return route.fulfill({ status: 503, json: { detail: historyFailure } });
    }
    return route.continue();
  });
  await page.goto('/');
  await switchTo(page, original);
  await expect(row(page, earlier.seq)).toBeVisible();
  await page.getByRole('textbox', { name: 'Message Loqman' }).fill(question);
  await send(page).click();

  const alert = transcript(page).getByRole('alert').filter({ hasText: historyFailure });
  await expect(alert, 'A successful ask with a failed history read must offer transcript Retry').toBeVisible();
  expect(asks).toHaveLength(1);
  expect(asks[0].params.conversation_id).toBe(original.id);
  const requestId = asks[0].params.request_id;
  expect(requestId).toMatch(/^ceo-/);
  const record = () => page.evaluate(key => localStorage.getItem(key), 'agentos.rusebot.submission.v1.' + requestId);
  expect(JSON.parse((await record()) ?? 'null')).toMatchObject({ requestId, cid: original.id, acknowledged: true });
  const history = () => rpc<{ messages: Message[] }>(page, 'conversations.messages', { conversation_id: original.id });
  const { messages } = await history();
  expect(messages.map(message => [message.role, message.client_message_id])).toEqual([
    ['assistant', null], ['user', `ask:${requestId}:user`], ['assistant', `ask:${requestId}:assistant`],
  ]);
  const [, savedUser, savedReply] = messages;
  expect(savedUser.content).toBe(question);
  expect(savedReply.content).not.toBe('');
  await expect(row(page, savedReply.seq)).toHaveCount(0);

  await switchTo(page, other);
  await expect(row(page, otherMessage.seq)).toBeVisible();
  await expect(row(page, savedReply.seq)).toHaveCount(0);
  await expect(alert).toHaveCount(0);
  const returnFailure = page.waitForResponse(response => {
    const request = response.request();
    if (request.method() !== 'POST' || new URL(request.url()).pathname !== '/__agentos/call') return false;
    const call = request.postDataJSON() as Rpc;
    return response.status() === 503 && call.method === 'conversations.messages' && call.params.conversation_id === original.id;
  });
  await switchTo(page, original);
  await returnFailure;
  await expect(alert).toBeVisible();
  rejectHistory = false;
  await alert.getByRole('button', { name: 'Retry', exact: true }).click();

  await expect(row(page, savedUser.seq)).toHaveCount(1);
  await expect(row(page, savedUser.seq)).toContainText(question);
  await expect(row(page, savedReply.seq)).toHaveCount(1);
  await expect(row(page, savedReply.seq)).toHaveText(new RegExp(savedReply.content.replace(/[.*+?^${}()|[\]\\]/g, '\\$&').replace(/\s+/g, '\\s+')));
  expect(await transcript(page).locator('[data-mid]').evaluateAll(rows => rows.map(el => Number(el.getAttribute('data-mid')))))
    .toEqual(messages.map(message => message.seq));
  await expect(alert).toHaveCount(0);
  await expect.poll(record).toBeNull();
  expect(asks).toHaveLength(1);
  expect((await history()).messages).toEqual(messages);
});

test('UX-03: explicit CEO Retry shows the durable reply once without another ask', async ({ page }) => {
  const fixture = await reopen(page, 'saved');
  const { messages } = await fixture.history();
  const saved = messages.find(message => message.client_message_id === `ask:${fixture.requestId}:assistant`);
  expect(saved?.content).toBeTruthy();
  if (!saved) throw new Error('The worker did not save a reply');
  await expect(row(page, saved.seq)).toHaveCount(0);
  await retry(page).click();
  await expect.poll(fixture.record).toBeNull();
  expect(fixture.asks()).toEqual([]);
  await expect(row(page, saved.seq)).toHaveCount(1);
  await expect(row(page, saved.seq)).toBeVisible();
  await expect(row(page, saved.seq)).toHaveText(new RegExp(saved.content.replace(/[.*+?^${}()|[\]\\]/g, '\\$&').replace(/\s+/g, '\\s+')));
  expect(await transcript(page).locator('[data-mid]').evaluateAll(rows => rows.map(row => Number(row.getAttribute('data-mid')))))
    .toEqual(messages.map(message => message.seq));
  await expect(retry(page)).toHaveCount(0);
  expect((await fixture.history()).messages).toEqual(messages);
});

test('UX-03: a held CEO recovery cannot publish a reply under another chat', async ({ page }) => {
  const fixture = await reopen(page, 'switching');
  const saved = (await fixture.history()).messages.find(message => message.client_message_id === `ask:${fixture.requestId}:assistant`);
  if (!saved) throw new Error('The worker did not save a reply');
  try {
    await retry(page).click();
    await fixture.retryRead.wait;
    await page.locator(`[data-sidebar-thread-row="${fixture.other.id}"]`).click();
    await expect(page.locator('[data-chathead-conversation]')).toHaveText(fixture.other.title);
    await expect(row(page, fixture.otherMessage.seq)).toBeVisible();
  } finally {
    fixture.releaseRetryRead.release();
  }
  await fixture.retryDelivered.wait;
  await page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
  expect(await fixture.record()).not.toBeNull();
  await expect(row(page, saved.seq)).toHaveCount(0);
  await expect(row(page, fixture.otherMessage.seq)).toBeVisible();
  await page.locator(`[data-sidebar-thread-row="${fixture.original.id}"]`).click();
  await expect(row(page, saved.seq)).toBeVisible();
  await expect.poll(fixture.record).toBeNull();
  await expect(row(page, saved.seq)).toHaveCount(1);
  await expect(row(page, fixture.otherMessage.seq)).toHaveCount(0);
  expect(fixture.asks()).toEqual([]);
});

test('UX-03: an incomplete CEO ask remains recoverable without duplicate work', async ({ page }) => {
  const fixture = await reopen(page, 'incomplete');
  const recovery = JSON.parse((await fixture.record()) ?? 'null');
  await retry(page).click();
  await expect(page.getByRole('alert')).toContainText('The worker has received this question');
  await expect(retry(page)).toBeVisible();
  expect(JSON.parse((await fixture.record()) ?? 'null')).toMatchObject(recovery);
  expect(fixture.asks()).toEqual([]);
  expect(fixture.calls.filter(call => call.method === 'ceo.ask_progress')).toEqual([
    { method: 'ceo.ask_progress', params: { request_id: fixture.requestId } },
  ]);
  expect((await fixture.history()).messages.map(message => message.seq)).toEqual([fixture.earlier.seq]);
});

test('UX-03 hardening: Send on the returned recovery draft never dispatches a completed CEO ask again', async ({ page }) => {
  const fixture = await reopen(page, 'switching');
  const { messages } = await fixture.history();
  const saved = messages.find(message => message.client_message_id === `ask:${fixture.requestId}:assistant`);
  if (!saved) throw new Error('The worker did not save the CEO reply');
  fixture.stopDuplicateBeforeExecution();
  let returning: ReturnType<typeof fixture.holdNextHistory> | undefined;
  try {
    await retry(page).click();
    await fixture.retryRead.wait;
    await switchTo(page, fixture.other);
    await expect(row(page, fixture.otherMessage.seq)).toBeVisible();
    fixture.releaseRetryRead.release();
    await fixture.retryDelivered.wait;
    await settleRender(page);
    expect(await fixture.record()).not.toBeNull();

    returning = fixture.holdNextHistory();
    await switchTo(page, fixture.original);
    await returning.captured.wait;
    const draft = page.getByRole('textbox', { name: 'Message Loqman' });
    await expect(draft).toHaveValue(question);
    // Dispatch the actual button interaction while history is held, including when the UI safely disables it.
    await send(page).evaluate((button: HTMLButtonElement) => button.click());
    await settleRender(page);
    expect(fixture.asks(), 'The unchanged recovery draft must not issue ceo.ask while history is held').toEqual([]);

    returning.release.release();
    await expect(row(page, saved.seq)).toHaveCount(1);
    await expect.poll(fixture.record).toBeNull();
    await expect(retry(page)).toHaveCount(0);
    expect((await fixture.history()).messages).toEqual(messages);

    const newQuestion = 'Please summarize the next milestone.';
    await draft.fill(newQuestion);
    await send(page).click();
    await expect.poll(() => fixture.asks().map(call => call.params.question)).toEqual([newQuestion]);
    await expect.poll(async () => (await fixture.history()).messages.filter(message => message.role === 'assistant').length).toBe(3);
    await expect(row(page, saved.seq)).toHaveCount(1);
  } finally {
    fixture.releaseRetryRead.release();
    returning?.release.release();
  }
});

test('UX-03 hardening: recovery preserves a newer in-flight history snapshot', async ({ page }) => {
  const fixture = await reopen(page, 'switching');
  const saved = (await fixture.history()).messages.find(message => message.client_message_id === `ask:${fixture.requestId}:assistant`);
  if (!saved) throw new Error('The worker did not save the CEO reply');
  let returning: ReturnType<typeof fixture.holdNextHistory> | undefined;
  try {
    await retry(page).click();
    await fixture.retryRead.wait;
    const later = await rpc<Message>(page, 'conversations.add_message', {
      conversation_id: fixture.original.id, role: 'assistant', content: 'A later persisted CEO update.',
    });
    await switchTo(page, fixture.other);
    await expect(row(page, fixture.otherMessage.seq)).toBeVisible();
    await expect(row(page, saved.seq)).toHaveCount(0);
    await expect(row(page, later.seq)).toHaveCount(0);

    returning = fixture.holdNextHistory();
    await switchTo(page, fixture.original);
    await returning.captured.wait;
    fixture.releaseRetryRead.release();
    await expect(row(page, saved.seq)).toBeVisible();
    await expect(row(page, later.seq)).toHaveCount(0);
    returning.release.release();
    await returning.delivered.wait;
    await expect(row(page, later.seq)).toBeVisible();
    expect(await transcript(page).locator('[data-mid]').evaluateAll(rows => rows.map(el => Number(el.getAttribute('data-mid')))))
      .toEqual((await fixture.history()).messages.map(message => message.seq));
    expect(fixture.asks()).toEqual([]);
  } finally {
    fixture.releaseRetryRead.release();
    returning?.release.release();
  }
});
