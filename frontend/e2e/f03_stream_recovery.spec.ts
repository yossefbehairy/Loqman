import { expect, test, type Page, type Route } from '@playwright/test';

type Snapshot = {
  id: string;
  conversation_id: string;
  status: string;
  assistant_content: string | null;
};

type StreamFrame = {
  type: 'stream' | 'stream_end';
  stream_id: string;
  id?: number;
  event?: string;
  data?: object;
  reason?: string;
};

type Subscription = {
  runId: string;
  cursor: number;
  format: string;
  closed: boolean;
  receive: (frame: StreamFrame) => void;
};

type TestWindow = typeof window & { fe01Subscriptions: Subscription[] };
type StatusRoute = (route: Route, runId: string, attempt: number) => Promise<void>;
const specialist = 'FE-01 stream specialist';
const delayed = 'Specialist stream recovery [mock:delay=3000]';
const longRun = 'Cancellable specialist run [mock:delay=30000]';
// The shared mock worker can still be draining earlier specs' long runs.
const workerStatusTimeout = 45_000;
// A worker can settle just after a status read. Allow the 15-second maximum retry delay and RPC time.
const rendererRecoveryTimeout = 20_000;
test.describe.configure({ timeout: 75_000 });
const box = (page: Page) => page.getByRole('textbox', { name: 'Message ' + specialist });
const stop = (page: Page) => page.getByRole('button', { name: 'Stop', exact: true });
const send = (page: Page) => page.getByRole('button', { name: 'Send', exact: true });

function gate() {
  let release!: () => void;
  const wait = new Promise<void>((resolve) => { release = resolve; });
  return { wait, release };
}

async function rpc<T>(page: Page, method: string, params: object) {
  const response = await page.request.post('/__agentos/call', { data: { method, params } });
  expect(response.ok(), 'Mock worker RPC ' + method).toBe(true);
  return (await response.json() as { result: T }).result;
}

/** Emulate the desktop stream boundary over the existing mock worker RPC bridge. */
async function controlStream(page: Page, mode: 'reject' | 'manual' | 'resume') {
  await page.addInitScript((mode) => {
    const target = window as TestWindow;
    const subscriptions: Subscription[] = [];
    const started = new Set<string>();
    target.fe01Subscriptions = subscriptions;
    Object.defineProperty(window, 'agentos', { value: {
      async call(method: string, params: object) {
        const response = await fetch('/__agentos/call', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ method, params }),
        });
        const body = await response.json() as { result: unknown; detail?: string };
        if (!response.ok) throw new Error(JSON.stringify({ status: response.status, detail: body.detail }));
        if (method === 'runs.start_turn') started.add((body.result as Snapshot).id);
        return body.result;
      },
      async openStream(topic: string, runId: string, cursor: number, receive: Subscription['receive'], format: string) {
        const subscription = { runId, cursor, receive, format, closed: false };
        const inTest = started.has(runId);
        const resuming = subscriptions.some((entry) => entry.runId === runId);
        if (inTest) subscriptions.push(subscription);
        if (inTest && mode === 'reject') throw new Error('Injected desktop stream subscription rejection');
        // After dropping the first stream, let the real worker replay and tail on the resumed one.
        const source = !inTest || (mode === 'resume' && resuming)
          ? new EventSource('/__agentos/stream?' + new URLSearchParams({ topic, id: runId, last: String(cursor), format }))
          : null;
        for (const event of ['stream', 'stream_end']) {
          source?.addEventListener(event, (frame: MessageEvent<string>) => receive(JSON.parse(frame.data) as StreamFrame));
        }
        return { close: () => {
          subscription.closed = true;
          source?.close();
        } };
      },
    } });
  }, mode);
}

function deliver(page: Page, frame: StreamFrame) {
  // A queued IPC callback can arrive after close, so deliberately keep the first subscription addressable.
  return page.evaluate((frame) => (window as TestWindow).fe01Subscriptions[0].receive(frame), frame);
}

const disconnect = (page: Page) => deliver(page, { type: 'stream_end', stream_id: 'first', reason: 'error' });

function subscriptions(page: Page) {
  return page.evaluate(() => (window as TestWindow).fe01Subscriptions.map(({ runId, cursor, format, closed }) =>
    ({ runId, cursor, format, closed })));
}

async function openChat(page: Page, onStatus?: StatusRoute) {
  const profile = await rpc<{ id: string }>(page, 'profiles.create', {
    name: specialist, role_type: 'custom', instructions: 'Answer briefly.',
  });
  const chat = await rpc<{ id: string }>(page, 'conversations.create', {
    title: 'FE-01 specialist stream', profile_id: profile.id,
  });
  const starts: Snapshot[] = [];
  const cancellations: Snapshot[] = [];
  const reads: string[] = [];
  await page.route('**/__agentos/call', async (route) => {
    const { method, params } = route.request().postDataJSON() as { method: string; params: Record<string, string> };
    if (method === 'runs.start_turn' || method === 'runs.cancel') {
      if (method === 'runs.start_turn' && params.engine !== 'mock') {
        return route.fulfill({ status: 400, json: { detail: 'Only mock execution is allowed in this spec.' } });
      }
      const response = await route.fetch();
      if (response.ok()) {
        const { result } = await response.json() as { result: Snapshot };
        (method === 'runs.start_turn' ? starts : cancellations).push(result);
      }
      return route.fulfill({ response });
    }
    if (method === 'runs.get' && starts.some(({ id }) => id === params.run_id)) {
      reads.push(params.run_id);
      if (onStatus) return onStatus(route, params.run_id, reads.length);
    }
    return route.continue();
  });
  await page.addInitScript((id) => localStorage.setItem('agentos.rusebot.selected.v1', JSON.stringify(id)), chat.id);
  await page.goto('/');
  await expect(box(page)).toBeVisible();

  const get = (runId: string) => rpc<Snapshot>(page, 'runs.get', { run_id: runId });
  const expectStatus = (runId: string, status: string) =>
    expect.poll(async () => (await get(runId)).status, { timeout: workerStatusTimeout }).toBe(status);
  const started = (runId: string | null) => starts.some(({ id }) => id === runId);
  const expectRuns = async (expected: Array<Pick<Snapshot, 'id' | 'status'>>) => {
    const { runs } = await rpc<{ runs: Snapshot[] }>(page, 'runs.list', { conversation_id: chat.id });
    expect(runs.map(({ id, status }) => ({ id, status }))).toEqual(expected);
    expect(starts.map(({ id }) => id)).toEqual(expected.map(({ id }) => id));
  };
  const start = async (prompt: string) => {
    const count = starts.length;
    await box(page).fill(prompt);
    await expect(send(page)).toBeEnabled();
    await send(page).click();
    await expect.poll(() => starts.length).toBe(count + 1);
    expect(starts[count].conversation_id).toBe(chat.id);
    return starts[count].id;
  };
  const expectComplete = async (runId: string) => {
    await expectStatus(runId, 'succeeded');
    const snap = await get(runId);
    expect(snap.assistant_content).toBeTruthy();
    await expect(stop(page), 'The worker is terminal, so Stop should be gone').toHaveCount(0, { timeout: rendererRecoveryTimeout });
    await expect(page.locator('[data-message-row][data-role="assistant"]')).toContainText(snap.assistant_content!, { timeout: 6000 });
    await box(page).fill('The next draft');
    await expect(send(page)).toBeEnabled();
  };
  return { start, get, started, expectStatus, expectRuns, expectComplete, cancellations, reads };
}

test('A: failed desktop subscription recovers the original accepted turn', async ({ page }) => {
  await controlStream(page, 'reject');
  const chat = await openChat(page);
  const id = await chat.start(delayed);
  await expect.poll(() => subscriptions(page)).toEqual(expect.arrayContaining([
    expect.objectContaining({ runId: id, cursor: 0, format: 'normalised-v1' }),
  ]));
  await chat.expectComplete(id);
  await chat.expectRuns([{ id, status: 'succeeded' }]);
  expect(chat.reads.length).toBeGreaterThan(0);
  expect(new Set(chat.reads)).toEqual(new Set([id]));
});

test('A: a rejected browser EventSource still reconciles the accepted turn', async ({ page }) => {
  await page.clock.install();
  const chat = await openChat(page);
  await page.route('**/__agentos/stream?**', async (route) => {
    const id = new URL(route.request().url()).searchParams.get('id');
    if (!chat.started(id)) return route.continue();
    await route.fulfill({ status: 503, json: { detail: 'Injected browser stream subscription rejection' } });
  });
  const refused = page.waitForResponse((response) => response.url().includes('/__agentos/stream?') && response.status() === 503);
  const id = await chat.start('Specialist reply after a rejected browser stream');
  expect(new URL((await refused).url()).searchParams.get('id')).toBe(id);
  await chat.expectStatus(id, 'succeeded');
  await page.clock.fastForward(20_000);
  await chat.expectComplete(id);
  await chat.expectRuns([{ id, status: 'succeeded' }]);
  expect(new Set(chat.reads)).toEqual(new Set([id]));
});

test('B: premature stream end while running resumes after the last worker event', async ({ page }) => {
  await controlStream(page, 'resume');
  const observed: Snapshot[] = [];
  let ended = false;
  const chat = await openChat(page, async (route) => {
    const afterEnd = ended;
    const response = await route.fetch();
    if (afterEnd) observed.push((await response.json() as { result: Snapshot }).result);
    await route.fulfill({ response });
  });
  const id = await chat.start(delayed);
  await expect.poll(() => subscriptions(page).then((all) => all.length)).toBe(1);
  await chat.expectStatus(id, 'running');
  const { events } = await rpc<{ events: Array<{ id: number; type: string; data: object }> }>(page, 'runs.events', { run_id: id });
  const event = events.find((entry) => entry.type === 'running');
  expect(event).toBeDefined();
  await deliver(page, { type: 'stream', stream_id: 'first', event: 'running', id: event!.id, data: event!.data });
  ended = true;
  await disconnect(page);
  await expect.poll(() => observed[0]).toMatchObject({ id, status: 'running' });
  await chat.expectComplete(id);
  expect(await subscriptions(page)).toEqual([
    { runId: id, cursor: 0, format: 'normalised-v1', closed: true },
    { runId: id, cursor: event!.id, format: 'normalised-v1', closed: true },
  ]);
  await chat.expectRuns([{ id, status: 'succeeded' }]);
});

test('C: a temporary status failure does not abandon terminal recovery', async ({ page }) => {
  await controlStream(page, 'manual');
  const errors: Error[] = [];
  const recoveryReads: string[] = [];
  let ended = false;
  page.on('pageerror', (error) => errors.push(error));
  const chat = await openChat(page, async (route, id) => {
    if (ended) {
      recoveryReads.push(id);
      if (recoveryReads.length === 1) {
        return route.fulfill({ status: 503, json: { detail: 'Temporary status failure' } });
      }
    }
    await route.continue();
  });
  const id = await chat.start('A specialist reply with a temporary status read failure');
  await expect.poll(() => subscriptions(page).then((all) => all.length)).toBe(1);
  await chat.expectStatus(id, 'succeeded');
  ended = true;
  await disconnect(page);
  await chat.expectComplete(id);
  expect(recoveryReads).toEqual([id, id]);
  expect(errors).toEqual([]);
  await chat.expectRuns([{ id, status: 'succeeded' }]);
});

test('C: a prolonged status outage is visible and keeps the accepted run recoverable', async ({ page }) => {
  await controlStream(page, 'manual');
  let connected = false;
  const chat = await openChat(page, async (route) => {
    if (!connected) return route.fulfill({ status: 503, json: { detail: 'Worker connection unavailable' } });
    await route.continue();
  });
  const id = await chat.start('Specialist reply during a status outage');
  await expect.poll(() => subscriptions(page).then((all) => all.length)).toBe(1);
  await disconnect(page);
  await expect(page.getByRole('alert').filter({ hasText: /could not verify.*run/i })).toBeVisible({ timeout: 6000 });
  await expect(stop(page)).toBeEnabled();
  await expect(send(page)).toHaveCount(0);
  connected = true;
  await chat.expectComplete(id);
  await expect(page.getByRole('alert')).toHaveCount(0);
  await chat.expectRuns([{ id, status: 'succeeded' }]);
  expect(new Set(chat.reads)).toEqual(new Set([id]));
});

for (const newerRun of [false, true]) {
  test(newerRun ? 'E: late recovery cannot change the newer run controls' : 'D: Stop settles from the cancel acknowledgement without a stream',
    async ({ page }) => {
      await controlStream(page, 'manual');
      const statusGate = gate();
      const chat = await openChat(page, async (route, _id, attempt) => {
        if (attempt === 1) await statusGate.wait;
        await route.continue();
      });
      try {
        const first = await chat.start(longRun);
        await expect.poll(() => subscriptions(page).then((all) => all.length)).toBe(1);
        await chat.expectStatus(first, 'running');
        await deliver(page, {
          type: 'stream', stream_id: 'first', id: 10, event: 'content.delta',
          data: { item_id: 'partial', data: { stream_kind: 'assistant_text', delta: 'Partial specialist reply' } },
        });
        const live = page.locator('[data-live-bubble]');
        await expect(live).toContainText('Partial specialist reply');
        await disconnect(page);
        await expect.poll(() => chat.reads[0]).toBe(first);
        await stop(page).click();
        await expect.poll(() => chat.cancellations[0]).toMatchObject({ id: first, status: 'cancelled' });
        expect((await chat.get(first)).status).toBe('cancelled');
        await expect(stop(page), 'Cancellation must settle while the stream and status lookup are unavailable').toHaveCount(0, { timeout: 6000 });
        await expect(live.locator('[data-live-stopped]')).toHaveText('Stopped');
        if (!newerRun) {
          await box(page).fill('Draft after stopping');
          await expect(send(page)).toBeEnabled();
          await chat.expectRuns([{ id: first, status: 'cancelled' }]);
          return;
        }
        const second = await chat.start('Newer ' + longRun);
        expect(second).not.toBe(first);
        await chat.expectStatus(second, 'running');
        await box(page).fill('Draft owned by the current chat');
        const lateResponse = page.waitForResponse((response) => {
          const request = response.request();
          if (!request.url().endsWith('/__agentos/call') || request.method() !== 'POST') return false;
          const { method, params } = request.postDataJSON() as { method: string; params: { run_id?: string } };
          return method === 'runs.get' && params.run_id === first;
        });
        statusGate.release();
        expect((await (await lateResponse).json() as { result: Snapshot }).result).toMatchObject({ id: first, status: 'cancelled' });
        await deliver(page, { type: 'stream', stream_id: 'first', id: 11, event: 'cancelled', data: {} });
        await expect(stop(page)).toBeEnabled();
        await expect(send(page)).toHaveCount(0);
        await expect(box(page)).toHaveValue('Draft owned by the current chat');
        await chat.expectRuns([{ id: first, status: 'cancelled' }, { id: second, status: 'running' }]);
        await stop(page).click();
        await expect.poll(() => chat.cancellations[1]).toMatchObject({ id: second, status: 'cancelled' });
        await expect(send(page)).toBeEnabled();
      } finally {
        statusGate.release();
      }
    });
}

test('F: the normalised worker stream still completes and cancels accepted turns', async ({ page }) => {
  const chat = await openChat(page);
  const stream = page.waitForRequest((request) =>
    request.url().includes('/__agentos/stream?') && chat.started(new URL(request.url()).searchParams.get('id')));
  const first = await chat.start('A normal specialist reply');
  const params = new URL((await stream).url()).searchParams;
  expect(Object.fromEntries(params)).toMatchObject({ id: first, topic: 'run', format: 'normalised-v1' });
  await chat.expectComplete(first);
  const second = await chat.start(longRun);
  await chat.expectStatus(second, 'running');
  await stop(page).click();
  await expect.poll(() => chat.cancellations[0]).toMatchObject({ id: second, status: 'cancelled' });
  await expect(stop(page)).toHaveCount(0);
  await chat.expectRuns([{ id: first, status: 'succeeded' }, { id: second, status: 'cancelled' }]);
});
