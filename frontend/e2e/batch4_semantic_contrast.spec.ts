import { expect, test, type Locator, type Page } from '@playwright/test';
import { writeFile } from 'node:fs/promises';
import { SKINS } from '../src/rusebot/lib/skins';

const stamp = '2026-11-24T12:00:00Z';
const profiles = [
  { id: 'custom', name: 'Loqman', role_type: 'ceo', description: 'CEO', instructions: 'Coordinate.' },
  { id: 'reviewer', name: 'Demo researcher', role_type: 'custom', description: 'Research specialist', instructions: 'Review the evidence.' },
];
const run = {
  id: 'review-run', conversation_id: 'review-chat', engine: 'mock', model_id: 'mock-review', status: 'running',
  user_message_seq: null, assistant_message_seq: null, assistant_content: null, error: null,
  tool_mode: 'none', created_at: stamp, started_at: stamp, finished_at: null,
};
const dialog = (page: Page) => page.getByRole('dialog');
const nav = (page: Page, name: string) => page.getByRole('button', { name, exact: true }).click();
const action = (scope: Locator, name: string) => scope.getByRole('button', { name, exact: true });
const settingsTab = (page: Page, tab: string) => dialog(page).locator('[data-settings-page="' + tab + '"]').click();

async function setup(page: Page, skin = 'meadow') {
  const approvals = ['Review evidence summary', 'Reject unsupported data'].map((action_summary, i) => ({
    id: 'decision-' + i, run_id: run.id, status: 'pending', scope: 'workspace_write', action_summary, expires_at: null,
  }));
  const drafts = ['pending_review', 'approved', 'rejected'].map(status => ({
    id: status, run_id: run.id, filename: status + '-deliverable.md', status, mime: 'text/markdown',
    content: null, downloadable: status === 'approved', content_sha256: 'fixture', size_bytes: 40,
  }));
  let release!: () => void;
  const holdDecision = new Promise<void>(resolve => { release = resolve; });
  const decisions: string[] = [];
  let saveAttempts = 0;
  await page.addInitScript(({ skin, id }) => {
    localStorage.setItem('rusebot-skin', skin);
    localStorage.setItem('agentos.rusebot.selected.v1', JSON.stringify(id));
  }, { skin, id: run.conversation_id });
  const replies: Record<string, unknown> = {
    'profiles.list': { profiles },
    'conversations.list': { conversations: [{ id: run.conversation_id, title: 'Evidence review', profile_id: 'reviewer', workspace_id: null, created_at: stamp, updated_at: stamp }] },
    'conversations.messages': { messages: [] },
    'runs.list': { runs: [run] },
    'runs.get': run,
    'runs.events': { events: [] },
    'deliverables.list': { drafts: [] },
    'deliverables.list_all': { drafts },
    'ceo.actions': { actions: [] },
    'missions.list': { missions: ['running', 'queued', 'succeeded', 'failed'].map(status => ({ id: status, status, title: status + ' example', tasks: [] })) },
    'engines.capabilities': { mock: { conversation_runs: true }, codex: { conversation_runs: true }, 'claude-code': { conversation_runs: true }, opencode: { conversation_runs: true } },
    'engines.status': { engines: [
      { engine_id: 'codex', title: 'Connected provider', install_state: 'installed', auth_state: 'authenticated' },
      { engine_id: 'claude-code', title: 'Provider error', install_state: 'unknown', auth_state: 'unknown' },
      { engine_id: 'opencode', title: 'Sign-in provider', install_state: 'installed', auth_state: 'unauthenticated' },
    ] },
    'preferences.get': { engine: 'mock', model_id: run.model_id },
    'models.free': { models: [{ id: run.model_id, name: 'Mock reviewer' }] },
    'usage.summary': { by_engine: [] },
  };
  await page.route('**/__agentos/stream?*', route => route.fulfill({
    contentType: 'text/event-stream', body: 'retry: 60000\n\n',
  }));
  await page.route('**/__agentos/call', async route => {
    const { method, params } = route.request().postDataJSON() as { method: string; params: Record<string, unknown> };
    const reply = (result: unknown) => route.fulfill({ json: { result } });
    if (method === 'approvals.list') return reply({ approvals });
    if (method === 'approvals.decide') {
      const id = String(params.approval_id);
      decisions.push(id);
      if (id === 'decision-0') await holdDecision;
      const index = approvals.findIndex(item => item.id === id);
      const approval = approvals.splice(index, 1)[0];
      return reply({ ...approval, status: params.decision === 'approve' ? 'approved' : 'denied' });
    }
    if (method === 'profiles.update') {
      saveAttempts++;
      return saveAttempts === 1 ? reply({ ...profiles[1], instructions: params.instructions }) :
        route.fulfill({ status: 503, json: { detail: 'Fixture could not save changes.' } });
    }
    if (method in replies) return reply(replies[method]);
    return route.fulfill({ status: 422, json: { detail: 'Unused RPC blocked by semantic color fixture.' } });
  });
  await page.goto('/');
  return { release, decisions };
}

type Reading = { foreground: string; background: string; ratio: number };

// Original sRGB action accents and warning backgrounds at the committed visual reference.
const ORIGINAL_COLORS = {
  midnight: ['#1084fe', '#ff9800'],
  atelier: ['#a05f25', '#7a5f31'],
  foundry: ['#d99a3e', '#dfa441'],
  lagoon: ['#11736d', '#7a5a2a'],
  graphite: ['#4f75a2', '#d3a150'],
  linen: ['#355f8a', '#765b2b'],
  dusk: ['#765683', '#d0a35d'],
  daylight: ['#0b62c4', '#8a5a00'],
  meadow: ['#1a7a48', '#8a5a00'],
  pop: ['#7f52f0', '#8a5a00'],
  halloween: ['#ff7f1f', '#f2b45a'],
} as const;

async function expectColorToken(locator: Locator, property: 'color' | 'backgroundColor', token: string) {
  const pairs = await locator.evaluate((el, { property, token }) => {
    const style = getComputedStyle(el);
    const context = document.createElement('canvas').getContext('2d')!;
    context.fillStyle = style[property];
    const actual = context.fillStyle;
    context.fillStyle = style.getPropertyValue(token).trim();
    return { actual, expected: context.fillStyle };
  }, { property, token });
  expect.soft(pairs.actual, property + ' follows ' + token).toBe(pairs.expected);
}

/** Measure the computed foreground and backdrop, compositing ancestor surfaces and group opacity. */
function readContrast(locator: Locator, mode: 'text' | 'fill' | 'border' = 'text'): Promise<Reading> {
  return locator.evaluate((element, mode) => {
    type Rgba = [number, number, number, number];
    const canvas = document.createElement('canvas');
    canvas.width = canvas.height = 1;
    const context = canvas.getContext('2d', { willReadFrequently: true })!;
    const read = (css: string): Rgba => {
      context.clearRect(0, 0, 1, 1);
      context.fillStyle = css;
      context.fillRect(0, 0, 1, 1);
      const [r, g, b, a] = context.getImageData(0, 0, 1, 1).data;
      return [r, g, b, a / 255];
    };
    const over = (front: Rgba, back: Rgba): Rgba => {
      const alpha = front[3] + back[3] * (1 - front[3]);
      if (!alpha) return [0, 0, 0, 0];
      const channel = (i: number) => (front[i] * front[3] + back[i] * back[3] * (1 - front[3])) / alpha;
      return [channel(0), channel(1), channel(2), alpha];
    };
    const style = getComputedStyle(element);
    let fg = read(mode === 'text' ? style.color : mode === 'fill' ? style.backgroundColor : style.borderTopColor);
    let bg: Rgba = [0, 0, 0, 0];
    for (let node: Element | null = element; node; node = node.parentElement) {
      const current = getComputedStyle(node);
      if (node !== element || mode === 'text') {
        const fill = read(current.backgroundColor);
        fg = over(fg, fill);
        bg = over(bg, fill);
      }
      const brightness = Number(current.filter.match(/^brightness\(([\d.]+)\)$/)?.[1] ?? 1);
      const adjust = (pixel: Rgba): Rgba => [
        Math.min(255, pixel[0] * brightness), Math.min(255, pixel[1] * brightness),
        Math.min(255, pixel[2] * brightness), pixel[3] * Number(current.opacity),
      ];
      fg = adjust(fg);
      bg = adjust(bg);
    }
    fg = over(fg, [255, 255, 255, 1]);
    bg = over(bg, [255, 255, 255, 1]);
    const linear = (channel: number) => channel / 255 <= 0.04045 ? channel / 255 / 12.92 : ((channel / 255 + 0.055) / 1.055) ** 2.4;
    const luminance = (color: Rgba) => 0.2126 * linear(color[0]) + 0.7152 * linear(color[1]) + 0.0722 * linear(color[2]);
    const a = luminance(fg), b = luminance(bg);
    const hex = (color: Rgba) => '#' + color.slice(0, 3).map(c => Math.round(c).toString(16).padStart(2, '0')).join('');
    return { foreground: hex(fg), background: hex(bg), ratio: (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05) };
  }, mode);
}

for (const [index, skin] of SKINS.entries()) {
  test(skin.name + ' rendered semantic colors meet contrast targets', async ({ page }, info) => {
    const [width, height] = [[1440, 900], [1280, 820], [1100, 700]][index % 3];
    await page.setViewportSize({ width, height });
    await page.emulateMedia({ reducedMotion: 'reduce' });
    const { release, decisions } = await setup(page, skin.id);
    const midnight = skin.id === 'midnight';
    const readings: Array<Reading & { name: string; target: number }> = [];
    const approvalStates: Record<string, Reading> = {};
    const regions: Record<string, NonNullable<Awaited<ReturnType<Locator['boundingBox']>>>> = {};
    const region = async (name: string, locator: Locator) => {
      const box = await locator.boundingBox();
      if (!box) throw new Error('Screenshot target is hidden: ' + name);
      regions[name] = box;
    };
    const capture = async (name: string, locator: Locator, target = 4.5, mode: 'text' | 'fill' | 'border' = 'text') => {
      await expect(locator).toBeVisible();
      readings.push({ name, target, ...await readContrast(locator, mode) });
    };
    const screenshot = (name: string) => page.screenshot({ path: info.outputPath(name + '.png'), animations: 'disabled' });
    const checkApproval = async (button: Locator) => {
      if (midnight) {
        await expect(button).toHaveCSS('background-color', 'rgb(8, 117, 209)');
        await expect(button).toHaveCSS('color', 'rgb(255, 255, 255)');
      } else {
        await expectColorToken(button, 'backgroundColor', '--color-accent');
      }
    };
    const checkPressed = async (name: string, button: Locator) => {
      await button.hover();
      await page.mouse.down();
      try {
        await expect(button).toHaveCSS('background-color', 'rgb(8, 109, 195)');
        const pressed = await readContrast(button);
        expect(pressed.foreground).toBe('#ffffff');
        expect(pressed.ratio).toBeGreaterThanOrEqual(4.5);
        approvalStates[name] = pressed;
      } finally {
        await page.mouse.move(0, 0);
        await page.mouse.up();
      }
    };
    try {
      const card = (id: 'first' | 'second') => page.locator('[data-approval-card="tool"]').filter({
        hasText: id === 'first' ? 'Review evidence summary' : 'Reject unsupported data',
      });
      const badge = page.locator('[data-needs-you]').getByText('2', { exact: true });
      const approve = action(card('first'), 'Approve');
      const deny = action(card('first'), 'Deny');
      const running = page.locator('[data-working]');
      await capture('Needs You badge', badge);
      await capture('Approve', approve);
      await capture('Deny', deny);
      await capture('Waiting for input', card('first').getByText('Waiting for your answer', { exact: true }));
      await capture('Running', running);
      if (!await page.evaluate(() => matchMedia('(color-gamut: p3)').matches)) {
        const [accent, warning] = ORIGINAL_COLORS[skin.id];
        expect.soft(readings.find(reading => reading.name === 'Approve')?.background).toBe(midnight ? '#0875d1' : accent);
        expect.soft(readings.find(reading => reading.name === 'Needs You badge')?.background).toBe(warning);
      }
      await checkApproval(approve);
      await expectColorToken(badge, 'backgroundColor', '--color-warning');
      await expectColorToken(deny, 'backgroundColor', '--color-control');
      await expectColorToken(deny, 'color', '--color-ink');
      await expectColorToken(running, 'color', '--color-ink-secondary');
      await region('approve', approve);
      await region('deny', deny);
      await region('needs-you', badge);
      await region('running', running.getByText(/Working|Queued/));
      await screenshot('chat-approvals');
      const hover = async (name: string, button: Locator) => {
        await button.hover();
        if (midnight && (name === 'Approve' || name === 'Approve deliverable')) {
          await expect(button).toHaveCSS('background-color', 'rgb(8, 119, 212)');
          await expect(button).toHaveCSS('color', 'rgb(255, 255, 255)');
          await screenshot(name === 'Approve' ? 'chat-approve-hover' : 'results-approve-hover');
        }
        await capture(name + ' hover', button);
      };
      await hover('Approve', approve);
      await hover('Deny', action(card('first'), 'Deny'));
      if (midnight) await checkPressed('Chat Approve pressed', approve);
      await approve.click();
      await expect(action(card('first'), 'Approve')).toBeDisabled();
      await expect(action(card('first'), 'Deny')).toBeDisabled();
      if (midnight) {
        await expect(approve).toHaveCSS('background-color', 'rgb(61, 61, 61)');
        await expectColorToken(approve, 'backgroundColor', '--color-raised-hover');
        await expectColorToken(approve, 'color', '--color-ink-secondary');
        approvalStates['Chat Approve pending/disabled'] = await readContrast(approve);
      }
      await capture('Submitting decision', card('first').getByText('Submitting decision…', { exact: true }));
      await screenshot('pending-approval');
      release();
      await capture('Approved decision', card('first').getByText('Approved', { exact: true }));
      await action(card('second'), 'Deny').click();
      await capture('Denied decision', card('second').getByText('Denied', { exact: true }));
      expect(decisions).toEqual(['decision-0', 'decision-1']);
      await nav(page, 'Results');
      const draft = (id: string) => page.locator('[data-deliverable="' + id + '"]');
      await capture('Approved deliverable', draft('approved').getByText('Approved', { exact: true }));
      await capture('Rejected deliverable', draft('rejected').getByText('Rejected', { exact: true }));
      await capture('Pending deliverable', draft('pending_review').getByText('Awaiting review', { exact: true }));
      const approveDeliverable = action(draft('pending_review'), 'Approve');
      await checkApproval(approveDeliverable);
      await capture('Approve deliverable', approveDeliverable);
      await capture('Reject deliverable', action(draft('pending_review'), 'Reject'));
      await expectColorToken(action(draft('pending_review'), 'Reject'), 'backgroundColor', '--color-control');
      await expectColorToken(draft('approved').getByText('Approved', { exact: true }), 'color', '--color-success');
      await expectColorToken(draft('rejected').getByText('Rejected', { exact: true }), 'color', '--color-danger');
      await region('approved', draft('approved').getByText('Approved', { exact: true }));
      await region('rejected', draft('rejected').getByText('Rejected', { exact: true }));
      await region('approve-deliverable', approveDeliverable);
      await hover('Approve deliverable', approveDeliverable);
      if (midnight) await checkPressed('Results Approve pressed', approveDeliverable);
      await hover('Reject deliverable', action(draft('pending_review'), 'Reject'));
      await screenshot('results');

      await nav(page, 'Team');
      const mission = (id: string) => page.locator('[data-mission="' + id + '"]').getByText(id, { exact: true });
      for (const name of ['running', 'queued', 'succeeded', 'failed']) await capture('Mission ' + name, mission(name));
      await action(page.locator('main'), 'Open Demo researcher').click();
      const panel = page.locator('[data-agent-panel]');
      const remove = action(panel, 'Delete agent');
      await remove.scrollIntoViewIfNeeded();
      await capture('Delete agent', remove);
      await expectColorToken(remove, 'color', '--color-danger');
      await capture('Delete outline', remove, 3, 'border');
      await hover('Delete agent', remove);
      await region('delete-agent', remove);
      await screenshot('agent-details');
      const confirmation = page.waitForEvent('dialog');
      const click = remove.click();
      const prompt = await confirmation;
      expect(prompt.type()).toBe('confirm');
      expect(prompt.message()).toBe('Delete Demo researcher? This cannot be undone.');
      await prompt.dismiss();
      await click;
      await expect(panel).toBeVisible();
      const instructions = panel.getByRole('textbox', { name: 'Contract' });
      await instructions.fill('Review the evidence in detail.');
      await action(panel, 'Save contract').click();
      await capture('Success message', panel.getByText('Contract saved.', { exact: true }));
      await expectColorToken(panel.getByText('Contract saved.', { exact: true }), 'color', '--color-success');
      await screenshot('success-message');
      await instructions.fill('Another review update.');
      await action(panel, 'Save contract').click();
      await capture('Error message', panel.getByText('Fixture could not save changes.', { exact: true }));
      await screenshot('error-message');

      await nav(page, 'Settings');
      await settingsTab(page, 'ai');
      for (const [state, name] of [['ready', 'Connected'], ['error', 'Provider error'], ['needs_login', 'Provider warning']]) {
        const pill = dialog(page).locator('[data-engine-state="' + state + '"]');
        await capture(name, pill);
        await capture(name + ' dot', pill.locator('span'), 3, 'fill');
        if (state === 'ready' || state === 'error') {
          await expectColorToken(pill, 'color', state === 'ready' ? '--color-success' : '--color-danger');
          await region(state === 'ready' ? 'connected-provider' : 'failed-provider', pill);
        }
      }
      await screenshot('settings-ai');
      await writeFile(info.outputPath('contrast.json'), JSON.stringify({ theme: skin.id, width, height, readings, regions }, null, 2));
      if (midnight) await writeFile(info.outputPath('approval-states.json'), JSON.stringify(approvalStates, null, 2));
      for (const row of readings) expect.soft(row.ratio, skin.name + ' / ' + row.name + ' ' + row.foreground + ' on ' + row.background).toBeGreaterThanOrEqual(row.target);
    } finally {
      release();
    }
  });
}

test('semantic colors follow Appearance selection and persist after reload', async ({ page }) => {
  await page.goto('/');
  await nav(page, 'Settings');
  await settingsTab(page, 'appearance');
  const palette = () => page.locator('html').evaluate(el => getComputedStyle(el).getPropertyValue('--color-danger').trim());
  await dialog(page).getByRole('button', { name: 'Use Midnight theme' }).click();
  await expect(page.locator('html')).toHaveAttribute('data-skin', 'midnight');
  const midnight = await palette();
  await dialog(page).getByRole('button', { name: 'View all 11 themes' }).click();
  await dialog(page).getByRole('button', { name: 'Use Meadow theme' }).click();
  await expect(page.locator('html')).toHaveAttribute('data-skin', 'meadow');
  const meadow = await palette();
  expect(meadow).not.toBe(midnight);
  await page.reload();
  await expect(page.locator('html')).toHaveAttribute('data-skin', 'meadow');
  expect(await palette()).toBe(meadow);
});
