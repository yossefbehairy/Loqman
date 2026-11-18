import { expect, test, type Page } from '@playwright/test';
import { SKINS } from '../src/rusebot/lib/skins';

async function settings(page: Page, appearance = true) {
  if (page.viewportSize()!.width < 768 && await page.getByRole('button', { name: 'Open chat list' }).getAttribute('aria-expanded') !== 'true') await page.getByRole('button', { name: 'Open chat list' }).click();
  await page.getByRole('button', { name: 'Settings', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: 'Settings', exact: true, includeHidden: true });
  if (appearance) {
    if (page.viewportSize()!.width < 640) {
      await dialog.getByRole('combobox', { name: 'Settings page' }).click();
      await page.getByRole('option', { name: 'Appearance', exact: true }).click();
    } else await dialog.locator('[data-settings-page=appearance]').click();
  }
  return dialog;
}
async function start(page: Page, skin = 'midnight', width = 1440) {
  await page.setViewportSize({ width, height: 1000 });
  await page.addInitScript(skin => { if (!localStorage.getItem('rusebot-skin')) localStorage.setItem('rusebot-skin', skin); }, skin);
  await page.goto('/?ui=rusebot');
  await expect(page.locator('[data-rusebot-shell]')).toBeVisible();
}
const shot = async (page: Page, name: string) => page.screenshot({ path: `D:/LOQMAN/ui-review/appearance/${name}.png`, animations: 'disabled' });

test('registry gallery, scoped tokens, changes and preference persistence', async ({ page }) => {
  await start(page);
  let dialog = await settings(page);
  await expect(dialog.locator('[data-theme-card]')).toHaveCount(Math.min(3, SKINS.length));
  await shot(page, 'midnight');
  await dialog.getByRole('button', { name: `View all ${SKINS.length} themes` }).click();
  await expect(dialog.locator('[data-theme-card]')).toHaveCount(SKINS.length);
  await shot(page, 'all-themes');
  for (const theme of SKINS) {
    const preview = dialog.locator(`[data-theme-card=${theme.id}] .theme-workspace`);
    const color = await preview.evaluate(el => getComputedStyle(el).backgroundColor);
    await dialog.getByRole('button', { name: `Use ${theme.name} theme` }).click();
    await expect(page.locator('html')).toHaveAttribute('data-skin', theme.id);
    expect(await page.evaluate(() => getComputedStyle(document.body).backgroundColor)).toBe(color);
    await dialog.getByRole('button', { name: 'Show fewer themes' }).click();
    await expect(dialog.locator(`[data-theme-card=${theme.id}]`)).toHaveAttribute('aria-pressed', 'true');
    await expect(dialog.locator('[data-theme-card]')).toHaveCount(3);
    await dialog.getByRole('button', { name: /View all/ }).click();
  }
  await dialog.locator('[data-theme-card=atelier]').click();
  await dialog.getByRole('button', { name: 'Show fewer themes' }).click();
  await shot(page, 'light');
  await dialog.getByRole('combobox', { name: 'Font' }).click();
  await page.getByRole('option', { name: 'Poppins', exact: true }).click();
  await dialog.getByRole('button', { name: 'Large', exact: true }).click();
  await dialog.getByRole('button', { name: 'Full chat width' }).click();
  await dialog.locator('[data-theme-card=midnight]').click();
  await page.reload();
  await expect(page.locator('html')).toHaveAttribute('data-font', 'poppins');
  await expect(page.locator('html')).toHaveAttribute('data-text-size', 'large');
  await expect(page.locator('html')).toHaveAttribute('data-chat-width', 'full');
  dialog = await settings(page);
  await expect(dialog.getByRole('button', { name: 'Full chat width' })).toHaveAttribute('aria-pressed', 'true');
});

test('font pointer and keyboard preview never commits until selection; dismissal restores', async ({ page }) => {
  await start(page);
  const dialog = await settings(page);
  const trigger = dialog.getByRole('combobox', { name: 'Font', exact: true });
  const applied = () => page.evaluate(() => ({ font: getComputedStyle(document.body).fontFamily, saved: localStorage.getItem('rusebot-font') }));
  const before = await applied();
  await trigger.click();
  const option = page.getByRole('option', { name: 'Poppins', exact: true });
  await option.hover();
  await expect(dialog.locator('[data-font-preview]')).toHaveAttribute('data-font-preview', 'poppins');
  expect(await dialog.locator('[data-font-preview]').evaluate(el => getComputedStyle(el).fontFamily)).toContain('Poppins');
  expect(await applied()).toEqual(before);
  expect((await page.getByRole('listbox').boundingBox())!.width).toBeGreaterThanOrEqual(200);
  await shot(page, 'font-hover');
  await page.keyboard.press('Escape');
  await expect(dialog).toBeVisible();
  await expect(trigger).toBeFocused();
  await expect(dialog.locator('[data-font-preview]')).toHaveAttribute('data-font-preview', 'skin');
  await trigger.press('Space');
  await page.keyboard.press('End');
  await expect(dialog.locator('[data-font-preview]')).toHaveAttribute('data-font-preview', 'serif');
  expect(await applied()).toEqual(before);
  await page.keyboard.press('Enter');
  await expect(page.locator('html')).toHaveAttribute('data-font', 'serif');
  await trigger.click();
  await page.getByRole('option', { name: 'Inter', exact: true }).hover();
  await dialog.locator('#appearance-typography').click({ force: true });
  await expect(page.getByRole('listbox')).toHaveCount(0);
  await expect(dialog.locator('[data-font-preview]')).toHaveAttribute('data-font-preview', 'serif');
  await trigger.click();
  await page.getByRole('option', { name: 'Theme default', exact: true }).click();
  const midnightFont = await page.evaluate(() => getComputedStyle(document.body).fontFamily);
  await dialog.locator('[data-theme-card=atelier]').click();
  expect(await page.evaluate(() => getComputedStyle(document.body).fontFamily)).not.toBe(midnightFont);
  await expect(page.locator('html')).not.toHaveAttribute('data-font');
  await shot(page, 'bilingual-typography');
});

for (const width of [1440, 1280, 1024, 768, 390]) {
  test(`sizes widths and responsive containment ${width}`, async ({ page }) => {
    await start(page, 'midnight', width);
    const dialog = await settings(page);
    const heading = dialog.locator('#appearance-size');
    const base = await heading.evaluate(el => parseFloat(getComputedStyle(el).fontSize));
    for (const [size, scale] of [['Small', .93], ['Default', 1], ['Large', 1.12]] as const) {
      await dialog.getByRole('button', { name: size, exact: true }).click();
      expect(await heading.evaluate(el => parseFloat(getComputedStyle(el).fontSize))).toBeCloseTo(base * scale, 1);
      expect(await dialog.evaluate(el => el.scrollWidth <= el.clientWidth)).toBe(true);
      await shot(page, `text-${size.toLowerCase()}-${width}`);
    }
    for (const name of ['Standard', 'Wide', 'Full']) {
      await dialog.getByRole('button', { name: `${name} chat width` }).click();
      await expect(dialog.getByRole('button', { name: `${name} chat width` })).toHaveAttribute('aria-pressed', 'true');
      const max = await page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue('--chat-column-max').trim());
      expect(max).toBe(({ Standard: '60rem', Wide: '80rem', Full: '100%' })[name]);
      await shot(page, `width-${name.toLowerCase()}-${width}`);
    }
    await dialog.getByRole('button', { name: 'Close settings' }).click();
    const composer = page.locator('.mention-editor textarea');
    await composer.fill('A clear conversation\nمحادثة واضحة');
    expect(await composer.evaluate(el => el.clientHeight >= parseFloat(getComputedStyle(el).lineHeight))).toBe(true);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.reload();
    await expect(page.locator('html')).toHaveAttribute('data-text-size', 'large');
  });
}

test('profile photo text actions and removal persist across reloads in every theme', async ({ page }) => {
  await start(page);
  let dialog = await settings(page, false);
  await expect(dialog.getByRole('button', { name: 'Remove photo', exact: true })).toHaveCount(0);
  const data = await page.evaluate(() => { const c = document.createElement('canvas'); c.width = c.height = 64; const ctx = c.getContext('2d')!; ctx.fillStyle = '#8c7770'; ctx.fillRect(0, 0, 64, 64); return c.toDataURL('image/png').split(',')[1]; });
  await dialog.getByLabel('Your name', { exact: true }).fill('Sara');
  await dialog.getByLabel('Choose profile photo').setInputFiles({ name: 'avatar.png', mimeType: 'image/png', buffer: Buffer.from(data, 'base64') });
  await expect(dialog.getByRole('img', { name: 'Your profile photo' })).toBeVisible();
  for (const theme of SKINS) {
    await dialog.locator('[data-settings-page=appearance]').click();
    await dialog.getByRole('button', { name: /View all/ }).click();
    await dialog.locator(`[data-theme-card=${theme.id}]`).click();
    await dialog.locator('[data-settings-page=general]').click();
    await expect(dialog.getByRole('button', { name: 'Remove photo', exact: true })).toBeVisible();
    await expect(dialog.getByRole('button', { name: 'Change photo', exact: true }).locator('svg')).toHaveCount(0);
    const contrast = await dialog.getByRole('button', { name: 'Remove photo', exact: true }).evaluate(el => {
      const ctx = document.createElement('canvas').getContext('2d')!;
      const luminance = (color: string) => {
        ctx.fillStyle = color; ctx.fillRect(0, 0, 1, 1);
        const rgb = [...ctx.getImageData(0, 0, 1, 1).data].slice(0, 3).map(v => { const n = v / 255; return n <= .04045 ? n / 12.92 : ((n + .055) / 1.055) ** 2.4; });
        return rgb[0] * .2126 + rgb[1] * .7152 + rgb[2] * .0722;
      };
      const style = getComputedStyle(el);
      const fg = luminance(style.color), bg = luminance(style.backgroundColor);
      return (Math.max(fg, bg) + .05) / (Math.min(fg, bg) + .05);
    });
    expect(contrast, theme.name + ' remove photo text contrast').toBeGreaterThanOrEqual(4.5);
    await shot(page, `photo-${theme.id}`);
  }
  await page.reload();
  dialog = await settings(page, false);
  await expect(dialog.getByRole('img', { name: 'Your profile photo' })).toBeVisible();
  await dialog.getByRole('button', { name: 'Remove photo', exact: true }).click();
  await expect(dialog.locator('[data-user-avatar]')).toHaveText('S');
  await page.reload();
  dialog = await settings(page, false);
  await expect(dialog.getByRole('img', { name: 'Your profile photo' })).toHaveCount(0);
  await expect(dialog.getByRole('button', { name: 'Remove photo', exact: true })).toHaveCount(0);
});


test('invalid saved theme retains existing fallback and reduced-motion keyboard font menu fits mobile', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.addInitScript(() => { localStorage.setItem('rusebot-skin', 'missing-theme'); localStorage.setItem('rusebot-text-size', 'missing-size'); });
  await start(page, 'midnight', 390);
  await expect(page.locator('html')).toHaveAttribute('data-skin', 'meadow');
  await expect(page.locator('html')).toHaveAttribute('data-text-size', 'default');
  const dialog = await settings(page);
  const trigger = dialog.getByRole('combobox', { name: 'Font', exact: true });
  await trigger.press('Space');
  await page.keyboard.press('End');
  await expect(dialog.locator('[data-font-preview]')).toHaveAttribute('data-font-preview', 'serif');
  const menu = (await page.getByRole('listbox').boundingBox())!;
  const bounds = (await dialog.boundingBox())!;
  expect(menu.x).toBeGreaterThanOrEqual(bounds.x);
  expect(menu.x + menu.width).toBeLessThanOrEqual(bounds.x + bounds.width);
  expect(menu.y).toBeGreaterThanOrEqual(0);
  expect(menu.y + menu.height).toBeLessThanOrEqual(1000);
  await shot(page, 'font-narrow');
  await page.keyboard.press('Escape');
  await expect(trigger).toBeFocused();
});

for (const width of [1440, 390]) {
  test(`application text scale covers sidebar chat composer Team and AgentPanel ${width}`, async ({ page }) => {
    await page.route('**/__agentos/call', route => {
      const { method } = route.request().postDataJSON();
      if (method === 'profiles.list') return route.fulfill({ json: { result: { profiles: [
        { id: 'custom', name: 'Loqman', description: 'CEO', instructions: 'Coordinate.', role_type: 'ceo', settings_json: '{}' },
        { id: 'size-fixture', name: 'ليلى Designer', description: 'Product design and research', instructions: 'Existing contract.', role_type: 'custom', settings_json: '{}' },
      ] } } });
      return route.continue();
    });
    await start(page, 'midnight', width);
    const sidebar = page.locator('[data-sidebar]');
    const composer = page.locator('.mention-editor textarea');
    const font = async (selector: string) => page.locator(selector).first().evaluate(el => parseFloat(getComputedStyle(el).fontSize));
    const baseComposer = await font('.mention-editor textarea');
    const baseChat = await font('[data-chathead-pill]');
    const sidebarBase = await sidebar.evaluate(el => parseFloat(getComputedStyle(el).fontSize));
    for (const [name, scale] of [['Small', .93], ['Default', 1], ['Large', 1.12]] as const) {
      const dialog = await settings(page);
      await dialog.getByRole('button', { name, exact: true }).click();
      await dialog.getByRole('button', { name: 'Close settings' }).click();
      if (width < 768) await page.locator('[data-rusebot-shell] > div > div[aria-hidden="true"]').click({ position: { x: width - 1, y: 1 } });
      expect(await font('.mention-editor textarea')).toBeCloseTo(baseComposer * scale, 1);
      expect(await font('[data-chathead-pill]')).toBeCloseTo(baseChat * scale, 1);
      expect(await sidebar.evaluate(el => parseFloat(getComputedStyle(el).fontSize))).toBeCloseTo(sidebarBase * scale, 1);
      await composer.fill('English and العربية\nMore room for ideas.');
      expect(await composer.evaluate(el => el.scrollHeight <= el.clientHeight + 1)).toBe(true);
      await shot(page, `app-${name.toLowerCase()}-${width}`);
    }
    if (width < 768 && await page.getByRole('button', { name: 'Open chat list' }).getAttribute('aria-expanded') !== 'true') await page.getByRole('button', { name: 'Open chat list' }).click();
    await page.locator('[data-sidebar-nav=team]').click();
    await page.getByRole('button', { name: 'Open ليلى Designer', exact: true }).click();
    const panel = page.getByRole('complementary', { name: 'ليلى Designer details' });
    const panelBase = (await font('.agent-details-section h3')) / 1.12;
    for (const [name, scale] of [['Large', 1.12], ['Small', .93], ['Default', 1]] as const) {
      const dialog = await settings(page);
      await dialog.getByRole('button', { name, exact: true }).click();
      await dialog.getByRole('button', { name: 'Close settings' }).click();
      if (width < 768) await page.locator('[data-rusebot-shell] > div > div[aria-hidden="true"]').click({ position: { x: width - 1, y: 1 } });
      expect(await font('.agent-details-section h3')).toBeCloseTo(panelBase * scale, 1);
      expect(await font('.org-label strong')).toBeCloseTo(15 * scale, 1);
      expect(await panel.evaluate(el => el.scrollWidth <= el.clientWidth)).toBe(true);
      await panel.locator('#rb-contract').fill('A readable contract\nعقد واضح وسهل القراءة');
      const remove = panel.getByRole('button', { name: 'Delete agent' });
      await remove.scrollIntoViewIfNeeded();
      await expect(remove).toBeInViewport();
      await expect(panel.locator('.agent-details-header')).toBeInViewport();
      await shot(page, `team-panel-${name.toLowerCase()}-${width}`);
    }
  });
}

test('each text size and chat width restores the actual UI after refresh', async ({ page }) => {
  await start(page);
  for (const size of ['Small', 'Default', 'Large']) {
    const dialog = await settings(page);
    await dialog.getByRole('button', { name: size, exact: true }).click();
    await page.reload();
    await expect(page.locator('html')).toHaveAttribute('data-text-size', size.toLowerCase());
  }
  const columns: number[] = [];
  for (const name of ['Standard', 'Wide', 'Full']) {
    const dialog = await settings(page);
    await dialog.getByRole('button', { name: `${name} chat width` }).click();
    await page.reload();
    expect(await page.evaluate(() => localStorage.getItem('rusebot-chat-width'))).toBe(name.toLowerCase());
    await expect(page.locator('.chat-column').first()).toBeVisible();
    columns.push((await page.locator('.chat-column').first().boundingBox())!.width);
  }
  expect(columns[1]).toBeGreaterThan(columns[0]);
  expect(columns[2]).toBeGreaterThanOrEqual(columns[1]);
});

test('125 and 150 percent simulated display density retain usable settings', async ({ page, context }) => {
  await start(page);
  const cdp = await context.newCDPSession(page);
  for (const density of [1.25, 1.5]) {
    await page.setViewportSize({ width: Math.floor(1440 / density), height: Math.floor(1000 / density) });
    await cdp.send('Emulation.setDeviceMetricsOverride', { width: Math.floor(1440 / density), height: Math.floor(1000 / density), deviceScaleFactor: density, mobile: false });
    const dialog = await settings(page);
    await dialog.getByRole('button', { name: 'Large', exact: true }).click();
    expect(await dialog.evaluate(el => el.scrollWidth <= el.clientWidth)).toBe(true);
    const trigger = dialog.getByRole('combobox', { name: 'Font' });
    await trigger.click();
    await page.getByRole('option', { name: 'Poppins', exact: true }).hover();
    const menu = (await page.getByRole('listbox').boundingBox())!;
    expect(menu.x + menu.width).toBeLessThanOrEqual(Math.floor(1440 / density));
    await shot(page, `density-${density}`);
    await page.keyboard.press('Escape');
    await dialog.getByRole('button', { name: 'Close settings' }).click();
  }
  await cdp.detach();
});

test('blocked local storage still keeps the selected controls synchronized with the live document', async ({ page }) => {
  await page.addInitScript(() => { Storage.prototype.setItem = () => { throw new DOMException('Blocked', 'SecurityError'); }; });
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto('/?ui=rusebot');
  const dialog = await settings(page);
  await dialog.getByRole('button', { name: /View all/ }).click();
  await dialog.locator('[data-theme-card=midnight]').click();
  await expect(dialog.locator('[data-theme-card=midnight]')).toHaveAttribute('aria-pressed', 'true');
  await expect(page.locator('html')).toHaveAttribute('data-skin', 'midnight');
  await dialog.getByRole('combobox', { name: 'Font' }).click();
  await page.getByRole('option', { name: 'Serif', exact: true }).click();
  await expect(dialog.getByRole('combobox', { name: 'Font' })).toHaveAttribute('data-value', 'serif');
  await dialog.getByRole('button', { name: 'Large', exact: true }).click();
  await dialog.getByRole('button', { name: 'Wide chat width' }).click();
  await expect(dialog.getByRole('button', { name: 'Large', exact: true })).toHaveAttribute('aria-pressed', 'true');
  await expect(dialog.getByRole('button', { name: 'Wide chat width' })).toHaveAttribute('aria-pressed', 'true');
});
