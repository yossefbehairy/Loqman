import { expect, test, type Locator, type Page } from '@playwright/test';

const api = '**/__agentos/call';
const nav = (page: Page, id: string) => page.locator('[data-sidebar-nav=' + id + ']');
const sidebar = (page: Page) => page.locator('aside[data-sidebar]');
const drawer = (page: Page) => page.getByRole('button', { name: 'Open chat list' });
const transcript = (page: Page) => page.getByRole('log');
const historyLoading = (page: Page) => transcript(page).getByRole('status').filter({ hasText: 'Loading conversation…' });
const empty = (page: Page) => transcript(page).getByText('Send a message to start the conversation.');
const settings = (page: Page) => page.getByRole('button', { name: 'Settings', exact: true });

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

async function saved(page: Page, title: string, text?: string) {
  const item = await rpc<{ id: string }>(page, 'conversations.create', { title });
  if (text) await rpc(page, 'conversations.add_message', { conversation_id: item.id, role: 'user', content: text });
  return item.id;
}

async function open(page: Page) {
  await page.goto('/?ui=rusebot');
  await expect(page.locator('[data-chathead-pill]')).toContainText('mock', { timeout: 30_000 });
}

function holdHistory(page: Page, ids: string[]) {
  const holds = new Map(ids.map(id => [id, { entered: gate(), release: gate(), delivered: gate() }]));
  return page.route(api, async route => {
    const { method, params } = route.request().postDataJSON() as { method: string; params: { conversation_id?: string } };
    const hold = method === 'conversations.messages' && params.conversation_id ? holds.get(params.conversation_id) : undefined;
    if (!hold) return route.continue();
    holds.delete(params.conversation_id!);
    const response = await route.fetch();
    hold.entered.release();
    await hold.release.promise;
    await route.fulfill({ response });
    hold.delivered.release();
  }).then(() => new Map(holds));
}

const selectChat = (page: Page, id: string) => page.locator('[data-sidebar-thread-row="' + id + '"]').click();

async function checkWrap(page: Page, dialog: Locator) {
  const stops = dialog.locator('button:enabled:visible, input:enabled:visible, textarea:enabled:visible, select:enabled:visible, a[href]:visible');
  const first = stops.first();
  const last = stops.last();
  await first.focus();
  await page.keyboard.press('Shift+Tab');
  await expect(last).toBeFocused();
  await page.keyboard.press('Tab');
  await expect(first).toBeFocused();
}

async function openAgentDialog(page: Page) {
  await page.getByRole('button', { name: 'New', exact: true }).click();
  await page.getByRole('menuitem', { name: 'New agent' }).press('Enter');
  return page.getByRole('dialog', { name: 'New agent', exact: true });
}

test('FE-08: a pending saved history shows Loading before its persisted messages', async ({ page }) => {
  const id = await saved(page, 'FE-08 saved transcript', 'A persisted user message');
  const pending = (await holdHistory(page, [id])).get(id)!;
  await open(page);
  await expect(page.locator('[data-chathead-conversation]')).toHaveText('New chat');
  await expect(historyLoading(page)).toHaveCount(0);
  await selectChat(page, id);
  try {
    await pending.entered.promise;
    await expect(historyLoading(page)).toBeVisible();
    await expect(empty(page)).toHaveCount(0);
  } finally {
    pending.release.release();
  }
  await expect(transcript(page).getByText('A persisted user message')).toBeVisible();
  await expect(historyLoading(page)).toHaveCount(0);
});

test('FE-08: a confirmed empty saved history shows the existing empty state', async ({ page }) => {
  const id = await saved(page, 'FE-08 confirmed empty history');
  await open(page);
  await selectChat(page, id);
  await expect(empty(page)).toBeVisible();
  await expect(historyLoading(page)).toHaveCount(0);
});

test('FE-08: switching chats during history reads keeps the new transcript isolated', async ({ page }) => {
  const a = await saved(page, 'FE-08 older response', 'Only conversation A contains this text');
  const b = await saved(page, 'FE-08 current response', 'Conversation B is current');
  const holds = await holdHistory(page, [a, b]);
  const first = holds.get(a)!;
  const second = holds.get(b)!;
  await open(page);
  try {
    await selectChat(page, a);
    await first.entered.promise;
    await selectChat(page, b);
    await second.entered.promise;
    second.release.release();
    await expect(transcript(page).getByText('Conversation B is current')).toBeVisible();
    first.release.release();
    await first.delivered.promise;
    await page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
    await expect(transcript(page).getByText('Only conversation A contains this text')).toHaveCount(0);
    await expect(transcript(page).getByText('Conversation B is current')).toBeVisible();
  } finally {
    first.release.release();
    second.release.release();
  }
});

test('FE-07: Settings focuses a visible control and Escape restores its trigger', async ({ page }) => {
  await open(page);
  const button = settings(page);
  await button.press('Enter');
  const dialog = page.getByRole('dialog', { name: 'Settings' });
  await expect(dialog.getByRole('textbox', { name: 'Search settings' })).toBeFocused();
  await page.keyboard.press('Escape');
  await expect(dialog).toHaveCount(0);
  await expect(button).toBeFocused();
});

test('FE-07: Settings wraps Tab and Shift+Tab inside its dialog', async ({ page }) => {
  await open(page);
  await settings(page).click();
  await checkWrap(page, page.getByRole('dialog', { name: 'Settings' }));
});

test('FE-07: New Agent initially focuses Name and wraps Tab in both directions', async ({ page }) => {
  await open(page);
  const dialog = await openAgentDialog(page);
  await expect(dialog.getByRole('textbox', { name: 'Name', exact: true })).toBeFocused();
  await checkWrap(page, dialog);
});

test('FE-07: closing New Agent restores the surviving New menu trigger', async ({ page }) => {
  await open(page);
  const dialog = await openAgentDialog(page);
  await dialog.getByRole('button', { name: 'Close', exact: true }).click();
  await expect(dialog).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'New', exact: true })).toBeFocused();
});

test('FE-07: the mobile Settings page picker still supports keyboard selection and nested Escape', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 760 });
  await open(page);
  await drawer(page).click();
  await settings(page).click();
  const dialog = page.getByRole('dialog', { name: 'Settings' });
  const picker = dialog.getByRole('combobox', { name: 'Settings page' });
  await picker.press('Space');
  await expect(page.getByRole('listbox')).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.getByRole('listbox')).toHaveCount(0);
  await expect(picker).toBeFocused();
  await picker.press('Space');
  await expect(page.getByRole('option', { name: 'General', exact: true })).toBeFocused();
  await page.keyboard.press('End');
  await expect(page.getByRole('option', { name: 'About', exact: true })).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(page.getByRole('listbox')).toHaveCount(0);
  await expect(picker).toHaveAttribute('data-value', 'about');
  await expect(dialog).toBeVisible();
});

test('FE-12: Tab skips the closed mobile sidebar and reaches it when opened', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 760 });
  await open(page);
  const toggle = drawer(page);
  await toggle.focus();
  await page.keyboard.press('Tab');
  expect(await sidebar(page).evaluate(el => el.contains(document.activeElement))).toBe(false);
  await toggle.press('Enter');
  await expect(toggle).toHaveAttribute('aria-expanded', 'true');
  await page.keyboard.press('Tab');
  await expect(sidebar(page).getByRole('button', { name: 'Search chats' })).toBeFocused();
});

test('FE-12: sidebar navigation closes the mobile drawer and returns its focus to the toggle', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 760 });
  await open(page);
  await drawer(page).click();
  const team = nav(page, 'team');
  await team.focus();
  await team.press('Enter');
  await expect(page.getByRole('heading', { name: 'Team', exact: true })).toBeVisible();
  await expect(drawer(page)).toHaveAttribute('aria-expanded', 'false');
  await expect(drawer(page)).toBeFocused();
});

test('FE-12: desktop sidebar remains reachable by Tab', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await open(page);
  const search = sidebar(page).getByRole('button', { name: 'Search chats' });
  await search.focus();
  await page.keyboard.press('Tab');
  await expect(sidebar(page).getByRole('button', { name: 'New', exact: true })).toBeFocused();
});
