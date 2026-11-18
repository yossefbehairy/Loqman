import { expect, test, type Page } from '@playwright/test';
import { SKINS } from '../src/rusebot/lib/skins';

const output = 'D:/LOQMAN/ui-review/agent-panel-cohesive';
const longModel = 'deepseek-v4.1-flash-free-' + 'extended-context-'.repeat(9);
async function openPanel(page: Page, skin = 'midnight', width = 1440, height = 1000, long = false) {
  const name = long ? 'ليلى — تصميم تجربة المستخدم والبحث في المنتجات الطويلة' : 'ANYA';
  await page.setViewportSize({ width, height });
  await page.addInitScript(({ skin, long }) => {
    localStorage.setItem('rusebot-skin', skin);
    localStorage.setItem('agentos.rusebot.rail.v1', '1');
    localStorage.setItem('rusebot-text-size', long ? 'large' : 'default');
  }, { skin, long });
  const profiles = [
    { id: 'custom', name: 'Loqman', description: 'CEO', instructions: 'Coordinate.', role_type: 'ceo', settings_json: '{}' },
    { id: 'panel-design-fixture', name, description: long ? 'مصممة تجربة المستخدم — Product research and accessibility' : 'Product design & research', instructions: 'Make each interaction clear and useful.\nاجعل كل تفاعل واضحاً ومفيداً.', role_type: 'custom', settings_json: '{}', ...(long ? { engine: 'layout-fixture', default_model_id: longModel } : {}) },
  ];
  const calls: string[] = [];
  await page.route('**/__agentos/call', route => {
    const { method } = route.request().postDataJSON();
    calls.push(method);
    if (method === 'profiles.list') return route.fulfill({ json: { result: { profiles } } });
    if (method === 'missions.list') return route.fulfill({ json: { result: { missions: [] } } });
    if (method === 'ceo.actions') return route.fulfill({ json: { result: { actions: [] } } });
    return route.continue();
  });
  await page.goto('/?ui=rusebot');
  await expect(page.locator('[data-chathead-pill]')).toContainText('mock', { timeout: 30000 });
  if (width < 768) await page.getByRole('button', { name: 'Open chat list' }).click();
  await page.locator('[data-sidebar-nav=team]').click();
  const trigger = page.getByRole('button', { name: `Open ${name}`, exact: true });
  await trigger.click();
  const panel = page.getByRole('complementary', { name: `${name} details`, exact: true });
  await expect(panel.getByLabel('Close details')).toBeFocused();
  return { panel, trigger, calls };
}

for (const theme of SKINS) {
  test(`cohesive panel presentation and theme tokens: ${theme.name}`, async ({ page }) => {
    const { panel, calls } = await openPanel(page, theme.id);
    const header = panel.locator('.agent-details-header');
    const body = panel.locator('.agent-details-body');
    const style = await header.evaluate(el => {
      const s = getComputedStyle(el), p = getComputedStyle(el.parentElement!);
      return { background: s.backgroundColor, panel: p.backgroundColor, radius: s.borderRadius, shadow: s.boxShadow, panelShadow: p.boxShadow, border: s.borderBottomWidth, padding: s.paddingLeft };
    });
    expect(style.background).toBe(style.panel);
    expect(style.radius).toBe('0px');
    expect(style.shadow).toBe('none');
    expect(style.panelShadow).toBe('none');
    expect(style.border).toBe('1px');
    expect(await body.evaluate(el => getComputedStyle(el).paddingLeft)).toBe(style.padding);
    await expect(panel.locator('h3 svg, dt svg, dd svg')).toHaveCount(0);
    await expect(panel.locator('h3')).toHaveText(['Overview', 'Contract', 'Delegate a task']);
    await expect(panel.locator('dt')).toHaveText(['Status', 'Model', 'Effort', 'Timeout']);
    await expect(panel.locator('[data-model-note]')).toContainText('Used for delegated tasks.');
    for (const name of ['Save contract', 'Delegate']) {
      const button = panel.getByRole('button', { name, exact: true });
      await expect(button).toBeDisabled();
      const width = await button.evaluate(el => ({ button: el.getBoundingClientRect().width, section: el.parentElement!.getBoundingClientRect().width }));
      expect(width.button).toBeCloseTo(width.section, 0);
    }
    const save = (await panel.getByRole('button', { name: 'Save contract' }).boundingBox())!;
    const delegate = (await panel.getByRole('button', { name: 'Delegate', exact: true }).boundingBox())!;
    expect(save.width).toBe(delegate.width);
    expect(save.height).toBe(delegate.height);
    const chat = panel.getByRole('button', { name: 'Open chat', exact: true });
    const colors = await chat.evaluate(el => ({ actual: getComputedStyle(el).backgroundColor, expected: getComputedStyle(el).getPropertyValue('--color-accent').trim() }));
    expect(colors.actual).toBe(await chat.evaluate((el, expected) => { const probe = document.createElement('span'); probe.style.color = expected; el.append(probe); const color = getComputedStyle(probe).color; probe.remove(); return color; }, colors.expected));
    const remove = panel.getByRole('button', { name: 'Delete agent', exact: true });
    await expect(remove.locator('svg')).toHaveCount(0);
    expect(await remove.evaluate(el => el.parentElement === el.closest('.agent-details-body')!.lastElementChild)).toBe(true);
    expect(calls.filter(method => /conversations.create|profiles.update|profiles.delete|missions.delegate/.test(method))).toEqual([]);
    expect(await panel.evaluate(el => el.scrollWidth <= el.clientWidth)).toBe(true);
    await page.screenshot({ path: `${output}/${theme.id}.png`, animations: 'disabled' });
    await remove.scrollIntoViewIfNeeded();
    await expect(remove).toBeInViewport();
    const contrast = await remove.evaluate(el => {
      const ctx = document.createElement('canvas').getContext('2d')!;
      const lum = (value: string) => { ctx.fillStyle = value; ctx.fillRect(0, 0, 1, 1); const rgb = [...ctx.getImageData(0, 0, 1, 1).data].slice(0, 3).map(v => { const n = v / 255; return n <= .04045 ? n / 12.92 : ((n + .055) / 1.055) ** 2.4; }); return rgb[0] * .2126 + rgb[1] * .7152 + rgb[2] * .0722; };
      const s = getComputedStyle(el), a = lum(s.color), b = lum(s.backgroundColor);
      return (Math.max(a, b) + .05) / (Math.min(a, b) + .05);
    });
    expect(contrast).toBeGreaterThanOrEqual(4.5);
  });
}

for (const width of [1440, 1280, 1024, 768, 390]) {
  test(`sticky header, long bilingual content and full-width actions at ${width}px`, async ({ page }) => {
    const { panel, trigger, calls } = await openPanel(page, 'midnight', width, 610, true);
    const header = panel.locator('.agent-details-header');
    const headerY = (await header.boundingBox())!.y;
    await expect(panel.locator('h2')).toContainText('ليلى');
    await expect(panel.getByText(longModel, { exact: true })).toBeVisible();
    for (const selector of ['.agent-details-body', '.agent-details-fields', '.agent-details-identity']) {
      expect(await panel.locator(selector).evaluate(el => el.scrollWidth <= el.clientWidth)).toBe(true);
    }
    await page.screenshot({ path: `${output}/long-${width}.png`, animations: 'disabled' });
    await panel.locator('#rb-contract').fill('Updated but unsaved.\nتغييرات غير محفوظة.');
    await expect(panel.getByRole('button', { name: 'Save contract' })).toBeEnabled();
    await panel.locator('#rb-delegate').fill('   ');
    await expect(panel.getByRole('button', { name: 'Delegate', exact: true })).toBeDisabled();
    await panel.locator('#rb-delegate').fill('Review the design — راجع التصميم');
    await expect(panel.getByRole('button', { name: 'Delegate', exact: true })).toBeEnabled();
    const remove = panel.getByRole('button', { name: 'Delete agent' });
    await remove.scrollIntoViewIfNeeded();
    await expect(remove).toBeInViewport();
    expect((await header.boundingBox())!.y).toBe(headerY);
    await expect(header.getByLabel('Close details')).toBeInViewport();
    await page.screenshot({ path: `${output}/scrolled-${width}.png`, animations: 'disabled' });
    await panel.getByLabel('Close details').press('Escape');
    await expect(trigger).toBeFocused();
    await trigger.click();
    await expect(panel.locator('#rb-contract')).not.toHaveValue('Updated but unsaved.\nتغييرات غير محفوظة.');
    expect(calls.filter(method => /conversations.create|profiles.update|profiles.delete|missions.delegate/.test(method))).toEqual([]);
  });
}
