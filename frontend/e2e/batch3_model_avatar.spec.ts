import { expect, test, type Locator, type Page } from '@playwright/test';

const ceo = { id: 'custom', name: 'Loqman', role_type: 'ceo', description: 'CEO', instructions: 'Coordinate.' };
const specialist = { id: 'specialist', name: 'Samira', role_type: 'custom', description: 'Researcher', instructions: 'Review notes.' };
const stamp = '2026-11-23T12:00:00Z';
const chats = [
  { id: 'workspace', title: 'Quarterly planning', profile_id: null },
  { id: 'ceo-bound', title: 'Roadmap review', profile_id: ceo.id },
  { id: 'research', title: 'Academic reading', profile_id: specialist.id },
  { id: 'evidence', title: 'Evidence review', profile_id: specialist.id },
  { id: 'hinted', title: 'Earlier specialist chat', profile_id: null },
  { id: 'deleted', title: 'Budget review', profile_id: 'deleted-agent' },
].map(chat => ({ ...chat, workspace_id: null, created_at: stamp, updated_at: stamp }));
const mainModel = { id: 'main', name: 'Workspace model' };
const alternative = { id: 'alternative', name: 'A long alternative model label for checking that the model dropdown keeps its existing width' };
const codexModel = { id: 'catalog-model', name: 'Recovered model' };
const execKey = 'agentos.rusebot.chatExecution.v1.workspace';
const model = (page: Page) => page.locator('#rb-model');
const add = (page: Page) => page.getByRole('button', { name: 'Add', exact: true });
const retry = (page: Page) => page.getByRole('button', { name: 'Retry model discovery', exact: true });
const row = (page: Page, id: string) => page.locator('[data-sidebar-thread-row="' + id + '"]');
const avatar = (page: Page, id: string) => row(page, id).locator('[data-agent-avatar]');
const send = (page: Page) => page.getByRole('button', { name: 'Send', exact: true });
type Catalog = { models: Array<{id: string; name: string}> } | { error: string };

function gate<T>() {
  let release!: (value: T) => void;
  const pending = new Promise<T>(resolve => { release = resolve; });
  return { pending, release };
}

async function setup(page: Page, options: {
  skin?: string;
  mockCatalog?: () => Catalog | Promise<Catalog>;
  codexCatalog?: () => Catalog | Promise<Catalog>;
  override?: string;
  missingDraft?: boolean;
  onlyMock?: boolean;
} = {}) {
  const catalogCalls: string[] = [], writes: string[] = [];
  await page.addInitScript(({ skin, override, missingDraft, execKey }) => {
    localStorage.setItem('rusebot-skin', skin);
    localStorage.setItem('agentos.rusebot.selected.v1', JSON.stringify(missingDraft ? { draftOwner: 'new:deleted-agent' } : 'workspace'));
    localStorage.setItem('agentos.rusebot.convAgents.v1', JSON.stringify({ research: 'custom', hinted: 'specialist', deleted: 'custom' }));
    localStorage.setItem('agentos_ceo_threads_v1', JSON.stringify([
      { id: 'older', question: 'Old project notes', reply: 'Historical response', ts: Date.UTC(2026, 10, 20) },
    ]));
    if (override) localStorage.setItem(execKey, JSON.stringify({ engine: 'mock', model: override }));
  }, { skin: options.skin ?? 'meadow', override: options.override, missingDraft: options.missingDraft, execKey });
  const replies: Record<string, unknown> = {
    'profiles.list': { profiles: [ceo, specialist] },
    'conversations.list': { conversations: chats },
    'conversations.messages': { messages: [] },
    'runs.list': { runs: [] },
    'approvals.list': { approvals: [] },
    'ceo.actions': { actions: [] },
    'missions.list': { missions: [] },
    'engines.capabilities': { mock: { conversation_runs: true }, ...(!options.onlyMock && { codex: { conversation_runs: true } }) },
    'engines.status': { engines: [{ engine_id: 'codex', title: 'Fixture Codex', install_state: 'installed', auth_state: 'authenticated' }] },
    'preferences.get': { engine: 'mock', model_id: mainModel.id },
  };
  await page.route('**/__agentos/call', async route => {
    const { method } = route.request().postDataJSON() as { method: string };
    if (method === 'models.free' || method === 'engines.models') {
      catalogCalls.push(method);
      const result = await (method === 'models.free'
        ? options.mockCatalog?.() ?? { models: [mainModel, alternative] }
        : options.codexCatalog?.() ?? { models: [codexModel] });
      return 'error' in result
        ? route.fulfill({ status: 503, json: { detail: result.error } })
        : route.fulfill({ json: { result } });
    }
    if (method in replies) return route.fulfill({ json: { result: replies[method] } });
    if (/^(conversations\.(add_message|bind_profile|create)|profiles\.(create|update|delete)|preferences\.set_default|runs\.start_turn|ceo\.ask|engines\.(test|open_setup))$/.test(method)) writes.push(method);
    return route.fulfill({ status: 422, json: { detail: 'Unused RPC blocked by Batch 3 fixture.' } });
  });
  await page.goto('/');
  await expect(row(page, 'workspace')).toBeVisible();
  return { writes, catalogCalls };
}

async function expectClosed(select: Locator) {
  await expect(select).toBeDisabled();
  await expect(select).toHaveAttribute('data-state', 'closed');
  await expect(select.page().getByRole('listbox')).toHaveCount(0);
}

async function expectBlockedSend(page: Page, writes: string[]) {
  await add(page).click();
  const input = page.locator('[data-tour="composer"]').getByRole('textbox');
  await input.fill('Keep this draft.');
  await expect(send(page)).toBeDisabled();
  await input.press('Enter');
  await expect(input).toHaveValue('Keep this draft.');
  expect(writes).toEqual([]);
}

test('a delayed model catalog displays loading and prevents an empty menu', async ({ page }, info) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  const pending = gate<Catalog>();
  try {
    const { catalogCalls, writes } = await setup(page, { mockCatalog: () => pending.pending, onlyMock: true });
    await expect.poll(() => catalogCalls).toContain('models.free');
    await add(page).click();
    await page.screenshot({ path: info.outputPath('model-loading.png'), animations: 'disabled' });
    await expect(model(page)).toContainText('Loading models…');
    await expectClosed(model(page));
    await expectBlockedSend(page, writes);
    pending.release({ models: [mainModel, alternative] });
    await expect(send(page)).toBeEnabled();
    await add(page).click();
    await expect(model(page)).toContainText(mainModel.name);
    expect(catalogCalls).toEqual(['models.free']);
    expect(writes).toEqual([]);
  } finally {
    pending.release({ models: [mainModel, alternative] });
  }
});

test('a successful empty workspace catalog displays a disabled empty state', async ({ page }, info) => {
  await page.setViewportSize({ width: 1280, height: 820 });
  const { writes } = await setup(page, { skin: 'midnight', onlyMock: true, mockCatalog: () => ({ models: [] }) });
  await expect(page.locator('[data-composer-no-provider]')).toBeVisible();
  await add(page).click();
  await expect(page.locator('#rb-engine')).toHaveAttribute('data-value', '');
  await page.screenshot({ path: info.outputPath('model-empty.png'), animations: 'disabled' });
  await expect(model(page)).toContainText('No models available');
  await expectClosed(model(page));
  await expect(retry(page)).toHaveCount(0);
  await expectBlockedSend(page, writes);
});

test('a rejected selected catalog has read-only Retry and recovers without dispatch', async ({ page }, info) => {
  await page.setViewportSize({ width: 1100, height: 700 });
  let catalog: Catalog | Promise<Catalog> = { error: 'Fixture discovery failed.' };
  const { writes, catalogCalls } = await setup(page, { skin: 'midnight', codexCatalog: () => catalog });
  await add(page).click();
  await expect(model(page)).toContainText(mainModel.name);
  await page.locator('#rb-engine').click();
  await page.getByRole('option', { name: 'Fixture Codex' }).click();
  await expect(page.locator('[data-composer-no-provider]')).toContainText('Could not load models');
  await page.screenshot({ path: info.outputPath('model-error.png'), animations: 'disabled' });
  await expect(model(page)).toContainText('Could not load models');
  await expectClosed(model(page));
  await expectBlockedSend(page, writes);
  await add(page).click();
  const pending = gate<Catalog>();
  catalog = pending.pending;
  try {
    await retry(page).click();
    await expect(model(page)).toContainText('Loading models…');
    await expectClosed(model(page));
    await expect(send(page)).toBeDisabled();
    pending.release({ models: [codexModel] });
    await expect(model(page)).toBeEnabled();
    await expect(model(page)).toContainText(codexModel.name);
    await expect(retry(page)).toHaveCount(0);
    await expect(send(page)).toBeEnabled();
    expect(catalogCalls.filter(call => call === 'engines.models')).toHaveLength(2);
    expect(writes).toEqual([]);
  } finally {
    pending.release({ models: [codexModel] });
  }
});

test('a workspace catalog failure is distinguishable from a successful empty result', async ({ page }) => {
  let catalog: Catalog = { error: 'Fixture workspace discovery failed.' };
  const { writes } = await setup(page, { onlyMock: true, mockCatalog: () => catalog });
  await expect(page.locator('[data-composer-no-provider]')).toBeVisible();
  await add(page).click();
  await expect(model(page)).toContainText('Could not load models');
  await expectClosed(model(page));
  catalog = { models: [mainModel] };
  await retry(page).click();
  await expect(model(page)).toContainText(mainModel.name);
  await expect(model(page)).toBeEnabled();
  expect(writes).toEqual([]);
});

for (const hasAlternatives of [true, false]) {
  test('a missing explicit model keeps its identity ' + (hasAlternatives ? 'with alternatives' : 'without alternatives'), async ({ page }, info) => {
    await page.setViewportSize({ width: 1100, height: 700 });
    const { writes } = await setup(page, {
      override: 'removed-model', onlyMock: true, mockCatalog: () => ({ models: hasAlternatives ? [mainModel] : [] }),
    });
    await add(page).click();
    await expect(page.locator('[data-composer-no-provider]')).toContainText('The selected model is unavailable.');
    await page.screenshot({ path: info.outputPath('model-unavailable.png'), animations: 'disabled' });
    await expect(model(page)).toHaveAttribute('data-value', 'removed-model');
    await expect(model(page)).toContainText('Unavailable · removed-model');
    const saved = () => page.evaluate(key => JSON.parse(localStorage.getItem(key) ?? 'null'), execKey);
    expect(await saved()).toEqual({ engine: 'mock', model: 'removed-model' });
    await expectBlockedSend(page, writes);
    await add(page).click();
    if (hasAlternatives) {
      await model(page).click();
      await page.getByRole('option', { name: mainModel.name }).click();
      await expect(model(page)).toHaveAttribute('data-value', mainModel.id);
      await expect(send(page)).toBeEnabled();
    } else {
      await expectClosed(model(page));
    }
    expect(writes).toEqual([]);
  });
}

test('normal model options preserve the Composer layout and allow an intentional selection', async ({ page }, info) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  const { writes } = await setup(page);
  const composer = page.locator('[data-tour="composer"]');
  await add(page).click();
  await expect(model(page)).toContainText(mainModel.name);
  const before = await composer.boundingBox();
  await model(page).click();
  await expect(page.getByRole('option', { name: alternative.name })).toBeInViewport();
  await page.screenshot({ path: info.outputPath('model-ready.png'), animations: 'disabled' });
  await page.getByRole('option', { name: alternative.name }).click();
  await expect(model(page)).toHaveAttribute('data-value', alternative.id);
  expect(await composer.boundingBox()).toEqual(before);
  expect(writes).toEqual([]);
});

test('an unresolved specialist draft has no invented model', async ({ page }) => {
  const { writes } = await setup(page, { missingDraft: true });
  await expect(page.getByRole('textbox', { name: 'Recipient unavailable', exact: true })).toBeVisible();
  await add(page).click();
  await expect(model(page)).toContainText('Agent defaults unavailable');
  await expect(model(page)).toHaveAttribute('data-value', '');
  await expectClosed(model(page));
  await expectBlockedSend(page, writes);
});

test('conversation avatars follow recipient metadata in both sidebar layouts', async ({ page }, info) => {
  await page.setViewportSize({ width: 1280, height: 820 });
  const { writes } = await setup(page, { skin: 'midnight' });
  await expect(row(page, 'workspace')).toHaveAttribute('aria-current', 'page');
  await page.screenshot({ path: info.outputPath('avatars-expanded.png'), animations: 'disabled' });
  for (const [id, glyph] of [['workspace', 'L'], ['ceo-bound', 'L'], ['research', 'S'], ['evidence', 'S'], ['hinted', 'S'], ['deleted', '?'], ['legacy:older', 'L']]) {
    await expect(avatar(page, id)).toHaveText(glyph);
  }
  await expect(avatar(page, 'deleted')).toHaveAttribute('data-agent-avatar', 'neutral');
  expect(await avatar(page, 'research').getAttribute('data-agent-avatar')).toBe(await avatar(page, 'evidence').getAttribute('data-agent-avatar'));
  await row(page, 'research').click();
  await expect(page.getByRole('textbox', { name: 'Message Samira', exact: true })).toBeVisible();
  await row(page, 'workspace').click();
  await expect(page.getByRole('textbox', { name: 'Message Loqman', exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Collapse sidebar', exact: true }).click();
  await expect(row(page, 'workspace')).toHaveAccessibleName('Quarterly planning');
  await expect(avatar(page, 'workspace')).toHaveText('L');
  await expect(avatar(page, 'research')).toHaveText('S');
  await expect(avatar(page, 'deleted')).toHaveText('?');
  await page.screenshot({ path: info.outputPath('avatars-collapsed.png'), animations: 'disabled' });
  await page.getByRole('button', { name: 'Search chats', exact: true }).click();
  await page.getByRole('textbox', { name: 'Search chats', exact: true }).fill('Evidence');
  await expect(row(page, 'workspace')).toHaveCount(0);
  await expect(avatar(page, 'evidence')).toHaveText('S');
  await row(page, 'evidence').click();
  await expect(page.getByRole('textbox', { name: 'Message Samira', exact: true })).toBeVisible();
  expect(writes).toEqual([]);
});
