import { expect, test, type Locator, type Page } from '@playwright/test';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const timestamp = '2026-11-23T12:00:00Z';
const specialist = {
  id: 'batch-specialist',
  name: 'ResearchSpecialistWithAnIntentionallyLongUnbrokenName',
  role_type: 'custom',
  description: 'Research reviewer',
  instructions: 'Review notes.',
  engine: 'codex',
  default_model_id: 'specialist-model',
};
const chats = [
  { id: 'batch-specialist-chat', title: 'Research notes', profile_id: specialist.id },
  { id: 'batch-ceo-chat', title: 'Workspace notes', profile_id: 'custom' },
].map(chat => ({ ...chat, workspace_id: null, created_at: timestamp, updated_at: timestamp }));
const row = (page: Page, index: number) => page.locator('[data-sidebar-thread-row="' + chats[index].id + '"]');
const searchButton = (page: Page) => page.getByRole('button', { name: 'Search chats', exact: true });
const searchInput = (page: Page) => page.getByRole('textbox', { name: 'Search chats' });
const menu = (page: Page) => page.getByRole('menu', { name: 'Create' });
const panel = (page: Page) => page.locator('[data-agent-panel]');
const newButton = (page: Page) => page.getByRole('button', { name: 'New', exact: true });
const go = (page: Page, view: string) => page.locator('[data-sidebar-nav="' + view + '"]').click();
const choice = (page: Page, pair: string) => page.locator('[data-chathead-pill]').locator('[title="' + pair + '"]');
const recipient = (page: Page, name: string) => menu(page).getByRole('menuitem', { name: 'New chat with ' + name, exact: true });
const composer = (page: Page, name: string) => page.getByRole('textbox', { name: 'Message ' + name, exact: true });

async function openWorkspace(page: Page, skin = 'meadow') {
  const writes: string[] = [];
  let specialistPresent = true;
  await page.addInitScript(({ id, skin }) => {
    localStorage.setItem('agentos.rusebot.selected.v1', JSON.stringify(id));
    localStorage.setItem('rusebot-skin', skin);
  }, { id: chats[0].id, skin });
  const replies: Record<string, unknown> = {
    'conversations.list': { conversations: chats },
    'conversations.messages': { messages: [] },
    'runs.list': { runs: [] },
    'approvals.list': { approvals: [] },
    'ceo.actions': { actions: [] },
    'missions.list': { missions: [] },
    'deliverables.list_all': { drafts: [] },
    'engines.capabilities': { mock: { conversation_runs: true }, codex: { conversation_runs: true } },
    'engines.status': { engines: [{ engine_id: 'codex', title: 'Fixture Codex', install_state: 'installed', auth_state: 'authenticated' }] },
    'preferences.get': { engine: 'mock', model_id: 'workspace-model' },
    'models.free': { models: [{ id: 'workspace-model', name: 'Workspace model' }] },
    'engines.models': { models: [{ id: 'specialist-model', name: 'Specialist model' }, { id: 'override-model', name: 'Override model' }] },
  };
  await page.route('**/__agentos/call', route => {
    const { method } = route.request().postDataJSON() as { method: string };
    if (method === 'profiles.list') {
      const ceo = { id: 'custom', name: 'Loqman', role_type: 'ceo', description: 'CEO', instructions: 'Coordinate.' };
      return route.fulfill({ json: { result: { profiles: specialistPresent ? [ceo, specialist] : [ceo] } } });
    }
    if (method in replies) return route.fulfill({ json: { result: replies[method] } });
    if (['conversations.create', 'conversations.add_message', 'runs.start_turn', 'ceo.ask', 'preferences.set_default'].includes(method)) writes.push(method);
    return route.fulfill({ status: 422, json: { detail: 'Unused RPC blocked by Batch 1 fixture.' } });
  });
  await page.goto('/');
  await expect(row(page, 0)).toHaveAttribute('aria-current', 'page');
  await expect(choice(page, 'codex · specialist-model')).toBeVisible();
  return { writes, removeSpecialist: () => { specialistPresent = false; } };
}

async function insideViewport(locator: Locator) {
  await expect(locator).toBeVisible();
  await expect.poll(() => locator.evaluate(element => {
    const { left, top, right, bottom } = element.getBoundingClientRect();
    return left >= 0 && top >= 0 && right <= innerWidth && bottom <= innerHeight;
  })).toBe(true);
}

async function openDetails(page: Page) {
  await go(page, 'team');
  await page.getByRole('button', { name: 'Open ' + specialist.name, exact: true }).click();
  await expect(panel(page)).toBeVisible();
  await expect.poll(async () => (await page.locator('[data-sidebar]').boundingBox())?.width).toBe(92);
}

for (const [width, height, mode, skin] of [
  [1440, 900, 'expanded', 'meadow'],
  [1280, 820, 'manual rail', 'midnight'],
  [1100, 700, 'manual rail', 'meadow'],
  [1100, 700, 'forced rail', 'midnight'],
] as const) {
  test('New popup stays in the viewport at ' + width + ' × ' + height + ' with ' + mode, async ({ page }, info) => {
    await page.setViewportSize({ width, height });
    await openWorkspace(page, skin);
    if (mode === 'manual rail') await page.getByRole('button', { name: 'Collapse sidebar' }).click();
    if (mode === 'forced rail') await openDetails(page);
    await newButton(page).click();
    await page.screenshot({ path: info.outputPath('new-menu.png'), animations: 'disabled' });
    await insideViewport(menu(page));
    for (const action of await menu(page).getByRole('menuitem').all()) {
      await insideViewport(action);
      await action.click({ trial: true });
    }
    await page.keyboard.press('Escape');
    await expect(menu(page)).toHaveCount(0);
    await newButton(page).click();
    await page.locator('main').click({ position: { x: 360, y: 55 } });
    await expect(menu(page)).toHaveCount(0);
    await newButton(page).click();
    await menu(page).getByRole('menuitem', { name: 'New agent', exact: true }).click();
    await expect(page.getByRole('dialog', { name: /new agent/i })).toBeVisible();
    await page.keyboard.press('Escape');
    await newButton(page).click();
    await recipient(page, 'Loqman').click();
    await expect(composer(page, 'Loqman')).toBeVisible();
  });
}

for (const mode of ['expanded', 'manual rail', 'forced rail'] as const) {
  test('Search filters and opens the correct conversation from ' + mode, async ({ page }, info) => {
    await page.setViewportSize({ width: 1100, height: 700 });
    await openWorkspace(page, mode === 'forced rail' ? 'midnight' : 'meadow');
    if (mode === 'manual rail') await page.getByRole('button', { name: 'Collapse sidebar' }).click();
    if (mode === 'forced rail') await openDetails(page);
    const toggle = searchButton(page);
    await toggle.click();
    await page.screenshot({ path: info.outputPath('search-open.png'), animations: 'disabled' });
    await expect(searchInput(page)).toBeVisible();
    await expect(searchInput(page)).toBeFocused();
    await expect(toggle).toHaveAttribute('aria-expanded', 'true');
    if (mode === 'forced rail') await expect(panel(page)).toBeVisible();
    if (mode === 'manual rail') await expect(page.getByRole('button', { name: 'Collapse sidebar' })).toBeVisible();
    await searchInput(page).fill('Workspace');
    await expect(row(page, 0)).toHaveCount(0);
    await expect(row(page, 1)).toBeVisible();
    await page.screenshot({ path: info.outputPath('search-filtered.png'), animations: 'disabled' });
    await searchInput(page).press('Escape');
    await expect(toggle).toHaveAttribute('aria-expanded', 'false');
    await expect(searchInput(page)).toHaveCount(0);
    await toggle.click();
    await expect(searchInput(page)).toHaveValue('');
    if (mode === 'forced rail') {
      await panel(page).getByRole('heading', { name: specialist.name }).click();
      await expect(panel(page)).toBeVisible();
      await expect(toggle).toHaveAttribute('aria-expanded', 'false');
      await toggle.click();
    }
    await page.getByRole('button', { name: 'Close search' }).click();
    await expect(searchInput(page)).toHaveCount(0);
    await toggle.click();
    await searchInput(page).fill('Workspace');
    await row(page, 1).click();
    await expect(row(page, 1)).toHaveAttribute('aria-current', 'page');
    await expect(composer(page, 'Loqman')).toBeVisible();
  });
}

test('global New chat choices use the real recipient and preserve saved overrides', async ({ page }) => {
  const { writes, removeSpecialist } = await openWorkspace(page);
  await page.getByRole('button', { name: 'Add', exact: true }).click();
  await page.locator('#rb-model').click();
  await page.getByRole('option', { name: 'Override model' }).click();
  await expect(choice(page, 'codex · override-model')).toBeVisible();

  for (const view of ['team', 'contracts', 'results']) {
    await go(page, view);
    await newButton(page).click();
    await expect(menu(page).getByRole('menuitem')).toHaveText([
      'New chat with Loqman', 'New chat with ' + specialist.name, 'New agent',
    ]);
    await recipient(page, 'Loqman').click();
    await expect(composer(page, 'Loqman')).toBeVisible();
    await expect(choice(page, 'mock · workspace-model')).toBeVisible();
    await expect(page.locator('[data-chathead-conversation]')).toHaveText('New chat');
    await newButton(page).click();
    await expect(menu(page).getByRole('menuitem')).toHaveText(['New chat with Loqman', 'New agent']);
    await page.keyboard.press('Escape');
    await row(page, 0).click();
    await expect(choice(page, 'codex · override-model')).toBeVisible();
  }

  await go(page, 'results');
  await newButton(page).click();
  await recipient(page, specialist.name).click();
  await expect(composer(page, specialist.name)).toBeVisible();
  await expect(choice(page, 'codex · specialist-model')).toBeVisible();
  await expect(page.locator('[data-chathead-conversation]')).toHaveText('New chat');
  await row(page, 0).click();
  await expect(choice(page, 'codex · override-model')).toBeVisible();

  removeSpecialist();
  await go(page, 'team');
  await expect(page.locator('[data-agent-card="' + specialist.id + '"]')).toHaveCount(0);
  await newButton(page).click();
  await expect(menu(page).getByRole('menuitem')).toHaveText(['New chat with Loqman', 'New agent']);
  expect(writes).toEqual([]);
});

test('startup splash uses the Loqman wordmark and fits 460 × 300', async ({ page }, info) => {
  const splash = pathToFileURL(path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../electron/splash.html'));
  await page.setViewportSize({ width: 460, height: 300 });
  await page.goto(splash.href);
  const heading = page.getByRole('heading', { level: 1 });
  await page.screenshot({ path: info.outputPath('splash.png'), animations: 'disabled' });
  await expect(heading).toHaveText('Loqman');
  await expect(page.getByText('AgentOS', { exact: true })).toHaveCount(0);
  await expect(page.locator('#status')).toHaveText('Booting Loqman…');
  await insideViewport(heading);
  await insideViewport(page.locator('.bar'));
  await insideViewport(page.locator('#status'));
});
