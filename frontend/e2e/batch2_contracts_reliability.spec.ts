import { expect, test, type Locator, type Page } from '@playwright/test';

const longModel = 'demo/' + 'extended-model-'.repeat(10) + 'stable';
const longName = 'Research and evaluation specialist for the university demonstration';
const instructions = 'Review the supplied material.\n'.repeat(45);
const agent = (id: string, name: string, model: string | null, engine: string | null = null) => ({
  id, name, description: 'Reviews evidence, methodology, and presentation readiness across the project.',
  role_type: 'custom', instructions: 'Review the source.', engine, default_model_id: model,
});
type Profile = ReturnType<typeof agent>;
type ProfileReply = { profiles: Profile[] } | { error: string };
const inherited = agent('inherited', longName, 'demo/model-retired');
const explicit = agent('explicit', 'Explicit specialist', longModel, 'mock');
const unavailable = agent('unavailable', 'Unavailable specialist', 'demo/model-unavailable', 'mock');
const unresolved = agent('unresolved', 'Unresolved specialist', null);
const profiles = [inherited, explicit, unavailable, unresolved];
const catalog = [{ id: 'demo/new-model', name: 'Workspace model' }, { id: longModel, name: 'Long explicit model' }];
const view = (page: Page) => page.locator('[data-view="contracts"]');
const region = (page: Page) => view(page).getByRole('region', { name: 'Contracts', exact: true });
const row = (page: Page, profile: Profile) => page.locator('[data-contract-row="' + profile.id + '"]');
const panel = (page: Page) => page.locator('[data-agent-panel]');
const openContracts = (page: Page) => page.getByRole('button', { name: 'Contracts', exact: true }).click();

function gate<T>() {
  let release!: (value: T) => void;
  const pending = new Promise<T>(resolve => { release = resolve; });
  return { pending, release };
}

async function setup(page: Page, options: {
  skin?: string;
  readProfiles?: () => Promise<ProfileReply> | ProfileReply;
  defaultKnown?: boolean;
} = {}) {
  const writes: Array<{ method: string; params: Record<string, unknown> }> = [];
  await page.addInitScript(skin => localStorage.setItem('rusebot-skin', skin), options.skin ?? 'meadow');
  const replies: Record<string, unknown> = {
    'conversations.list': { conversations: [] },
    'approvals.list': { approvals: [] },
    'ceo.actions': { actions: [] },
    'missions.list': { missions: [] },
    'engines.capabilities': { mock: { conversation_runs: true } },
    'engines.status': { engines: [] },
    'preferences.get': options.defaultKnown === false ? {} : { engine: 'mock', model_id: catalog[0].id },
    'models.free': { models: catalog },
    'engines.models': { models: catalog },
  };
  await page.route('**/__agentos/call', async route => {
    const { method, params } = route.request().postDataJSON() as { method: string; params: Record<string, unknown> };
    if (method === 'profiles.list') {
      const reply = await (options.readProfiles?.() ?? { profiles });
      return 'error' in reply
        ? route.fulfill({ status: 503, json: { detail: reply.error } })
        : route.fulfill({ json: { result: reply } });
    }
    if (method in replies) return route.fulfill({ json: { result: replies[method] } });
    if (/^(profiles\.(create|update|delete)|preferences\.set_default|runs\.start_turn|ceo\.ask)$/.test(method)) writes.push({ method, params });
    if (method === 'profiles.create') {
      return route.fulfill({ json: { result: agent('created', String(params.name), catalog[0].id) } });
    }
    return route.fulfill({ status: 422, json: { detail: 'Unused RPC blocked by Batch 2 fixture.' } });
  });
  await page.goto('/');
  await openContracts(page);
  return writes;
}

for (const [width, height, skin] of [[1440, 900, 'meadow'], [1280, 820, 'midnight'], [1100, 700, 'meadow']] as const) {
  test('Contracts list and details remain independently usable at ' + width + ' × ' + height, async ({ page }, info) => {
    await page.setViewportSize({ width, height });
    const roster = [...profiles, ...Array.from({ length: 21 }, (_, index) => agent('scroll-' + index, 'Agent ' + index, longModel, 'mock'))];
    await setup(page, { skin, readProfiles: () => ({ profiles: roster }) });
    await row(page, inherited).click();
    await expect(panel(page)).toBeVisible();
    await page.screenshot({ path: info.outputPath('contracts-open.png'), animations: 'disabled' });
    await expect.poll(async () => {
      const [list, details] = await Promise.all([region(page).boundingBox(), panel(page).boundingBox()]);
      return !list || !details ? 1 : Math.max(0, list.x + list.width - details.x);
    }).toBeLessThanOrEqual(1);
    await expect.poll(() => region(page).evaluate(e => e.scrollWidth - e.clientWidth)).toBe(0);
    await row(page, explicit).click();
    await expect(panel(page)).toHaveAttribute('data-agent-panel', explicit.id);
    await expect(panel(page)).toContainText(longModel);
    const body = panel(page).locator('.agent-details-body');
    await expect.poll(async () => {
      await body.evaluate(e => { e.scrollTop = e.scrollHeight; });
      return body.evaluate(e => e.scrollTop);
    }).toBeGreaterThan(0);
    await expect(panel(page).getByRole('button', { name: 'Delete agent' })).toBeInViewport();
    await row(page, roster.at(-1)!).scrollIntoViewIfNeeded();
    await expect(row(page, roster.at(-1)!)).toBeInViewport();
    await panel(page).getByRole('button', { name: 'Close details' }).press('Escape');
    await expect(panel(page)).toHaveCount(0);
    await row(page, inherited).click();
    await panel(page).getByRole('button', { name: 'Close details' }).click();
    await expect(panel(page)).toHaveCount(0);
  });
}

test('a long model leaves room for Agent identity without horizontal overflow', async ({ page }, info) => {
  await page.setViewportSize({ width: 1100, height: 700 });
  await setup(page);
  const long = row(page, explicit);
  await expect(long).toBeVisible();
  await page.screenshot({ path: info.outputPath('contracts-long-model.png'), animations: 'disabled' });
  const name = long.getByText(explicit.name, { exact: true });
  await expect.poll(async () => (await name.boundingBox())?.width ?? 0).toBeGreaterThan(100);
  await expect.poll(() => region(page).evaluate(e => e.scrollWidth - e.clientWidth)).toBe(0);
});

test('Contracts and details agree on inherited, explicit, and unavailable models', async ({ page }) => {
  const writes = await setup(page);
  for (const [profile, label] of [[inherited, 'Default · demo/new-model'], [explicit, longModel], [unavailable, 'demo/model-unavailable (unavailable)']] as const) {
    await expect(row(page, profile).getByText(label, { exact: true })).toBeAttached();
    await row(page, profile).click();
    await expect(panel(page).getByText(label, { exact: true })).toBeVisible();
    await panel(page).getByRole('button', { name: 'Close details' }).click();
  }
  expect(writes).toEqual([]);
});

test('Contracts reports an unresolved default without inventing a model', async ({ page }) => {
  const writes = await setup(page, { defaultKnown: false });
  await expect(row(page, unresolved).getByText('Default model (unresolved)', { exact: true })).toBeVisible();
  await row(page, unresolved).click();
  await expect(panel(page).getByText('Default model (unresolved)', { exact: true })).toBeVisible();
  await expect(row(page, inherited)).toContainText('demo/model-retired (unverified)');
  expect(writes).toEqual([]);
});

for (const confirmed of [[], [inherited]]) {
  test('Contracts distinguishes pending profiles from ' + (confirmed.length ? 'a populated response' : 'a confirmed empty response'), async ({ page }, info) => {
    const initial = gate<ProfileReply>();
    await setup(page, { readProfiles: () => initial.pending });
    await page.screenshot({ path: info.outputPath('contracts-loading.png'), animations: 'disabled' });
    await expect(view(page).getByRole('status')).toHaveText('Loading contracts…');
    await expect(view(page).getByText('0 members', { exact: true })).toHaveCount(0);
    initial.release({ profiles: confirmed });
    if (confirmed.length) {
      await expect(view(page).getByText('1 member', { exact: true })).toBeVisible();
      await expect(row(page, inherited)).toBeVisible();
    } else {
      await expect(view(page).getByText('0 members', { exact: true })).toBeVisible();
      await expect(view(page).getByText('No contracts yet.', { exact: true })).toBeVisible();
    }
    await expect(view(page).getByText('Loading contracts…', { exact: true })).toHaveCount(0);
  });
}

test('Contracts distinguishes initial failure and preserves the last confirmed roster during refresh', async ({ page }) => {
  let response: Promise<ProfileReply> = Promise.resolve({ error: 'Profiles temporarily unavailable.' });
  await setup(page, { readProfiles: () => response });
  const error = page.locator('[data-load-error]');
  await expect(error).toContainText('Profiles temporarily unavailable.');
  await expect(view(page).getByText('0 members', { exact: true })).toHaveCount(0);
  response = Promise.resolve({ profiles: [inherited] });
  await error.getByRole('button', { name: 'Retry' }).click();
  await expect(view(page).getByText('1 member', { exact: true })).toBeVisible();
  const refresh = gate<ProfileReply>();
  response = refresh.pending;
  await page.getByRole('button', { name: 'Team', exact: true }).click();
  await openContracts(page);
  await expect(row(page, inherited)).toBeVisible();
  await expect(view(page).getByText('1 member', { exact: true })).toBeVisible();
  refresh.release({ error: 'Refresh failed.' });
  await expect(error).toContainText('Refresh failed.');
  await expect(row(page, inherited)).toBeVisible();
  response = Promise.resolve({ profiles: [] });
  await error.getByRole('button', { name: 'Retry' }).click();
  await expect(view(page).getByText('0 members', { exact: true })).toBeVisible();
  await expect(error).toHaveCount(0);
});

async function openDialog(page: Page) {
  await view(page).getByRole('button', { name: 'New agent', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: 'New agent', exact: true });
  await expect(dialog.locator('#na-name')).toBeFocused();
  await expect(dialog.locator('#na-model')).toHaveAttribute('data-value', catalog[0].id);
  return dialog;
}

async function resizeInstructions(field: Locator) {
  const box = await field.boundingBox();
  if (!box) throw new Error('The instructions textarea is missing.');
  const page = field.page();
  const x = box.x + box.width - 4, y = box.y + box.height - 4;
  await page.mouse.move(x, y);
  await page.mouse.down();
  await page.mouse.move(x, y + 410, { steps: 12 });
  await page.mouse.up();
  await expect.poll(async () => (await field.boundingBox())?.height ?? 0).toBeGreaterThan(box.height + 150);
}

test('approved New Agent dialog supports long instructions and dropdowns at normal desktop size', async ({ page }, info) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await setup(page);
  const dialog = await openDialog(page);
  await page.screenshot({ path: info.outputPath('dialog-normal.png'), animations: 'disabled' });
  await dialog.locator('#na-instructions').fill(instructions);
  await dialog.locator('#na-engine').click();
  await expect(page.getByRole('listbox')).toBeInViewport();
  await page.getByRole('option').first().click();
  await dialog.locator('#na-model').click();
  await expect(page.getByRole('option', { name: 'Long explicit model' })).toBeInViewport();
  await page.screenshot({ path: info.outputPath('dialog-dropdown.png'), animations: 'disabled' });
  await page.getByRole('option', { name: 'Long explicit model' }).click();
  await dialog.getByRole('button', { name: 'Cancel', exact: true }).click();
  await expect(dialog).toHaveCount(0);
});

test('resized New Agent instructions keep the minimum-size dialog actions reachable', async ({ page }, info) => {
  await page.setViewportSize({ width: 1100, height: 700 });
  const writes = await setup(page, { skin: 'midnight' });
  const dialog = await openDialog(page);
  await page.screenshot({ path: info.outputPath('dialog-minimum.png'), animations: 'disabled' });
  const normalWidth = (await dialog.boundingBox())?.width;
  await dialog.locator('#na-name').fill('Batch 2 mock creation');
  await dialog.locator('#na-instructions').fill(instructions);
  await resizeInstructions(dialog.locator('#na-instructions'));
  await page.screenshot({ path: info.outputPath('dialog-resized.png'), animations: 'disabled' });
  await expect.poll(() => dialog.evaluate(e => {
    const { top, bottom } = e.getBoundingClientRect();
    return top >= 0 && bottom <= innerHeight;
  })).toBe(true);
  expect((await dialog.boundingBox())?.width).toBe(normalWidth);
  await dialog.getByRole('button', { name: 'Cancel', exact: true }).click();
  await expect(dialog).toHaveCount(0);
  await openDialog(page);
  await dialog.locator('#na-name').fill('Batch 2 mock creation');
  await dialog.locator('#na-instructions').fill(instructions);
  await resizeInstructions(dialog.locator('#na-instructions'));
  await dialog.locator('#na-model').click();
  await expect(page.getByRole('option', { name: 'Long explicit model' })).toBeInViewport();
  await page.screenshot({ path: info.outputPath('dialog-resized-dropdown.png'), animations: 'disabled' });
  await page.getByRole('option', { name: 'Long explicit model' }).click();
  await dialog.getByRole('button', { name: 'Create agent', exact: true }).click();
  await expect(dialog).toHaveCount(0);
  expect(writes).toEqual([{ method: 'profiles.create', params: {
    name: 'Batch 2 mock creation', role_type: 'custom', description: 'Agent',
    instructions: instructions.trim(), default_model_id: longModel,
  } }]);
});
