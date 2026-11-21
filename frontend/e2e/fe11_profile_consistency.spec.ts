import { expect, test, type Page, type Request, type Route } from '@playwright/test';

type Profile = { id: string; name: string; instructions: string };
type Chat = { id: string; title: string };
type Rpc = { method: string; params: Record<string, unknown> };
const callUrl = '**/__agentos/call';
const selectedKey = 'agentos.rusebot.selected.v1';

function gate() {
  let release!: () => void;
  const promise = new Promise<void>(resolve => { release = resolve; });
  return { promise, release };
}

async function rpc<T>(page: Page, method: string, params: object = {}): Promise<T> {
  const response = await page.request.post('/__agentos/call', { data: { method, params } });
  expect(response.ok(), method + ' on the isolated worker').toBe(true);
  return (await response.json() as { result: T }).result;
}

const roster = (page: Page, profile: Profile) => page.locator('[data-agent-card="' + profile.id + '"]');
const contractRow = (page: Page, profile: Profile) => page.locator('[data-contract-row="' + profile.id + '"]');
const panel = (page: Page, profile: Profile) => page.locator('[data-agent-panel="' + profile.id + '"]');
const banner = (page: Page) => page.locator('[data-load-error]');
const nav = (page: Page, view: string) => page.locator('[data-sidebar-nav="' + view + '"]');
const failRead = (route: Route, detail: string) => route.fulfill({ status: 503, json: { status: 503, detail } });

async function seed(page: Page) {
  const existing = await rpc<Profile>(page, 'profiles.create', {
    name: 'FE-11 existing specialist', role_type: 'custom', instructions: 'Original specialist contract.',
  });
  const chat = await rpc<Chat>(page, 'conversations.create', {
    title: 'FE-11 keep this selected chat', profile_id: existing.id,
  });
  await page.addInitScript(({ key, id }) => localStorage.setItem(key, JSON.stringify(id)), { key: selectedKey, id: chat.id });
  return { existing, chat };
}

async function home(page: Page, existing: Profile) {
  await page.goto('/?ui=rusebot');
  await expect(page.getByRole('textbox', { name: 'Message ' + existing.name, exact: true })).toBeEnabled();
}

async function expectSelected(page: Page, chat: Chat) {
  expect(await page.evaluate(key => JSON.parse(localStorage.getItem(key) ?? 'null') as string | null, selectedKey)).toBe(chat.id);
  await expect(page.locator('[data-sidebar-thread-row="' + chat.id + '"]')).toBeVisible();
}

async function createAgent(page: Page, name: string) {
  await page.locator('[data-new-agent-page]').click();
  await page.locator('#na-name').fill(name);
  await page.locator('#na-role').fill('Profile fixture');
  await page.locator('#na-instructions').fill('Contract for ' + name);
  await page.getByRole('button', { name: 'Create agent', exact: true }).click();
  await expect(page.getByRole('dialog', { name: 'New agent', exact: true })).toHaveCount(0);
}

async function showDetails(page: Page, profile: Profile) {
  await roster(page, profile).getByRole('button', { name: 'Open ' + profile.name, exact: true }).click();
  await expectDetails(page, profile);
}

async function expectDetails(page: Page, profile: Profile) {
  const details = panel(page, profile);
  await expect(details.getByRole('heading', { name: profile.name, exact: true })).toBeVisible();
  await expect(details.getByRole('textbox', { name: 'Contract', exact: true })).toHaveValue(profile.instructions);
}

async function flushResponses(page: Page, requests: Request[]) {
  await Promise.all(requests.map(async request => {
    const response = await request.response();
    expect(response).not.toBeNull();
    await response!.finished();
  }));
  // Observe the UI after the browser has processed the released responses.
  await page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
}

function recordCreation(page: Page, observe: (route: Route, call: Rpc) => Promise<void> | undefined) {
  let created: Profile | undefined;
  const calls: string[] = [];
  const ready = page.route(callUrl, async route => {
    const call = route.request().postDataJSON() as Rpc;
    calls.push(call.method);
    if (call.method === 'profiles.create') {
      const response = await route.fetch();
      expect(response.ok()).toBe(true);
      created = (await response.json() as { result: Profile }).result;
      return route.fulfill({ response });
    }
    return observe(route, call) ?? route.continue();
  });
  return { ready, calls, created: () => created };
}

for (const outcome of ['success', 'error'] as const) {
  test('an older profile ' + outcome + ' cannot replace the post-creation snapshot', async ({ page }) => {
    const { existing, chat } = await seed(page);
    const olderSnapshot = await rpc<{ profiles: Profile[] }>(page, 'profiles.list');
    await home(page, existing);
    const requested = gate();
    const release = gate();
    const older: Request[] = [];
    let freshReads = 0;
    const worker = recordCreation(page, (route, call) => {
      if (call.method !== 'profiles.list') return;
      if (worker.created()) {
        freshReads++;
        return;
      }
      older.push(route.request());
      requested.release();
      return release.promise.then(() => outcome === 'success'
        ? route.fulfill({ json: { result: olderSnapshot } })
        : failRead(route, 'FE-11 superseded profile request failed'));
    });
    await worker.ready;
    let created: Profile;
    try {
      await nav(page, 'team').click();
      await requested.promise;
      await expect(roster(page, existing)).toBeVisible();
      await createAgent(page, 'FE-11 new after held ' + outcome);
      await expect.poll(() => worker.created()?.id).toBeTruthy();
      created = worker.created()!;
      await showDetails(page, created);
      expect(older.length).toBeGreaterThan(0);
      expect(freshReads).toBeGreaterThan(0);
      expect(worker.calls.indexOf('profiles.list')).toBeLessThan(worker.calls.indexOf('profiles.create'));
      expect(worker.calls.lastIndexOf('profiles.list')).toBeGreaterThan(worker.calls.indexOf('profiles.create'));
    } finally {
      release.release();
    }
    await flushResponses(page, older);
    await expect(roster(page, created)).toBeVisible();
    await expectDetails(page, created);
    await expect(banner(page)).toHaveCount(0);
    await expectSelected(page, chat);
    expect(worker.calls).not.toContain('conversations.create');
  });
}

test('Team and Contracts share the created profile while a mission refresh is still pending', async ({ page }) => {
  const { existing, chat } = await seed(page);
  await home(page, existing);
  await nav(page, 'team').click();
  await showDetails(page, existing);
  await panel(page, existing).getByRole('button', { name: 'Close details' }).click();

  const missionRequested = gate();
  const releaseMission = gate();
  const missionReads: Request[] = [];
  let profilesAfterCreate = 0;
  const worker = recordCreation(page, (route, call) => {
    if (!worker.created()) return;
    if (call.method === 'profiles.list') profilesAfterCreate++;
    if (call.method === 'missions.list') {
      missionReads.push(route.request());
      missionRequested.release();
      return releaseMission.promise.then(() => route.continue());
    }
  });
  await worker.ready;
  let created: Profile;
  try {
    await createAgent(page, 'FE-11 shared profile snapshot');
    await missionRequested.promise;
    created = worker.created()!;
    await showDetails(page, created);
    expect(profilesAfterCreate).toBe(1);
    await expect(panel(page, existing)).toHaveCount(0);
  } finally {
    releaseMission.release();
  }
  await flushResponses(page, missionReads);
  await nav(page, 'contracts').click();
  await expect(contractRow(page, created)).toBeVisible();
  await contractRow(page, existing).click();
  await expectDetails(page, existing);
  await expect(panel(page, created)).toHaveCount(0);
  await contractRow(page, created).click();
  await expectDetails(page, created);
  await expectSelected(page, chat);
  expect(worker.calls).not.toContain('conversations.create');
});

test('a current profile failure keeps the last good snapshot and Retry loads the new one', async ({ page }) => {
  const { existing, chat } = await seed(page);
  const { conversations: before } = await rpc<{ conversations: Chat[] }>(page, 'conversations.list');
  await home(page, existing);
  await nav(page, 'team').click();
  await showDetails(page, existing);
  let failedReads = 0;
  let recoveryReads = 0;
  let fail = true;
  await page.route(callUrl, route => {
    const call = route.request().postDataJSON() as Rpc;
    if (call.method !== 'profiles.list') return route.continue();
    if (!fail) {
      recoveryReads++;
      return route.continue();
    }
    failedReads++;
    return failRead(route, 'FE-11 current profile request failed');
  });
  await nav(page, 'contracts').click();
  await expect(banner(page)).toContainText('FE-11 current profile request failed');
  expect(failedReads).toBeGreaterThan(0);
  await expect(contractRow(page, existing)).toBeVisible();
  await contractRow(page, existing).click();
  await expectDetails(page, existing);

  const updated = await rpc<Profile>(page, 'profiles.update', {
    profile_id: existing.id, name: 'FE-11 refreshed specialist', instructions: 'Refreshed specialist contract.',
  });
  await panel(page, existing).getByRole('button', { name: 'Close details' }).click();
  fail = false;
  await banner(page).getByRole('button', { name: 'Retry', exact: true }).click();
  await expect(banner(page)).toHaveCount(0);
  expect(recoveryReads).toBeGreaterThan(0);
  await expect(contractRow(page, updated)).toContainText(updated.name);
  await contractRow(page, updated).click();
  await expectDetails(page, updated);
  await expectSelected(page, chat);
  expect((await rpc<{ conversations: Chat[] }>(page, 'conversations.list')).conversations).toEqual(before);
});
