import { expect, test, type Page } from '@playwright/test';

import { SKIN_IDS } from '../src/rusebot/lib/skins';

const teamNames = ['Development', 'Research', 'Design', 'Operations', 'Marketing', 'الدعم'];
const members = [
  [['Builder', 'Software Engineer'], ['Reviewer', 'Code & Quality Specialist'], ['DevOps', 'Infrastructure & Deployment']],
  [['Explorer', 'Research & Discovery'], ['Analyst', 'Data & Insights']],
  [['UXer', 'User Experience Design'], ['Visualizer', 'Interface Design'], ['Illustrator', 'Illustration'], ['Editor', 'Content Design'], ['ليلى', 'تصميم المنتج']],
];
const longModel = 'team-model-' + 'extended-context-'.repeat(9);
const stress = Array.from({ length: 18 }, (_, i) => [3, 1, 2, 1, 5, 1][i % 6]);

async function showChart(page: Page, sizes: number[], solos = 0, width = 1440, skin = 'meadow', withLongModel = false) {
  await page.setViewportSize({ width, height: 900 });
  await page.addInitScript(skin => {
    localStorage.setItem('agentos.rusebot.rail.v1', '1');
    localStorage.setItem('rusebot-skin', skin);
  }, skin);
  const profiles = [{ id: 'custom', name: 'Loqman', description: 'CEO', instructions: 'Coordinate.', role_type: 'ceo', settings_json: '{}' }];
  for (const [i, size] of sizes.entries()) {
    for (let j = 0; j < size; j++) {
      const [name, role] = members[i]?.[j] ?? [`Member ${i + 1}.${j + 1}`, 'Operations'];
      profiles.push({ id: `member-${i}-${j}`, name, description: role, instructions: 'Existing contract.', role_type: 'custom',
        settings_json: JSON.stringify({ team_name: teamNames[i] ?? `Team ${i + 1}` }) });
    }
  }
  for (let i = 0; i < solos; i++) {
    profiles.push({ ...profiles[0], id: `independent-${i}`, name: ['Monitor', 'Support'][i] ?? `Agent ${i}`,
      description: i === 0 ? 'System Health & Reliability' : 'User & Agent Assistance', role_type: 'custom',
      settings_json: JSON.stringify({ team_name: [undefined, 'agents', '', '   ', 'agents'][i % 5] }),
      ...(withLongModel && i === 0 ? { default_model_id: longModel, engine: 'layout-fixture' } : {}) });
  }
  if (width === 390) {
    const long = profiles.find(p => p.id === 'member-2-0')!;
    long.name = 'مصمم تجربة المستخدم '.repeat(8).trim();
    long.description = 'Accessibility and product research '.repeat(8).trim();
  }
  const creates: string[] = [];
  await page.route('**/__agentos/call', route => {
    const { method } = route.request().postDataJSON();
    if (method === 'profiles.list') return route.fulfill({ json: { result: { profiles } } });
    if (method === 'conversations.create') creates.push(method);
    return route.continue();
  });
  await page.goto('/?ui=rusebot');
  if (width < 768) await page.getByRole('button', { name: 'Open chat list' }).click();
  await page.locator('[data-sidebar-nav=team]').click();
  const teams = sizes.filter(size => size >= 2).length;
  await expect(page.locator('.org-summary span')).toHaveText(['1 CEO', `${teams} ${teams === 1 ? 'team' : 'teams'}`, `${profiles.length - 1} ${profiles.length === 2 ? 'agent' : 'agents'}`]);
  await expect(page.locator('[data-org-solo]')).toHaveCount(solos + sizes.length - teams);
  await expect(page.locator('[data-org-group]')).toHaveCount(teams);
  await expect(page.locator('.org-hub [data-agent-avatar]')).toHaveCount(0);
  const hub = page.locator('.org-hub');
  await expect(hub.locator('.org-chief strong')).toHaveText('Loqman ' + String.fromCodePoint(0x2605));
  await expect(hub.getByText('CEO & Chief Orchestrator', { exact: true })).toBeVisible();
  await expect(hub.getByText('Orchestrator', { exact: true })).toHaveCount(0);
  await expect(hub.getByRole('button', { name: 'Open chat with Loqman' })).toBeVisible();
  return creates;
}

for (const scenario of [
  { name: 'CEO alone', sizes: [], solos: 0 }, { name: 'one independent', sizes: [], solos: 1 },
  { name: 'five independent', sizes: [], solos: 5 }, { name: 'unique team name', sizes: [1], solos: 0 },
  { name: 'two share a team', sizes: [2], solos: 0 }, { name: 'mixed chart', sizes: [3,2,5], solos: 2 },
  { name: 'six teams', sizes: [2,3,2,3,2,3], solos: 0 }, { name: 'large mixed chart', sizes: stress, solos: 2 },
]) {
  test(scenario.name, async ({ page }, info) => {
    await showChart(page, scenario.sizes, scenario.solos, 1440, scenario.sizes === stress ? 'midnight' : 'meadow');
    const cards = page.locator('.org-stage > :is(.org-group,.org-solo)');
    await expect(page.locator('.org-lines path')).toHaveCount(await cards.count());
    if (scenario.sizes === stress || scenario.name === 'six teams') {
      const zoomIn = page.getByRole('button', { name: 'Zoom in', exact: true });
      for (let i = 0; i < 4; i++) await zoomIn.click();
      await expect(page.getByLabel('Zoom level')).toHaveText('200%');
    }
    const geometry = await page.locator('.org-stage').evaluate(el => {
      const bounds = el.getBoundingClientRect();
      const hub = el.querySelector('.org-hub')!.getBoundingClientRect();
      const boxes = [...el.querySelectorAll(':scope > :is(.org-group,.org-solo)')].map(card => card.getBoundingClientRect());
      function onEdge(p: DOMPoint, box: DOMRect) {
        return p.x >= box.left - 1 && p.x <= box.right + 1 && p.y >= box.top - 1 && p.y <= box.bottom + 1 &&
          Math.min(Math.abs(p.x - box.left), Math.abs(p.x - box.right), Math.abs(p.y - box.top), Math.abs(p.y - box.bottom)) < 1;
      }
      const all = [hub, ...boxes];
      return {
        centered: Math.abs(hub.left + hub.width / 2 - bounds.left - bounds.width / 2) < 1 && Math.abs(hub.top + hub.height / 2 - bounds.top - bounds.height / 2) < 1,
        overlap: all.some((a, i) => all.slice(i + 1).some(b => a.left < b.right && b.left < a.right && a.top < b.bottom && b.top < a.bottom)),
        inside: all.every(a => a.left >= bounds.left && a.right <= bounds.right && a.top >= bounds.top && a.bottom <= bounds.bottom),
        attached: [...el.querySelectorAll<SVGPathElement>('.org-lines path')].every((path, i) => {
          const matrix = path.getScreenCTM()!;
          return onEdge(path.getPointAtLength(0).matrixTransform(matrix), hub) && onEdge(path.getPointAtLength(path.getTotalLength()).matrixTransform(matrix), boxes[i]);
        }),
      };
    });
    expect(geometry).toEqual({ centered: true, overlap: false, inside: true, attached: true });
    for (const [i, size] of scenario.sizes.entries()) {
      if (size === 1) {
        await expect(page.locator(`[data-org-solo="member-${i}-0"]`)).toHaveCount(1);
      } else {
        const team = page.getByRole('region', { name: teamNames[i] ?? `Team ${i + 1}`, exact: true });
        await expect(team.locator('[data-bot-id]')).toHaveCount(Math.min(3, size));
        await expect(team.getByRole('button', { name: `View all ${size} agents` })).toHaveCount(1);
        if (size > 3) await expect(team.getByText(`+${size - 3} more`, { exact: true })).toBeVisible();
      }
    }
    await expect(page.locator('.org-hub')).toBeInViewport();
    await page.screenshot({ path: info.outputPath(scenario.name.replaceAll(' ', '-') + '.png') });
    if (scenario.name === 'six teams') {
      await page.getByRole('button', { name: 'Fit to view' }).click();
      await expect(page.getByRole('button', { name: 'Zoom in', exact: true })).toBeEnabled();
      for (const card of await cards.all()) await expect(card).toBeInViewport({ ratio: 0.99 });
    }
    if (scenario.sizes === stress) {
      for (const card of await cards.all()) {
        await card.scrollIntoViewIfNeeded();
        await expect(card).toBeInViewport();
      }
    }
  });
}

test('team roster, search, focus and agent details', async ({ page }, info) => {
  const creates = await showChart(page, [3,2,5], 2);
  await page.getByRole('button', { name: 'Zoom in', exact: true }).click();
  const development = page.getByRole('region', { name: 'Development', exact: true });
  await development.getByRole('button', { name: 'View all 3 agents' }).click();
  const roster = page.getByRole('dialog');
  await expect(roster.locator('[data-agent-card]')).toHaveCount(3);
  await expect(roster.getByRole('textbox')).toBeFocused();
  expect(await roster.getByRole('textbox').evaluate(el => el.dispatchEvent(
    new WheelEvent('wheel', { ctrlKey: true, deltaY: -20, bubbles: true, cancelable: true }),
  ))).toBe(true);
  await page.screenshot({ path: info.outputPath('roster.png') });
  await page.keyboard.press('Escape');
  await expect(development.getByRole('button', { name: 'View all 3 agents' })).toBeFocused();
  await expect(page.getByLabel('Zoom level')).toHaveText('120%');
  const more = page.getByRole('button', { name: 'View all 5 agents' });
  await more.press('Enter');
  const search = roster.getByRole('textbox', { name: 'Search team members' });
  await expect(roster.locator('[data-agent-card]')).toHaveCount(5);
  await search.press('Shift+Tab');
  await expect(roster.getByLabel('Close team roster')).toBeFocused();
  for (const query of ['UXER', 'Interface Design', 'تصميم', 'ليلى']) {
    await search.fill(query);
    await expect(roster.locator('[data-agent-card]')).toHaveCount(1);
  }
  await search.fill('absent');
  await expect(roster.getByRole('status')).toContainText('No matching agents');
  await search.fill('ليلى');
  await roster.getByRole('button', { name: 'Open ليلى', exact: true }).click();
  await expect(roster).toHaveCount(0);
  const details = page.getByRole('complementary', { name: 'ليلى details' });
  await expect(details.getByLabel('Close details')).toBeFocused();
  await expect(details.locator('#rb-contract')).toHaveValue('Existing contract.');
  expect(await details.locator('#rb-contract').evaluate(el => el.dispatchEvent(
    new WheelEvent('wheel', { ctrlKey: true, deltaY: -20, bubbles: true, cancelable: true }),
  ))).toBe(true);
  await details.getByLabel('Close details').click();
  await expect(more).toBeFocused();
  await expect(page.getByLabel('Zoom level')).toHaveText('120%');
  expect(creates).toEqual([]);
});

test('390px mobile chart and roster', async ({ page }, info) => {
  await showChart(page, [3,2,5], 2, 390, 'midnight');
  await expect(page.locator('.org-stack > .org-solo')).toHaveCount(2);
  await expect(page.getByRole('group', { name: 'Organization zoom' })).toHaveCount(0);
  const canceled = await page.locator('.org-frame').evaluate(el => !el.dispatchEvent(new WheelEvent('wheel', { ctrlKey: true, deltaY: -30, bubbles: true, cancelable: true })));
  expect(canceled).toBe(false);
  expect(await page.getByRole('region', { name: 'Team canvas' }).evaluate(el => el.scrollWidth <= el.clientWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath('mobile.png') });
  await page.getByRole('button', { name: 'View all 5 agents' }).click();
  const panel = page.getByRole('dialog');
  await expect(panel).toBeInViewport();
  await panel.getByRole('textbox').fill('مصمم');
  await expect(panel.locator('[data-agent-card]')).toHaveCount(1);
  await expect(panel.locator('.org-label strong')).toHaveText('مصمم تجربة المستخدم '.repeat(8).trim());
  expect(await panel.evaluate(el => el.scrollWidth <= el.clientWidth)).toBe(true);
  await panel.getByLabel('Close team roster').click();
  await expect(panel).toHaveCount(0);
});

test('theme change from Settings updates the mounted chart', async ({ page }, info) => {
  await showChart(page, [3,2,5], 2);
  await page.getByRole('button', { name: 'Open Monitor', exact: true }).click();
  const panel = page.getByRole('complementary', { name: 'Monitor details' });
  const hub = page.locator('.org-hub');
  const original = await hub.evaluate(el => getComputedStyle(el).backgroundColor);
  await page.getByRole('button', { name: 'Settings', exact: true }).click();
  const dialog = page.getByRole('dialog');
  await dialog.locator('[data-settings-page=appearance]').click();
  const lineColors = new Set<string>();
  const toolbarColors = new Set<string>();
  const panelColors = new Set<string>();
  await dialog.getByRole('button', { name: /View all/ }).click();
  for (const skin of [...SKIN_IDS.filter(id => id !== 'atelier'), 'atelier']) {
    await dialog.locator(`[data-theme-card="${skin}"]`).click();
    await expect(page.locator('html')).toHaveAttribute('data-skin', skin);
    lineColors.add(await page.locator('.org-lines path').first().evaluate(el => getComputedStyle(el).stroke));
    panelColors.add(await panel.evaluate(el => getComputedStyle(el).backgroundColor));
    toolbarColors.add(await page.locator('.org-zoom-toolbar').evaluate(el => getComputedStyle(el).backgroundColor));
  }
  expect(lineColors.size).toBeGreaterThan(6);
  expect(toolbarColors.size).toBeGreaterThan(6);
  expect(panelColors.size).toBeGreaterThan(6);
  await dialog.getByLabel('Close settings').click();
  await expect(page.locator('html')).toHaveAttribute('data-skin', 'atelier');
  expect(await hub.evaluate(el => getComputedStyle(el).backgroundColor)).not.toBe(original);
  await page.screenshot({ path: info.outputPath('atelier.png') });
});

test('details and roster preserve scrolled canvas across sidebar changes', async ({ page }) => {
  await showChart(page, stress, 2, 1100);
  await page.getByRole('button', { name: 'Zoom in', exact: true }).click();
  await page.getByRole('button', { name: 'Expand sidebar' }).click();
  await expect.poll(() => page.locator('[data-sidebar]').evaluate(el => el.getBoundingClientRect().width)).toBe(288);
  await expect(page.locator('.org-stack')).toBeVisible();
  const canvas = page.getByRole('region', { name: 'Team canvas' });
  const target = canvas.locator('[data-org-group]').last();
  const more = target.getByRole('button', { name: /^View all/ });
  await more.scrollIntoViewIfNeeded();
  const top = await canvas.evaluate(el => el.scrollTop);
  await more.click();
  await page.keyboard.press('Escape');
  await expect(more).toBeFocused();
  expect(await canvas.evaluate(el => el.scrollTop)).toBe(top);
  await more.click();
  await page.getByRole('dialog').locator('[data-bot-id]').first().click();
  await expect(page.locator('.org-lines')).toBeVisible();
  await page.getByRole('button', { name: 'Close details' }).click();
  await expect(page.locator('.org-stack')).toBeVisible();
  await expect.poll(() => canvas.evaluate(el => el.scrollTop)).toBe(top);
  await expect(more).toBeFocused();
  await page.getByRole('button', { name: 'Collapse sidebar' }).click();
  await expect(page.getByLabel('Zoom level')).toHaveText('120%');
});

test('zoom controls clamp, fit and recenter after a viewport resize', async ({ page }) => {
  await showChart(page, stress, 3);
  const zoomIn = page.getByRole('button', { name: 'Zoom in', exact: true });
  const zoomOut = page.getByRole('button', { name: 'Zoom out', exact: true });
  const level = page.getByLabel('Zoom level');
  await expect(level).toHaveText('100%');
  await zoomIn.click();
  await expect(level).toHaveText('120%');
  await zoomOut.click();
  await expect(level).toHaveText('100%');
  for (let i = 0; i < 4; i++) await zoomOut.click();
  await expect(level).toHaveText('50%');
  await expect(zoomOut).toBeDisabled();
  await page.setViewportSize({ width: 1250, height: 790 });
  await page.getByRole('button', { name: 'Fit to view' }).click();
  await expect(level).toHaveText('50%');
  const canvas = page.getByRole('region', { name: 'Team canvas' });
  const bounds = (await canvas.boundingBox())!;
  const hub = (await page.locator('.org-hub').boundingBox())!;
  expect(Math.abs(hub.y + hub.height / 2 - (bounds.y + bounds.height / 2))).toBeLessThan(10);
  for (const card of await page.locator('.org-stage > :is(.org-group,.org-solo)').all()) {
    await card.scrollIntoViewIfNeeded();
    await expect(card).toBeInViewport();
  }
});

test('wheel scrolls normally and Ctrl+wheel keeps the chart point under the pointer', async ({ page }) => {
  await showChart(page, stress, 3);
  const canvas = page.getByRole('region', { name: 'Team canvas' });
  const bounds = (await canvas.boundingBox())!;
  const x = Math.floor(bounds.x + bounds.width / 4);
  const y = Math.floor(bounds.y + bounds.height / 3);
  await canvas.evaluate(el => { el.scrollTop = 300; });
  await page.mouse.move(x, y);
  await page.mouse.wheel(0, 95);
  await expect.poll(() => canvas.evaluate(el => el.scrollTop)).toBeGreaterThan(300);
  const level = page.getByLabel('Zoom level');
  await expect(level).toHaveText('100%');
  const stage = page.locator('.org-stage');
  const before = (await stage.boundingBox())!;
  const pixelRatio = await page.evaluate(() => window.devicePixelRatio);
  await page.keyboard.down('Control');
  await page.mouse.wheel(0, -20);
  await page.keyboard.up('Control');
  await expect(level).toHaveText('122%');
  const after = (await stage.boundingBox())!;
  const ratio = after.width / before.width;
  expect(Math.abs(after.x + (x - before.x) * ratio - x)).toBeLessThan(2);
  expect(Math.abs(after.y + (y - before.y) * ratio - y)).toBeLessThan(2);
  expect(await page.evaluate(() => window.devicePixelRatio)).toBe(pixelRatio);
  await page.keyboard.down('Control');
  await page.mouse.wheel(0, 20);
  await page.keyboard.up('Control');
  await expect(level).toHaveText('100%');
  const restored = (await stage.boundingBox())!;
  expect(Math.abs(before.x - restored.x)).toBeLessThan(2);
  expect(Math.abs(before.y - restored.y)).toBeLessThan(2);
});

test('Chromium touchpad pinch zooms the scene while native pinch remains available outside it', async ({ page, context }) => {
  await showChart(page, [2, 3, 2, 3, 2, 3]);
  const cdp = await context.newCDPSession(page);
  const canvas = (await page.getByRole('region', { name: 'Team canvas' }).boundingBox())!;
  const level = page.getByLabel('Zoom level');
  const viewportScale = () => page.evaluate(() => window.visualViewport!.scale);
  const startScale = await viewportScale();
  // DevTools routes a mouse-source pinch through Chromium's synthetic touchpad input path.
  const x = Math.floor(canvas.x + canvas.width / 2);
  await cdp.send('Input.synthesizePinchGesture', { x, y: Math.floor(canvas.y + canvas.height / 2), scaleFactor: 1.3, gestureSourceType: 'mouse' });
  await expect(level).toHaveText('130%');
  expect(await viewportScale()).toBe(startScale);
  await cdp.send('Input.synthesizePinchGesture', { x, y: Math.floor(canvas.y / 2), scaleFactor: 1.3, gestureSourceType: 'mouse' });
  await expect.poll(viewportScale).toBeGreaterThan(startScale);
  await expect(level).toHaveText('130%');
  await cdp.detach();
});


async function setChartScale(page: Page, scale: number) {
  await expect(page.getByLabel('Zoom level')).toHaveText('100%');
  if (scale === 1) return;
  await page.getByRole('region', { name: 'Team canvas' }).locator('.org-stage').evaluate((stage, scale) => {
    const box = stage.parentElement!.parentElement!.parentElement!.getBoundingClientRect();
    for (let i = 0; i < 4; i++) {
      stage.dispatchEvent(new WheelEvent('wheel', {
        ctrlKey: true, bubbles: true, cancelable: true, deltaY: -25 * Math.log(scale),
        clientX: box.x + box.width / 2, clientY: box.y + box.height / 2,
      }));
    }
  }, scale);
  await expect(page.getByLabel('Zoom level')).toHaveText(`${Math.round(scale * 100)}%`);
}

for (const [scale, width] of [[0.5, 1440], [0.75, 1280], [1, 1440], [1.25, 1024], [1.5, 1440], [2, 1280]]) {
  test(`docked AgentPanel preserves ${scale * 100}% zoom at ${width}px`, async ({ page }, info) => {
    const creates = await showChart(page, [3,2,5], 2, width);
    await setChartScale(page, scale);
    const level = page.getByLabel('Zoom level');
    const chart = page.getByRole('region', { name: 'Team canvas' });
    const trigger = chart.getByRole('button', { name: 'Open Monitor', exact: true });
    const full = (await chart.boundingBox())!;
    await trigger.scrollIntoViewIfNeeded();
    const previousScroll = await chart.evaluate(el => [el.scrollLeft, el.scrollTop]);
    if (scale === 1) await page.screenshot({ path: info.outputPath('chart-100.png') });
    await trigger.click();
    const panel = page.getByRole('complementary', { name: 'Monitor details' });
    const close = panel.getByRole('button', { name: 'Close details' });
    await expect(close).toBeFocused();
    await expect(level).toHaveText(`${scale * 100}%`);
    await expect.poll(async () => Math.round((await chart.boundingBox())!.width + 360)).toBe(Math.round(full.width));
    expect((await chart.boundingBox())!.x + (await chart.boundingBox())!.width).toBeLessThanOrEqual((await panel.boundingBox())!.x + 1);
    const geometry = await chart.locator('.org-stage').evaluate(stage => {
      const hub = stage.querySelector('.org-hub')!.getBoundingClientRect();
      const cards = [...stage.querySelectorAll(':scope > :is(.org-group,.org-solo)')].map(card => card.getBoundingClientRect());
      function onEdge(p: DOMPoint, box: DOMRect) {
        return p.x >= box.left - 1 && p.x <= box.right + 1 && p.y >= box.top - 1 && p.y <= box.bottom + 1 &&
          Math.min(Math.abs(p.x - box.left), Math.abs(p.x - box.right), Math.abs(p.y - box.top), Math.abs(p.y - box.bottom)) < 1;
      }
      const attached = [...stage.querySelectorAll<SVGPathElement>('.org-lines path')].every((path, i) => {
        const matrix = path.getScreenCTM()!;
        return onEdge(path.getPointAtLength(0).matrixTransform(matrix), hub)
          && onEdge(path.getPointAtLength(path.getTotalLength()).matrixTransform(matrix), cards[i]);
      });
      return { attached, scale: new DOMMatrix(getComputedStyle(stage).transform).a };
    });
    expect(geometry.attached).toBe(true);
    expect(geometry.scale).toBeCloseTo(scale, 4);
    if ([0.5, 1, 1.5].includes(scale)) await page.screenshot({ path: info.outputPath(`panel-${scale * 100}.png`) });
    if (scale === 2) {
      for (const card of await chart.locator('.org-stage > :is(.org-group,.org-solo)').all()) {
        await card.scrollIntoViewIfNeeded();
        await expect(card).toBeInViewport();
      }
    }
    await close.press('Escape');
    await expect(trigger).toBeFocused();
    await expect(level).toHaveText(`${scale * 100}%`);
    await expect.poll(async () => {
      const [left, top] = await chart.evaluate(el => [el.scrollLeft, el.scrollTop]);
      return Math.max(Math.abs(left - previousScroll[0]), Math.abs(top - previousScroll[1]));
    }).toBeLessThanOrEqual(1);
    expect(creates).toEqual([]);
  });
}

test('Fit to View uses the remaining canvas and survives browser resizing', async ({ page }) => {
  await showChart(page, [3,2,5], 2);
  await setChartScale(page, 2);
  await page.getByRole('button', { name: 'Open Monitor', exact: true }).click();
  const level = page.getByLabel('Zoom level');
  await expect(level).toHaveText('200%');
  const chart = page.getByRole('region', { name: 'Team canvas' });
  const before = (await chart.boundingBox())!.width;
  const readCenter = () => chart.evaluate(el => {
    const viewport = el.getBoundingClientRect();
    const stage = el.querySelector('.org-stage')!.getBoundingClientRect();
    return [(viewport.left + el.clientWidth / 2 - stage.left) / stage.width,
      (viewport.top + el.clientHeight / 2 - stage.top) / stage.height];
  });
  const center = await readCenter();
  await page.getByRole('button', { name: 'Expand sidebar' }).click();
  await expect.poll(() => page.locator('[data-sidebar]').evaluate(el => el.getBoundingClientRect().width)).toBe(288);
  await expect(level).toHaveText('200%');
  await expect.poll(async () => {
    const after = await readCenter();
    return Math.max(Math.abs(after[0] - center[0]), Math.abs(after[1] - center[1]));
  }).toBeLessThan(0.01);
  await page.setViewportSize({ width: 1280, height: 820 });
  await expect.poll(async () => (await chart.boundingBox())!.width).toBeLessThan(before - 200);
  await expect(level).toHaveText('200%');
  await page.getByRole('button', { name: 'Fit to view' }).click();
  await expect(level).not.toHaveText('200%');
  const fitted = await level.textContent();
  await expect(page.locator('.org-hub')).toBeInViewport();
  await page.getByRole('button', { name: 'Close details' }).click();
  await expect(level).toHaveText(fitted!);
  await page.getByRole('button', { name: 'Fit to view' }).click();
  await expect(page.locator('.org-hub')).toBeInViewport();
});

for (const width of [768, 390]) {
  test(`AgentPanel at ${width}px keeps header visible and actions reachable`, async ({ page }, info) => {
    await showChart(page, [3,2,5], 2, width, width === 768 ? 'midnight' : 'meadow', true);
    await page.setViewportSize({ width, height: 610 });
    await page.getByRole('button', { name: 'Open Monitor', exact: true }).click();
    const panel = page.getByRole('complementary', { name: 'Monitor details' });
    const header = panel.locator('.agent-details-header');
    const before = (await header.boundingBox())!.y;
    await expect(panel.getByText(longModel, { exact: true })).toBeVisible();
    await expect(panel.getByRole('button', { name: 'Save contract' })).toBeDisabled();
    await expect(panel.getByRole('button', { name: 'Delegate', exact: true })).toBeDisabled();
    expect(await panel.evaluate(el => el.scrollWidth <= el.clientWidth)).toBe(true);
    await page.screenshot({ path: info.outputPath(width === 768 ? 'dark-panel.png' : 'narrow-panel.png') });
    const remove = panel.getByRole('button', { name: 'Delete agent' });
    await remove.scrollIntoViewIfNeeded();
    await expect(remove).toBeInViewport();
    expect((await header.boundingBox())!.y).toBe(before);
    if (width === 768) await page.screenshot({ path: info.outputPath('scrolled-panel.png') });
    await panel.locator('#rb-contract').fill('Unsaved draft');
    await panel.getByRole('button', { name: 'Close details' }).click();
    await page.getByRole('button', { name: 'Open Monitor', exact: true }).click();
    await expect(panel.locator('#rb-contract')).not.toHaveValue('Unsaved draft');
  });
}

const toolbarPositionKey = 'loqman.team.zoom-toolbar-position.v1';
const zoomToolbar = (page: Page) => page.getByRole('group', { name: 'Organization zoom' });
const zoomGrip = (page: Page) => page.getByRole('button', { name: 'Reset zoom toolbar position' });

async function toolbarBounds(page: Page) {
  return zoomToolbar(page).evaluate(el => {
    const canvas = el.parentElement!.previousElementSibling!;
    const viewport = canvas.getBoundingClientRect();
    const toolbar = el.getBoundingClientRect();
    return {
      left: toolbar.left - viewport.left, top: toolbar.top - viewport.top,
      right: viewport.left + canvas.clientWidth - toolbar.right,
      bottom: viewport.top + canvas.clientHeight - toolbar.bottom,
    };
  });
}

async function dragToolbar(page: Page, x: number, y: number) {
  const grip = (await zoomGrip(page).boundingBox())!;
  await page.mouse.move(grip.x + grip.width / 2, grip.y + grip.height / 2);
  await page.mouse.down();
  await page.mouse.move(x, y, { steps: 12 });
  await page.mouse.up();
}

test('floating zoom toolbar drags independently of the chart and restores its position after reload', async ({ page }, info) => {
  await showChart(page, [3, 2, 5], 2, 1440, 'midnight');
  const canvas = page.getByRole('region', { name: 'Team canvas' });
  const level = page.getByLabel('Zoom level');
  const toolbar = zoomToolbar(page);
  await expect(toolbar).toHaveCSS('border-top-left-radius', '999px');
  await expect.poll(() => toolbarBounds(page)).toMatchObject({ right: 8, bottom: 8 });
  await page.screenshot({ path: info.outputPath('midnight-default.png'), animations: 'disabled' });
  await toolbar.getByRole('button', { name: 'Zoom in' }).click();
  await expect(level).toHaveText('120%');
  const chartSnapshot = () => canvas.evaluate(el => ({
    left: el.scrollLeft, top: el.scrollTop,
    transform: el.querySelector<HTMLElement>('.org-stage')!.style.transform,
    nodes: [...el.querySelectorAll('.org-stage > article, .org-stage > section')].map(node => node.getAttribute('style')),
    connectors: [...el.querySelectorAll('.org-lines path')].map(path => path.getAttribute('d')),
  }));
  const before = await chartSnapshot();
  await dragToolbar(page, 0, 0);
  await expect.poll(() => toolbarBounds(page)).toMatchObject({ left: 8, top: 8 });
  expect(await chartSnapshot()).toEqual(before);
  await expect(level).toHaveText('120%');
  await page.screenshot({ path: info.outputPath('midnight-dragged.png'), animations: 'disabled' });
  const displaced = await toolbarBounds(page);
  await toolbar.getByRole('button', { name: 'Zoom out' }).click();
  await expect(level).toHaveText('100%');
  await canvas.locator('.org-stage').evaluate(el => el.dispatchEvent(new WheelEvent('wheel', { ctrlKey: true, deltaY: 1, bubbles: true, cancelable: true })));
  await expect(level).toHaveText('99%');
  await toolbar.getByRole('button', { name: 'Fit to view' }).click();
  await expect(level).not.toHaveText('99%');
  expect(await toolbarBounds(page)).toEqual(displaced);
  await page.reload();
  await page.locator('[data-sidebar-nav=team]').click();
  await expect.poll(() => toolbarBounds(page)).toMatchObject({ left: 8, top: 8 });
  await expect(level).toHaveText('100%');
  await zoomGrip(page).click();
  await expect.poll(() => toolbarBounds(page)).toMatchObject({ right: 8, bottom: 8 });
});

test('toolbar keyboard movement and relative positioning survive panel, sidebar, and window resizing', async ({ page }, info) => {
  await showChart(page, [3, 2, 5], 2, 1440, 'atelier');
  const grip = zoomGrip(page);
  await expect.poll(() => toolbarBounds(page)).toMatchObject({ right: 8, bottom: 8 });
  await grip.focus();
  await grip.press('Shift+ArrowLeft');
  await grip.press('Shift+ArrowUp');
  await expect(grip).toBeFocused();
  await expect.poll(() => toolbarBounds(page)).toMatchObject({ right: 72, bottom: 72 });
  await page.screenshot({ path: info.outputPath('atelier-position.png'), animations: 'disabled' });
  const saved = await page.evaluate(key => localStorage.getItem(key), toolbarPositionKey);
  expect(saved).not.toBeNull();
  await zoomToolbar(page).getByRole('button', { name: 'Zoom in' }).click();
  await page.getByRole('button', { name: 'Open Monitor', exact: true }).click();
  const panel = page.getByRole('complementary', { name: 'Monitor details' });
  const assertInside = async () => {
    await expect.poll(async () => Math.min(...Object.values(await toolbarBounds(page)))).toBeGreaterThanOrEqual(0);
  };
  await assertInside();
  expect((await zoomToolbar(page).boundingBox())!.x + (await zoomToolbar(page).boundingBox())!.width).toBeLessThan((await panel.boundingBox())!.x);
  await page.screenshot({ path: info.outputPath('atelier-agent-panel.png'), animations: 'disabled' });
  await page.getByRole('button', { name: 'Expand sidebar' }).click();
  await expect.poll(() => page.locator('[data-sidebar]').evaluate(el => el.getBoundingClientRect().width)).toBe(288);
  await assertInside();
  await panel.getByRole('button', { name: 'Close details' }).click();
  await page.getByRole('button', { name: 'Collapse sidebar' }).click();
  await expect.poll(() => toolbarBounds(page)).toMatchObject({ right: 72, bottom: 72 });
  await page.setViewportSize({ width: 950, height: 635 });
  await assertInside();
  await expect(page.getByLabel('Zoom level')).toHaveText('120%');
  await page.screenshot({ path: info.outputPath('atelier-narrow.png'), animations: 'disabled' });
  await page.setViewportSize({ width: 1440, height: 900 });
  await expect.poll(() => toolbarBounds(page)).toMatchObject({ right: 72, bottom: 72 });
  expect(await page.evaluate(key => localStorage.getItem(key), toolbarPositionKey)).toBe(saved);
  await grip.press('Home');
  await expect(grip).toBeFocused();
  await expect.poll(() => toolbarBounds(page)).toMatchObject({ right: 8, bottom: 8 });
});

test('touch pointer captures the grip and invalid saved positions fall back to bottom right', async ({ page, context }) => {
  await page.addInitScript(key => localStorage.setItem(key, '{"x":400,"y":"obsolete"}'), toolbarPositionKey);
  await showChart(page, [3, 2, 5], 2, 1024);
  await expect.poll(() => toolbarBounds(page)).toMatchObject({ right: 8, bottom: 8 });
  const grip = (await zoomGrip(page).boundingBox())!;
  const canvas = (await page.getByRole('region', { name: 'Team canvas' }).boundingBox())!;
  const cdp = await context.newCDPSession(page);
  const x = grip.x + grip.width / 2;
  const y = grip.y + grip.height / 2;
  await cdp.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x, y }] });
  await cdp.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: [{ x: canvas.x + 1, y: canvas.y + 1 }] });
  await cdp.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
  await expect.poll(() => toolbarBounds(page)).toMatchObject({ left: 8, top: 8 });
  await expect(page.getByLabel('Zoom level')).toHaveText('100%');
  expect(await page.evaluate(key => JSON.parse(localStorage.getItem(key)!), toolbarPositionKey)).toEqual({ x: 0, y: 0 });
  const movedGrip = (await zoomGrip(page).boundingBox())!;
  await cdp.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x: movedGrip.x + 10, y: movedGrip.y + 10 }] });
  await cdp.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: [{ x, y }] });
  await cdp.send('Input.dispatchTouchEvent', { type: 'touchCancel', touchPoints: [] });
  await expect.poll(() => toolbarBounds(page)).toMatchObject({ left: 8, top: 8 });
  await cdp.detach();
});
