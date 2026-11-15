import { expect, test, type Page, type Route } from '@playwright/test';

/**
 * RuseBot-derived interface (src/rusebot). Runs against the isolated mock-mode
 * worker started by playwright.config.ts (AGENTOS_DATA_DIR=./.agentos-e2e-data).
 * Reference numbers come from RuseBot 0.1.127 rendered from its own dist in an
 * isolated fixture (temp data dir, fake engine) at 1440x900 and 700x900.
 */

const REFERENCE = {
  sidebarWidth: 288,
  composerHeight: 44,
  composerRadius: '24px',
  composerPadding: '6px 8px',
  bubbleRadius: '23px',
  bubblePadding: '10px 16px',
  bubbleFont: '15px',
  textareaFont: '15px',
  textareaLine: '24px',
  headerPillHeight: 36,
  columnWidth: 960,
  composerX: 384,
  hamburger: { x: 12, y: 12, w: 30, h: 30 },
};

const uid = () => Math.random().toString(36).slice(2, 8);

// Chats with an agent run on the engine (cancellable runs, approvals, deliverables). Loqman himself
// answers through the CEO loop, which has its own tests below.
let agentName = 'Loqman';

async function open(page: Page, path = '/?ui=rusebot', { asAgent = true } = {}) {
  await page.goto(path);
  await expect(page.locator('[data-rusebot-shell]')).toBeVisible();
  await expect(page.getByRole('textbox', { name: 'Message Loqman' })).toBeVisible();
  agentName = 'Loqman';
  if (asAgent) {
    agentName = `Tester${uid()}`;
    await openDrawerIfNarrow(page);
    await page.locator('[data-sidebar-new]').click();
    await page.locator('[data-new-agent]').click();
    await page.locator('#na-name').fill(agentName);
    await page.locator('#na-role').fill('Test agent');
    await page.locator('#na-instructions').fill('You are a test agent. Answer briefly.');
    await page.getByRole('button', { name: 'Create agent' }).click();
    await expect(page.getByRole('textbox', { name: `Message ${agentName}` })).toBeVisible();
    // Start from a fresh chat with this agent (New chat stays with the current agent).
    await openDrawerIfNarrow(page);
    await page.locator('[data-sidebar-new]').click();
    await page.locator('[data-new-chat]').click();
    await expect(page.getByRole('textbox', { name: `Message ${agentName}` })).toBeVisible();
    await page.waitForTimeout(350); // let the drawer finish closing
  }
  await expect(page.locator('[data-chathead-pill]')).toContainText('mock');
  await expect(page.getByRole('button', { name: 'Send' })).toBeDisabled();
}

async function send(page: Page, text: string) {
  const box = page.getByRole('textbox', { name: `Message ${agentName}` });
  await box.fill(text);
  await box.press('Enter');
}

async function call<T = unknown>(page: Page, method: string, params: Record<string, unknown> = {}): Promise<T> {
  const res = await page.request.post('/__agentos/call', { data: { method, params } });
  const body = await res.json();
  if (!res.ok()) throw new Error(JSON.stringify(body));
  return body.result as T;
}

async function openDrawerIfNarrow(page: Page) {
  const hamburger = page.getByRole('button', { name: 'Open chat list' });
  if (await hamburger.isVisible()) await hamburger.click();
}

const MOCK_REPLY = /Mock task execution succeeded/;

test('model images never fetch remote URLs and local images still render', async ({ page }) => {
  const remote = 'https://example.com/model-tracker.png?secret=test';
  const reply = `![Remote image](${remote})\n\n![Local image](data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=)`;
  const conversation = { id: 'image-test', title: 'Images', profile_id: null, created_at: '2026-11-15T00:00:00Z', updated_at: '2026-11-15T00:00:00Z' };
  let created = false;
  let answered = false;
  const requests: string[] = [];
  page.on('request', (request) => { if (request.url() === remote) requests.push(request.url()); });
  await page.route('https://example.com/**', (route) => route.abort());
  await page.route('**/__agentos/call', (route) => {
    const { method, params } = reqOf(route);
    const empty = { profiles: [], conversations: [], messages: [], missions: [], approvals: [], actions: [], runs: [], models: [{ id: 'mock', name: 'Mock' }] };
    if (method === 'engines.status') return okJson(route, { engines: [] });
    if (method === 'engines.capabilities') return okJson(route, { mock: { conversation_runs: true } });
    if (method === 'preferences.get') return okJson(route, { engine: 'mock', model_id: 'mock' });
    if (method === 'conversations.create') { created = true; return okJson(route, conversation); }
    if (method === 'conversations.list') return okJson(route, { conversations: created ? [conversation] : [] });
    if (method === 'conversations.messages') return okJson(route, { messages: answered ? [{ seq: 1, conversation_id: conversation.id, role: 'assistant', content: reply, created_at: conversation.created_at }] : [] });
    if (method === 'conversations.add_message') return okJson(route, { ...params, seq: 1 });
    return okJson(route, empty);
  });
  await page.route('**/__agentos/call', (route) => {
    if (reqOf(route).method === 'ceo.ask') { answered = true; return okJson(route, {
      reply,
      actions_proposed: [], agents_created: [], iterations: 1, fast: true,
    }); }
    return route.fallback();
  });
  await openBare(page);
  const box = page.getByRole('textbox', { name: 'Message Loqman' });
  await box.fill('Show the image');
  await box.press('Enter');
  const link = page.getByRole('link', { name: 'Remote image', exact: true });
  await expect(link).toHaveAttribute('href', remote);
  await expect(link).toHaveAttribute('target', '_blank');
  await expect(page.locator('.chat-md img')).toHaveCount(1);
  await expect(page.getByRole('img', { name: 'Local image', exact: true })).toHaveAttribute('src', /^data:image\//);
  expect(requests).toEqual([]);
});

test('startup loads self-hosted fonts without external network requests or CSP violations', async ({ page }) => {
  const external: string[] = [];
  page.on('request', (request) => {
    if (/^https?:/.test(request.url()) && new URL(request.url()).hostname !== '127.0.0.1') external.push(request.url());
  });
  await page.addInitScript(() => {
    (window as unknown as { cspViolations: string[] }).cspViolations = [];
    document.addEventListener('securitypolicyviolation', (event) => {
      (window as unknown as { cspViolations: string[] }).cspViolations.push(event.violatedDirective);
    });
  });
  await openBare(page);
  const loaded = await page.evaluate(async () => {
    // The families styles.css actually names (Manrope and JetBrains Mono were only in the old Google link).
    const fonts = ['Inter', 'Poppins', 'Plus Jakarta Sans'];
    return Promise.all(fonts.map(async (font) => (await document.fonts.load(`500 16px "${font}"`)).length > 0));
  });
  expect(external).toEqual([]);
  expect(await page.evaluate(() => (window as unknown as { cspViolations: string[] }).cspViolations)).toEqual([]);
  expect(loaded).toEqual([true, true, true]);
});

test.describe('look and layout match RuseBot 0.1.127', () => {
  test('wide window: sidebar, composer, bubbles and fonts', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await open(page);
    const t = `wide-${uid()}`;
    await send(page, t);
    await expect(page.locator('[data-chat-bubble]').filter({ hasText: MOCK_REPLY })).toBeVisible({ timeout: 20000 });

    const m = await page.evaluate(() => {
      const box = (el: Element | null) => (el ? el.getBoundingClientRect() : null);
      const cs = (el: Element | null) => (el ? getComputedStyle(el) : null);
      const bubble = document.querySelector('[data-chat-bubble]');
      const composer = document.querySelector('[data-tour=composer]');
      const ta = document.querySelector('textarea');
      return {
        skin: document.documentElement.dataset.skin,
        body: cs(document.body)!.fontSize,
        bodyBg: cs(document.body)!.backgroundColor,
        sidebar: box(document.querySelector('aside'))!.width,
        mainX: box(document.querySelector('main'))!.x,
        composer: box(composer)!,
        composerRadius: cs(composer)!.borderRadius,
        composerPadding: cs(composer)!.padding,
        bubbleRadius: cs(bubble)!.borderRadius,
        bubblePadding: cs(bubble)!.padding,
        bubbleFont: cs(bubble)!.fontSize,
        taFont: cs(ta)!.fontSize,
        taLine: cs(ta)!.lineHeight,
        pill: box(document.querySelector('[data-chathead-pill]'))!.height,
        column: box(document.querySelector('.chat-column'))!.width,
        hamburger: box(document.querySelector('button[aria-label="Open chat list"]'))!,
      };
    });
    expect(m.skin).toBe('meadow');
    expect(m.body).toBe('16px');
    expect(m.bodyBg).toBe('rgb(255, 255, 255)');
    expect(m.sidebar).toBe(REFERENCE.sidebarWidth);
    expect(m.mainX).toBe(REFERENCE.sidebarWidth);
    expect(m.composer.height).toBe(REFERENCE.composerHeight);
    expect(m.composer.x).toBe(REFERENCE.composerX);
    expect(m.composer.width).toBe(REFERENCE.columnWidth);
    expect(m.composerRadius).toBe(REFERENCE.composerRadius);
    expect(m.composerPadding).toBe(REFERENCE.composerPadding);
    expect(m.bubbleRadius).toBe(REFERENCE.bubbleRadius);
    expect(m.bubblePadding).toBe(REFERENCE.bubblePadding);
    expect(m.bubbleFont).toBe(REFERENCE.bubbleFont);
    expect(m.taFont).toBe(REFERENCE.textareaFont);
    expect(m.taLine).toBe(REFERENCE.textareaLine);
    expect(m.pill).toBe(REFERENCE.headerPillHeight);
    expect(m.column).toBe(REFERENCE.columnWidth);
    expect(m.hamburger.width).toBe(0);
  });

  test('narrow window: sidebar is an overlay drawer opened by the hamburger', async ({ page }) => {
    await page.setViewportSize({ width: 700, height: 900 });
    await open(page);
    const aside = page.locator('aside[data-sidebar]');
    expect((await aside.boundingBox())!.x).toBe(-REFERENCE.sidebarWidth);
    const hamburger = page.getByRole('button', { name: 'Open chat list' });
    const h = (await hamburger.boundingBox())!;
    expect([h.x, h.y, h.width, h.height]).toEqual([
      REFERENCE.hamburger.x,
      REFERENCE.hamburger.y,
      REFERENCE.hamburger.w,
      REFERENCE.hamburger.h,
    ]);
    const composer = (await page.locator('[data-tour=composer]').boundingBox())!;
    expect(composer.x).toBe(20);
    expect(composer.width).toBe(660);
    await hamburger.click();
    await expect.poll(async () => (await aside.boundingBox())!.x).toBe(0);
    await page.locator('div.bg-black\\/30').click({ position: { x: 600, y: 400 } });
    await expect.poll(async () => (await aside.boundingBox())!.x).toBe(-REFERENCE.sidebarWidth);
    await hamburger.click();
    await page.getByRole('button', { name: 'Team', exact: true }).click();
    await expect.poll(async () => (await aside.boundingBox())!.x).toBe(-REFERENCE.sidebarWidth);
    await expect(page.getByRole('heading', { name: 'Team', exact: true })).toBeVisible();
  });

  test('sidebar collapses to the icon rail and back', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await open(page);
    const aside = page.locator('aside[data-sidebar]');
    await page.getByRole('button', { name: 'Collapse sidebar' }).click();
    await expect.poll(async () => (await aside.boundingBox())!.width).toBe(92);
    await page.getByRole('button', { name: 'Expand sidebar' }).click();
    await expect.poll(async () => (await aside.boundingBox())!.width).toBe(REFERENCE.sidebarWidth);
  });

  test('a side panel on a mid-size window folds the sidebar into the rail and overlays the panel', async ({ page }) => {
    await page.setViewportSize({ width: 1100, height: 800 });
    await open(page);
    await page.getByRole('button', { name: 'Team', exact: true }).click();
    const aside = page.locator('aside[data-sidebar]');
    await expect(page.locator('[data-agent-card]').first()).toBeVisible();
    await expect.poll(async () => (await aside.boundingBox())!.width).toBe(REFERENCE.sidebarWidth);
    await page.locator('[data-agent-card] button[data-bot-id]').first().click();
    const panel = page.locator('[data-agent-panel]');
    await expect(panel).toBeVisible();
    await expect.poll(async () => (await aside.boundingBox())!.width).toBe(92);
    expect(await panel.evaluate((el) => getComputedStyle(el).position)).toBe('absolute');
    await page.getByRole('button', { name: 'Close details' }).click();
    await expect.poll(async () => (await aside.boundingBox())!.width).toBe(REFERENCE.sidebarWidth);
  });

  test('appearance settings change theme, font and chat width and persist', async ({ page }) => {
    await open(page);
    await openDrawerIfNarrow(page);
    await page.getByRole('button', { name: 'Settings', exact: true }).click();
    const dialog = page.getByRole('dialog');
    await dialog.locator('[data-settings-page=appearance]').click();
    await dialog.getByRole('combobox', { name: 'Theme' }).selectOption('midnight');
    await dialog.getByRole('combobox', { name: 'Chat width' }).selectOption('wide');
    await dialog.getByRole('combobox', { name: 'Font' }).selectOption('serif');
    await expect(page.locator('html')).toHaveAttribute('data-skin', 'midnight');
    await expect(page.locator('html')).toHaveAttribute('data-chat-width', 'wide');
    await expect(page.locator('html')).toHaveAttribute('data-font', 'serif');
    expect(await page.evaluate(() => getComputedStyle(document.body).backgroundColor)).toBe('rgb(7, 7, 7)');
    await page.keyboard.press('Escape');
    await expect(dialog).toHaveCount(0);
    await page.reload();
    await expect(page.locator('html')).toHaveAttribute('data-skin', 'midnight');
    await expect(page.locator('html')).toHaveAttribute('data-chat-width', 'wide');
  });
});

test.describe('chat on the real worker', () => {
  test('sends exactly once, shows the reply, restores history after reload', async ({ page }) => {
    await open(page);
    const text = `once-${uid()}`;
    const calls: string[] = [];
    page.on('request', (r) => {
      if (r.url().endsWith('/__agentos/call')) {
        try {
          calls.push(JSON.parse(r.postData() ?? '{}').method);
        } catch {
          /* ignore */
        }
      }
    });
    await send(page, text);
    await expect(page.locator('[data-message-row][data-role=user]').filter({ hasText: text })).toHaveCount(1);
    await expect(page.locator('[data-chat-bubble]').filter({ hasText: MOCK_REPLY })).toBeVisible({ timeout: 20000 });
    expect(calls.filter((c) => c === 'runs.start_turn')).toHaveLength(1);
    expect(calls.filter((c) => c === 'conversations.create')).toHaveLength(1);

    const conversations = await call<{ conversations: Array<{ id: string; title: string }> }>(page, 'conversations.list');
    const conv = conversations.conversations.find((c) => c.title.startsWith(text))!;
    const msgs = await call<{ messages: Array<{ role: string; content: string }> }>(page, 'conversations.messages', {
      conversation_id: conv.id,
    });
    expect(msgs.messages.map((m) => m.role)).toEqual(['user', 'assistant']);

    await page.reload();
    await expect(page.locator('[data-message-row][data-role=user]').filter({ hasText: text })).toHaveCount(1);
    await expect(page.locator('[data-chat-bubble]').filter({ hasText: MOCK_REPLY })).toBeVisible();
    await expect(page.locator('[data-chathead-conversation]')).toContainText(text);
  });

  test('Arabic and mixed text with paths and code keep their direction', async ({ page }) => {
    await open(page);
    const text = `مرحبا ${uid()} C:\\work\\src\\app.ts \`npm run build\``;
    await send(page, text);
    await expect(page.locator('[data-chat-bubble]').filter({ hasText: MOCK_REPLY })).toBeVisible({ timeout: 20000 });
    const bubble = page.locator('[data-message-row][data-role=user] .chat-text').filter({ hasText: 'مرحبا' });
    await expect(bubble).toContainText('C:\\work\\src\\app.ts');
    await expect.poll(() => bubble.evaluate((el) => getComputedStyle(el).unicodeBidi)).toBe('plaintext');
  });

  test('stop cancels a running turn without an error banner', async ({ page }) => {
    await open(page);
    await send(page, `slow-${uid()} [mock:delay=30000]`);
    await expect(page.locator('[data-working]')).toBeVisible();
    await page.getByRole('button', { name: 'Stop' }).click();
    await expect(page.locator('[data-working]')).toHaveCount(0, { timeout: 15000 });
    await expect(page.getByRole('alert')).toHaveCount(0);
    await expect(page.getByRole('button', { name: 'Send' })).toBeVisible();
    await expect(page.locator('[data-chat-bubble]').filter({ hasText: MOCK_REPLY })).toHaveCount(0);
  });

  test('switching conversations while a turn runs keeps each reply in its own chat', async ({ page }) => {
    await open(page);
    const a = `alpha-${uid()}`;
    const b = `beta-${uid()}`;
    await send(page, `${a} [mock:delay=2500]`);
    await expect(page.locator('[data-working]')).toBeVisible();
    await page.locator('[data-sidebar-new]').click();
    await page.locator('[data-new-chat]').click();
    await expect(page.locator('[data-message-row]')).toHaveCount(0);
    await send(page, b);
    await expect(page.locator('[data-chat-bubble]').filter({ hasText: MOCK_REPLY })).toBeVisible({ timeout: 20000 });
    await expect(page.locator('[data-message-row]').filter({ hasText: a })).toHaveCount(0);
    await openDrawerIfNarrow(page);
    await page.locator('[data-sidebar-thread-row]').filter({ hasText: a }).click();
    await expect(page.locator('[data-chat-bubble]').filter({ hasText: MOCK_REPLY })).toBeVisible({ timeout: 20000 });
    await expect(page.locator('[data-message-row]').filter({ hasText: b })).toHaveCount(0);
    await expect(page.locator('[data-working]')).toHaveCount(0);
  });

  test('a failed start shows the error and keeps the draft', async ({ page }) => {
    await open(page);
    await page.route('**/__agentos/call', async (route: Route) => {
      const req = JSON.parse(route.request().postData() ?? '{}');
      if (req.method === 'runs.start_turn') {
        await route.fulfill({
          status: 409,
          contentType: 'application/json',
          body: JSON.stringify({ status: 409, detail: 'Conversation already has an active run' }),
        });
        return;
      }
      await route.continue();
    });
    const text = `fail-${uid()}`;
    await send(page, text);
    await expect(page.getByRole('alert')).toContainText('Conversation already has an active run');
    await expect(page.getByRole('textbox', { name: `Message ${agentName}` })).toHaveValue(text);
    await page.getByRole('button', { name: 'Dismiss' }).click();
    await expect(page.getByRole('alert')).toHaveCount(0);
  });

  test('drafts are kept per conversation', async ({ page }) => {
    await open(page);
    const a = `draftA-${uid()}`;
    await send(page, a);
    await expect(page.locator('[data-chat-bubble]').filter({ hasText: MOCK_REPLY })).toBeVisible({ timeout: 20000 });
    const box = page.getByRole('textbox', { name: `Message ${agentName}` });
    await box.fill('half-written thought');
    await page.locator('[data-sidebar-new]').click();
    await page.locator('[data-new-chat]').click();
    await expect(box).toHaveValue('');
    await box.fill('other draft');
    await openDrawerIfNarrow(page);
    await page.locator('[data-sidebar-thread-row]').filter({ hasText: a }).click();
    await expect(box).toHaveValue('half-written thought');
    await page.reload();
    await expect(page.getByRole('textbox', { name: `Message ${agentName}` })).toHaveValue('half-written thought');
  });

  test('Open chat reuses the agent conversation and shows the agent', async ({ page }) => {
    await open(page);
    await page.locator('[data-sidebar-nav=team]').click();
    const chatBtn = page.locator('[aria-label^="Open chat with"]').first();
    await chatBtn.click();
    await expect(page.locator('[data-chathead-conversation]')).toContainText('—');
    const rows = await page.locator('[data-sidebar-thread-row]').count();
    await page.locator('[data-sidebar-nav=team]').click();
    await chatBtn.click();
    await expect(page.locator('[data-sidebar-thread-row]')).toHaveCount(rows);
    await expect(page.locator('[data-chathead-pill]')).not.toContainText('AgentOS');
  });

  test('the sidebar switches chats', async ({ page }) => {
    await open(page);
    const a = `menu-${uid()}`;
    await send(page, a);
    await expect(page.locator('[data-chat-bubble]').filter({ hasText: MOCK_REPLY })).toBeVisible({ timeout: 20000 });
    await page.locator('[data-sidebar-new]').click();
    await page.locator('[data-new-chat]').click();
    await openDrawerIfNarrow(page);
    await page.locator('[data-sidebar-thread-row]').filter({ hasText: a }).first().click();
    await expect(page.locator('[data-message-row][data-role=user]').filter({ hasText: a })).toBeVisible();
  });
});

test.describe('earlier chats from the classic interface', () => {
  const thread = {
    id: 'legacy-1',
    question: 'سؤال قديم about C:\\work\\src\\app.ts',
    reply: 'مرحبا بك\n\n```ts\nconst x = 1;\n```\n\nUse `npm run build` at C:\\work\\src.',
    proposed: [],
    ts: Date.UTC(2026, 10, 1, 9, 30),
    turns: [
      {
        question: 'سؤال قديم about C:\\work\\src\\app.ts',
        reply: 'مرحبا بك\n\n```ts\nconst x = 1;\n```\n\nUse `npm run build` at C:\\work\\src.',
        proposed: [],
        ts: Date.UTC(2026, 10, 1, 9, 30),
      },
    ],
  };

  test.beforeEach(async ({ page }) => {
    await page.addInitScript((t) => {
      localStorage.setItem('agentos_ceo_threads_v1', JSON.stringify([t]));
    }, thread);
  });

  test('browser-stored CEO threads open read-only history with rendered markdown', async ({ page }) => {
    await open(page, '/?ui=rusebot', { asAgent: false });
    await openDrawerIfNarrow(page);
    await expect(page.getByText('Earlier chats')).toBeVisible();
    await page.locator('[data-sidebar-thread-row="legacy:legacy-1"]').click();
    await expect(page.locator('[data-message-row][data-role=user]')).toContainText('سؤال قديم');
    const reply = page.locator('[data-message-row][data-role=assistant]');
    await expect(reply.locator('p[dir=rtl]')).toContainText('مرحبا بك');
    await expect(reply.locator('code.ui-code-chip')).toContainText('npm run build');
    await expect(reply.locator('[dir=ltr]').filter({ hasText: 'TypeScript' }).first()).toBeVisible();
    await expect(reply.getByRole('button', { name: 'Copy code to clipboard' })).toBeVisible();
    await page.reload();
    await expect(page.locator('[data-message-row][data-role=user]')).toContainText('سؤال قديم');
  });

  test('sending from an earlier chat starts a fresh real conversation', async ({ page }) => {
    await open(page, '/?ui=rusebot', { asAgent: false });
    await openDrawerIfNarrow(page);
    await page.locator('[data-sidebar-thread-row="legacy:legacy-1"]').click();
    const text = `fromlegacy-${uid()}`;
    await send(page, text); // old chats were with Loqman, so the answer comes from the CEO loop
    await expect(page.locator('[data-message-row][data-role=assistant]').first()).toBeVisible({ timeout: 30000 });
    await expect(page.locator('[data-message-row]').filter({ hasText: 'سؤال قديم' })).toHaveCount(0);
  });
});

test.describe('approvals and deliverables', () => {
  test('tool approval card decides through approvals.decide', async ({ page }) => {
    const decided: Array<Record<string, unknown>> = [];
    let pending = true;
    await page.route('**/__agentos/call', async (route: Route) => {
      const req = JSON.parse(route.request().postData() ?? '{}');
      if (req.method === 'approvals.list') {
        const approvals = pending
          ? [{ id: 'ap-1', run_id: 'run-fixture', action_summary: 'Write report.md in the workspace', scope: 'workspace_write', status: 'pending', expires_at: null }]
          : [];
        await route.fulfill({ contentType: 'application/json', body: JSON.stringify({ result: { approvals } }) });
        return;
      }
      if (req.method === 'approvals.decide') {
        decided.push(req.params);
        pending = false;
        await route.fulfill({
          contentType: 'application/json',
          body: JSON.stringify({ result: { id: 'ap-1', run_id: 'run-fixture', action_summary: '', scope: '', status: 'approved', expires_at: null } }),
        });
        return;
      }
      await route.continue();
    });
    await open(page);
    await openDrawerIfNarrow(page);
    await page.locator('[data-needs-you]').click();
    const card = page.locator('[data-approval-card=tool]');
    await expect(card).toContainText('Write report.md in the workspace');
    await expect(card).toContainText('workspace_write');
    await card.getByRole('button', { name: 'Approve' }).click();
    await expect(card).toContainText('Approved');
    expect(decided).toEqual([{ run_id: 'run-fixture', approval_id: 'ap-1', decision: 'approve' }]);
  });

  test('a manager proposal that fails to apply shows the failure, not Approved', async ({ page }) => {
    let pending = true;
    await page.route('**/__agentos/call', async (route: Route) => {
      const req = JSON.parse(route.request().postData() ?? '{}');
      const reply = (result: unknown) => route.fulfill({ contentType: 'application/json', body: JSON.stringify({ result }) });
      if (req.method === 'ceo.actions')
        return reply({ actions: pending ? [{ id: 'ca-1', kind: 'update_contract', summary: 'Tighten the writer contract', status: 'pending' }] : [] });
      if (req.method === 'ceo.approve') {
        pending = false;
        return reply({ id: 'ca-1', kind: 'update_contract', summary: 'Tighten the writer contract', status: 'failed', decision_note: 'Action failed.' });
      }
      await route.continue();
    });
    await open(page);
    await openDrawerIfNarrow(page);
    await page.locator('[data-needs-you]').click();
    const card = page.locator('[data-approval-card=manager]');
    await expect(card).toContainText('Tighten the writer contract');
    await card.getByRole('button', { name: 'Approve' }).click();
    await expect(card).toContainText('Action failed.');
    await expect(card).not.toContainText('Approved');
  });

  test('a proposal Loqman already applied shows what happened, not an error', async ({ page }) => {
    let pending = true;
    const statusQueries: string[] = [];
    await page.route('**/__agentos/call', async (route: Route) => {
      const req = JSON.parse(route.request().postData() ?? '{}');
      const reply = (result: unknown) => route.fulfill({ contentType: 'application/json', body: JSON.stringify({ result }) });
      const team = { id: 'ca-auto', kind: 'new_team', summary: 'Create the Growth team', status: 'pending' };
      if (req.method === 'ceo.actions') {
        const status = req.params?.status ?? 'pending';
        if (status === 'pending') return reply({ actions: pending ? [team] : [] });
        statusQueries.push(status);
        return reply({ actions: status === 'approved' ? [{ ...team, status: 'approved', decision_note: 'Created by the CEO' }] : [] });
      }
      if (req.method === 'ceo.deny') {
        pending = false;
        return route.fulfill({ status: 409, contentType: 'application/json', body: JSON.stringify({ status: 409, detail: 'Action already decided or being processed.' }) });
      }
      await route.continue();
    });
    await open(page);
    await openDrawerIfNarrow(page);
    await page.locator('[data-needs-you]').click();
    const card = page.locator('[data-approval-card=manager]');
    await expect(card).toContainText('Create the Growth team');
    await card.getByRole('button', { name: 'Deny', exact: true }).click();
    await expect(card).toContainText('Loqman already approved this');
    await expect(card).not.toContainText('already decided or being processed');
    await expect(card.getByRole('button', { name: 'Approve', exact: true })).toHaveCount(0);
    expect(statusQueries).toContain('approved');
  });

  test('tool approval can be denied', async ({ page }) => {
    const decided: Array<Record<string, unknown>> = [];
    await page.route('**/__agentos/call', async (route: Route) => {
      const req = JSON.parse(route.request().postData() ?? '{}');
      if (req.method === 'approvals.list') {
        await route.fulfill({
          contentType: 'application/json',
          body: JSON.stringify({ result: { approvals: [{ id: 'ap-2', run_id: 'run-fixture', action_summary: 'Run npm install', scope: 'shell', status: 'pending', expires_at: null }] } }),
        });
        return;
      }
      if (req.method === 'approvals.decide') {
        decided.push(req.params);
        await route.fulfill({ contentType: 'application/json', body: JSON.stringify({ result: {} }) });
        return;
      }
      await route.continue();
    });
    await open(page);
    await openDrawerIfNarrow(page);
    await page.locator('[data-needs-you]').click();
    await page.locator('[data-approval-card=tool]').getByRole('button', { name: 'Deny' }).click();
    await expect(page.locator('[data-approval-card=tool]')).toContainText('Denied');
    expect(decided[0]).toMatchObject({ approval_id: 'ap-2', decision: 'deny' });
  });

  test('a manager proposal is approved from Results through ceo.approve', async ({ page }) => {
    await open(page);
    await call(page, 'profiles.create', { id: 'planner', name: 'Planner', role_type: 'planner', instructions: 'Plan work.' }).catch(() => undefined);
    await call(page, 'ceo.ask', { question: 'عدل سرعة الموظف' });
    const pending = await call<{ actions: Array<{ id: string; summary: string }> }>(page, 'ceo.actions', { status: 'pending' });
    expect(pending.actions.length).toBeGreaterThan(0);
    await page.reload();
    await openDrawerIfNarrow(page);
    await page.locator('[data-needs-you]').click();
    const card = page.locator('[data-approval-card=manager]').first();
    await expect(card).toBeVisible();
    await card.getByRole('button', { name: 'Approve' }).click();
    await expect(card).toContainText('Approved');
    const after = await call<{ actions: Array<{ id: string }> }>(page, 'ceo.actions', { status: 'pending' });
    expect(after.actions.length).toBeLessThan(pending.actions.length);
  });

  test('a finished reply becomes a deliverable that is reviewed, downloaded and listed in Results', async ({ page }) => {
    await open(page);
    await send(page, `deliver-${uid()}`);
    await expect(page.locator('[data-chat-bubble]').filter({ hasText: MOCK_REPLY })).toBeVisible({ timeout: 20000 });
    await page.getByRole('button', { name: 'Save reply as deliverable for review' }).click();
    const card = page.locator('[data-deliverable]').first();
    await expect(card).toHaveAttribute('data-deliverable-status', 'pending_review');
    await expect(card).toContainText('Awaiting review');
    await expect(card.getByRole('button', { name: 'Download' })).toHaveCount(0);
    await card.getByRole('button', { name: /^Preview / }).click();
    await expect(card.locator('pre')).toContainText('Mock task execution succeeded');
    await card.getByRole('button', { name: 'Approve' }).click();
    await expect(card).toHaveAttribute('data-deliverable-status', 'approved');
    const [download] = await Promise.all([page.waitForEvent('download'), card.getByRole('button', { name: 'Download' }).click()]);
    expect(download.suggestedFilename()).toMatch(/\.(md|txt|json)$/);

    await openDrawerIfNarrow(page);
    await page.getByRole('button', { name: 'Results', exact: true }).click();
    await expect(page.locator('[data-view=results] [data-deliverable-status=approved]').first()).toBeVisible();
  });

  test('runs and deliverables survive cleared browser storage', async ({ page }) => {
    await open(page);
    await send(page, `durable-${uid()}`);
    await expect(page.locator('[data-chat-bubble]').filter({ hasText: MOCK_REPLY })).toBeVisible({ timeout: 20000 });
    await page.getByRole('button', { name: 'Save reply as deliverable for review' }).click();
    await expect(page.locator('[data-deliverable]').first()).toHaveAttribute('data-deliverable-status', 'pending_review');
    // The browser-side run index is only a cache: wipe it and reload.
    await page.evaluate(() => localStorage.removeItem('agentos.rusebot.runs.v1'));
    await page.reload();
    await expect(page.locator('[data-chat-bubble]').filter({ hasText: MOCK_REPLY })).toBeVisible({ timeout: 20000 });
    await expect(page.locator('[data-deliverable]').first()).toHaveAttribute('data-deliverable-status', 'pending_review');
    await openDrawerIfNarrow(page);
    await page.getByRole('button', { name: 'Results', exact: true }).click();
    await expect(page.locator('[data-view=results] [data-deliverable]').first()).toBeVisible();
  });

  test('a rejected deliverable cannot be downloaded', async ({ page }) => {
    await open(page);
    await send(page, `reject-${uid()}`);
    await expect(page.locator('[data-chat-bubble]').filter({ hasText: MOCK_REPLY })).toBeVisible({ timeout: 20000 });
    await page.getByRole('button', { name: 'Save reply as deliverable for review' }).click();
    const card = page.locator('[data-deliverable]').first();
    await card.getByRole('button', { name: 'Reject' }).click();
    await expect(card).toHaveAttribute('data-deliverable-status', 'rejected');
    await expect(card.getByRole('button', { name: 'Download' })).toHaveCount(0);
  });
});

test.describe('team, contracts and settings', () => {
  test('team map shows departments and members, and opens a member panel', async ({ page }) => {
    await open(page);
    await openDrawerIfNarrow(page);
    await page.getByRole('button', { name: 'Team', exact: true }).click();
    await expect(page.getByRole('heading', { name: 'Team', exact: true })).toBeVisible();
    await expect(page.locator('[data-agent-card]').first()).toBeVisible();
    const name = (await page.locator('[data-agent-card] button[data-bot-id]').first().getAttribute('aria-label'))!.replace('Open ', '');
    await page.locator('[data-agent-card] button[data-bot-id]').first().click();
    const panel = page.locator('[data-agent-panel]');
    await expect(panel.getByRole('heading', { name })).toBeVisible();
    await expect(panel).toContainText('Status');
    await page.getByRole('button', { name: 'Close details' }).click();
    await expect(panel).toHaveCount(0);
  });

  test('"Open chat" on a member starts a real conversation', async ({ page }) => {
    await open(page);
    await openDrawerIfNarrow(page);
    await page.getByRole('button', { name: 'Team', exact: true }).click();
    const before = (await call<{ conversations: unknown[] }>(page, 'conversations.list')).conversations.length;
    await page.locator('[data-agent-card]').first().getByRole('button', { name: /^Open chat with / }).click();
    await expect(page.getByRole('textbox', { name: /^Message / })).toBeVisible();
    await expect.poll(async () => (await call<{ conversations: unknown[] }>(page, 'conversations.list')).conversations.length).toBeGreaterThanOrEqual(before);
  });

  test('delegating a task creates a mission that is listed with its status', async ({ page }) => {
    await open(page);
    await openDrawerIfNarrow(page);
    await page.getByRole('button', { name: 'Team', exact: true }).click();
    await page.locator('[data-agent-card] button[data-bot-id]').first().click();
    const task = `Draft a launch checklist ${uid()}`;
    await page.getByLabel('Delegate a task').fill(task);
    await page.getByRole('button', { name: 'Delegate', exact: true }).click();
    await expect(page.getByRole('status').filter({ hasText: 'Delegated.' })).toBeVisible({ timeout: 20000 });
    await expect(page.locator('[data-mission]').filter({ hasText: task })).toBeVisible({ timeout: 20000 });
  });

  test('delegating from an agent panel runs the work under that agent profile', async ({ page }) => {
    const sent: Array<{ method: string; params: Record<string, unknown> }> = [];
    await page.route('**/__agentos/call', async (route: Route) => {
      const req = JSON.parse(route.request().postData() ?? '{}');
      if (String(req.method).startsWith('missions.')) sent.push({ method: req.method, params: req.params ?? {} });
      await route.continue();
    });
    await open(page);
    await openDrawerIfNarrow(page);
    await page.getByRole('button', { name: 'Team', exact: true }).click();
    await page.getByRole('button', { name: `Open ${agentName}`, exact: true }).click();
    const panel = page.locator('[data-agent-panel]');
    const profileId = await panel.getAttribute('data-agent-panel');
    const task = `Summarise the weekly notes ${uid()}`;
    await page.getByLabel('Delegate a task').fill(task);
    await page.getByRole('button', { name: 'Delegate', exact: true }).click();
    await expect(page.getByRole('status').filter({ hasText: 'Delegated.' })).toBeVisible({ timeout: 20000 });
    const delegated = sent.filter((c) => c.method === 'missions.delegate');
    expect(delegated).toHaveLength(1);
    expect(delegated[0].params.agent_profile_id).toBe(profileId);
    expect(delegated[0].params.instructions).toBe(task);
    expect(delegated[0].params.model_id).toBeUndefined(); // the worker resolves engine and model
    expect(sent.some((c) => c.method === 'missions.create')).toBe(false);
  });

  test('delegated missions update while active and open to their result and errors', async ({ page }) => {
    let listCalls = 0;
    await page.route('**/__agentos/call', async (route: Route) => {
      const req = JSON.parse(route.request().postData() ?? '{}');
      if (req.method !== 'missions.list') return route.continue();
      listCalls += 1;
      const done = listCalls > 2;
      const missions = [
        {
          id: 'm-ok', title: 'Write the weekly note', objective: 'x', status: done ? 'succeeded' : 'running',
          final_summary: done ? { result: 'FINAL-SUMMARY-TEXT' } : null, error_summary: null,
          tasks: [{ id: 't-ok', title: 'Draft', status: done ? 'succeeded' : 'running', output: done ? { result: 'TASK-OUTPUT-TEXT' } : null, error_message: null }],
        },
        {
          id: 'm-bad', title: 'Fetch the report', objective: 'y', status: 'failed', final_summary: null, error_summary: 'MISSION-ERROR-TEXT',
          tasks: [{ id: 't-bad', title: 'Fetch', status: 'failed', output: null, error_message: 'TASK-ERROR-TEXT' }],
        },
      ];
      await route.fulfill({ contentType: 'application/json', body: JSON.stringify({ result: { missions } }) });
    });
    await open(page);
    await openDrawerIfNarrow(page);
    await page.getByRole('button', { name: 'Team', exact: true }).click();
    const ok = page.locator('[data-mission=m-ok]');
    await expect(ok).toContainText('running');
    // No navigation: the list refreshes by itself until the mission is terminal.
    await expect(ok).toContainText('succeeded', { timeout: 15000 });
    await ok.getByRole('button').click();
    await expect(ok.locator('[data-mission-detail]')).toContainText('FINAL-SUMMARY-TEXT');
    await expect(ok.locator('[data-mission-detail]')).toContainText('TASK-OUTPUT-TEXT');
    const bad = page.locator('[data-mission=m-bad]');
    await bad.getByRole('button').click();
    await expect(bad.locator('[data-mission-detail]')).toContainText('MISSION-ERROR-TEXT');
    await expect(bad.locator('[data-mission-detail]')).toContainText('TASK-ERROR-TEXT');
    // All missions are terminal now: polling stops.
    const settled = listCalls;
    await page.waitForTimeout(4500);
    expect(listCalls).toBe(settled);
  });

  test('contracts view lists every employee and opens the contract', async ({ page }) => {
    await open(page);
    await openDrawerIfNarrow(page);
    await page.getByRole('button', { name: 'Contracts', exact: true }).click();
    await expect(page.getByRole('heading', { name: 'Contracts' })).toBeVisible();
    const rows = page.locator('[data-contract-row]');
    expect(await rows.count()).toBeGreaterThan(0);
    await rows.first().click();
    await expect(page.locator('[data-agent-panel]').getByText('Contract', { exact: true })).toBeVisible();
  });

  test('settings: search filters pages, profile name is saved, attribution is shown', async ({ page }) => {
    await open(page);
    await openDrawerIfNarrow(page);
    await page.getByRole('button', { name: 'Settings', exact: true }).click();
    const dialog = page.getByRole('dialog');
    await dialog.getByLabel('Search settings').fill('provid');
    await expect(dialog.locator('[data-settings-page]')).toHaveCount(1);
    await dialog.getByLabel('Search settings').fill('');
    await dialog.getByLabel('Your name').fill('Sara');
    await dialog.locator('[data-settings-page=about]').click();
    await expect(dialog).toContainText('RuseBot 0.1.127');
    await expect(dialog).toContainText('Apache License 2.0');
    await page.keyboard.press('Escape');
    await page.reload();
    await openDrawerIfNarrow(page);
    await expect(page.getByRole('button', { name: 'Settings', exact: true })).toContainText('Sara');
  });
});


test.describe('agents: created by the user or by Loqman, no built-in specialists', () => {
  test('only Loqman ships with the app', async ({ page }) => {
    await open(page, '/?ui=rusebot', { asAgent: false });
    const profiles = await call<{ profiles: Array<{ id: string; name: string }> }>(page, 'profiles.list');
    expect(profiles.profiles.map((p) => p.id)).toContain('custom');
    const names = profiles.profiles.map((p) => p.name);
    for (const gone of ['Dr. Sara', 'Eng. Tarek', 'Prof. Yasmin', 'Ahmed', 'Khaled', 'Layla', 'Omar', 'Nour', 'Mona']) {
      expect(names).not.toContain(gone);
    }
  });

  test('the + menu creates an agent that appears in Team and gets its own chat', async ({ page }) => {
    await open(page, '/?ui=rusebot', { asAgent: false });
    await openDrawerIfNarrow(page);
    await page.locator('[data-sidebar-new]').click();
    await expect(page.getByRole('menuitem')).toHaveText(['New chat', 'New agent']);
    await page.locator('[data-new-agent]').click();
    const name = `Maker${uid()}`;
    await page.locator('#na-name').fill(name);
    await page.locator('#na-role').fill('Maker');
    await expect(page.getByRole('button', { name: 'Create agent' })).toBeDisabled(); // instructions are required
    await page.locator('#na-instructions').fill('Make things.');
    await page.getByRole('button', { name: 'Create agent' }).click();
    await expect(page.locator('[data-chathead-pill]')).toContainText(name);
    await openDrawerIfNarrow(page);
    await page.locator('[data-sidebar-nav=team]').click();
    await expect(page.locator(`[aria-label="Open chat with ${name}"]`)).toBeVisible();
  });

  test('an agent can be deleted from its panel', async ({ page }) => {
    await open(page, '/?ui=rusebot', { asAgent: false });
    const name = `Gone${uid()}`;
    await call(page, 'profiles.create', { name, role_type: 'custom', instructions: 'Temporary.' });
    await openDrawerIfNarrow(page);
    await page.locator('[data-sidebar-nav=team]').click();
    await page.locator(`[aria-label="Open ${name}"]`).click();
    page.once('dialog', (d) => void d.accept());
    await page.getByRole('button', { name: 'Delete agent' }).click();
    await expect(page.locator(`[aria-label="Open ${name}"]`)).toHaveCount(0);
  });

  test('Loqman creates an agent on his own, with no approval step', async ({ page }) => {
    await open(page, '/?ui=rusebot', { asAgent: false });
    const before = (await call<{ profiles: unknown[] }>(page, 'profiles.list')).profiles.length;
    await send(page, 'أضف موظف جديد');
    await expect(page.locator('[data-role=assistant]').filter({ hasText: 'Agent created' })).toBeVisible({ timeout: 30000 });
    expect((await call<{ profiles: unknown[] }>(page, 'profiles.list')).profiles.length).toBe(before + 1);
    expect((await call<{ actions: unknown[] }>(page, 'ceo.actions', { status: 'pending' })).actions).toHaveLength(0);
    await openDrawerIfNarrow(page);
    await page.locator('[data-sidebar-nav=team]').click();
    const total = (await call<{ profiles: unknown[] }>(page, 'profiles.list')).profiles.length;
    await expect(page.locator('[data-agent-card]')).toHaveCount(total);
  });
});


// ---------------------------------------------------------------------------------------
// AI providers (Providers v2). Every engine call is stubbed: nothing here may reach a real
// engine, spend quota or open a terminal.
// ---------------------------------------------------------------------------------------
type StubEngine = {
  engine_id: string;
  title: string;
  vendor: string;
  state: 'ready' | 'needs_login' | 'not_installed' | 'unverified' | 'error';
  version?: string;
};

const STUB_INSTALL: Record<string, string> = { ready: 'installed', needs_login: 'installed', unverified: 'installed', not_installed: 'not_installed', error: 'unknown' };
const STUB_AUTH: Record<string, string> = { ready: 'authenticated', needs_login: 'unauthenticated', unverified: 'unknown', not_installed: 'unknown', error: 'unknown' };

const FIVE_STATES: StubEngine[] = [
  { engine_id: 'not-installed-e', title: 'Zeta', vendor: 'Vendor Z', state: 'not_installed' },
  { engine_id: 'error-e', title: 'Epsilon', vendor: 'Vendor E', state: 'error' },
  { engine_id: 'needs-login-e', title: 'Delta', vendor: 'Vendor D', state: 'needs_login', version: '2.0.1' },
  { engine_id: 'unverified-e', title: 'Gamma', vendor: 'Vendor G', state: 'unverified', version: '1.5.0' },
  { engine_id: 'ready-e', title: 'Alpha', vendor: 'Vendor A', state: 'ready', version: '3.1.4' },
];

type Handler = (params: Record<string, unknown>) => unknown | Promise<unknown>;

async function stubProviders(
  page: Page,
  engines: StubEngine[],
  handlers: Record<string, Handler> = {}
): Promise<Array<{ method: string; params: Record<string, unknown> }>> {
  const calls: Array<{ method: string; params: Record<string, unknown> }> = [];
  await page.route('**/__agentos/call', async (route: Route) => {
    const req = JSON.parse(route.request().postData() ?? '{}') as { method: string; params?: Record<string, unknown> };
    const params = req.params ?? {};
    calls.push({ method: req.method, params });
    const reply = (result: unknown) => route.fulfill({ contentType: 'application/json', body: JSON.stringify({ result }) });
    const custom = handlers[req.method];
    if (custom) {
      try {
        return reply(await custom(params)); // a handler may be slow (await) or fail (throw)
      } catch (e) {
        return route.fulfill({ status: 500, contentType: 'application/json', body: JSON.stringify({ detail: e instanceof Error ? e.message : String(e) }) });
      }
    }
    if (req.method === 'engines.status')
      return reply({
        engines: engines.map((e) => ({
          engine_id: e.engine_id,
          display_name: e.title,
          title: e.title,
          vendor: e.vendor,
          state: e.state,
          install_state: STUB_INSTALL[e.state],
          auth_state: STUB_AUTH[e.state],
          version: e.version ?? null,
          executable: e.state === 'not_installed' ? null : `/stub/${e.engine_id}`,
          detail: 'stub detail',
          checked_at: '2026-11-14T10:00:00+00:00',
        })),
      });
    if (req.method === 'engines.capabilities')
      return reply({ mock: { conversation_runs: true }, ...Object.fromEntries(engines.map((e) => [e.engine_id, { conversation_runs: true }])) });
    if (req.method === 'engines.models')
      return reply({ models: [{ id: 'm1', name: 'Fixture Model One' }, { id: 'm2', name: 'Fixture Model Two', is_free: true, variants: ['low', 'high'] }] });
    if (req.method === 'engines.check') {
      const own = engines.find((e) => e.engine_id === params.engine_id);
      return reply({
        engine_id: params.engine_id,
        error_code: null,
        runtime: { status: 'ok' },
        discovery: { install_state: STUB_INSTALL[own?.state ?? 'ready'], auth_state: STUB_AUTH[own?.state ?? 'ready'] },
      });
    }
    if (req.method === 'engines.setup')
      return reply({
        engine_id: params.engine_id,
        login_method: 'cli',
        command: ['stub', 'login'],
        instructions: 'Stub instructions.',
        install: { command: ['npm', 'install', '-g', 'stub-cli'], url: null },
        login: { command: ['stub', 'login'] },
        docs_url: 'https://example.com/docs',
      });
    if (req.method === 'engines.open_setup') return reply({ launched: false, reason: 'stubbed' });
    if (req.method === 'engines.test')
      return reply({ engine_id: params.engine_id, model_id: 'm1', ok: true, duration_ms: 1200, reply_preview: 'OK', error_code: null, error_message: null });
    if (req.method === 'preferences.get') return reply({ engine: null, model_id: null });
    if (req.method === 'preferences.set_default') return reply({ engine: params.engine, model_id: params.model_id });
    if (req.method === 'usage.summary')
      return reply({
        by_engine: [
          { key: 'ready-e', events: 4, succeeded: 3, failed: 1, total_duration_ms: 8000, avg_duration_ms: 2000, tokens_in: 1200, tokens_out: 340, tokens_cache_read: 0, token_usage_unknown_events: 0 },
        ],
      });
    return route.continue();
  });
  return calls;
}

// Without the mock engine the real provider logic decides (no engine is preferred over the others).
async function openBare(page: Page) {
  await page.goto('/?ui=rusebot');
  await expect(page.locator('[data-rusebot-shell]')).toBeVisible();
  await expect(page.getByRole('textbox', { name: 'Message Loqman' })).toBeVisible();
}

const capsWithoutMock = (engines: StubEngine[]) => () => Object.fromEntries(engines.map((e) => [e.engine_id, { conversation_runs: true }]));
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
const MODELS = [{ id: 'm1', name: 'Fixture Model One' }, { id: 'm2', name: 'Fixture Model Two' }];

async function openAiSettings(page: Page, bare = false) {
  if (bare) await openBare(page);
  else await open(page);
  await openDrawerIfNarrow(page);
  await page.getByRole('button', { name: 'Settings', exact: true }).click();
  const dialog = page.getByRole('dialog');
  await dialog.locator('[data-settings-page=ai]').click();
  await expect(dialog.locator('[data-settings-block=ai]')).toBeVisible();
  return dialog;
}

const expandRow = (dialog: ReturnType<Page['getByRole']>, id: string) => dialog.locator(`[data-engine-card=${id}] [aria-expanded]`).click();

test.describe('settings: AI providers (engine calls stubbed)', () => {
  test('rows are ordered by state and show an accurate pill and primary action for all five states', async ({ page }) => {
    await stubProviders(page, FIVE_STATES);
    const dialog = await openAiSettings(page);
    await expect(dialog.getByRole('heading', { name: 'Model providers and accounts' })).toBeVisible();
    await expect(dialog.locator('[data-engine-card]')).toHaveText([/Alpha/, /Gamma/, /Delta/, /Epsilon/, /Zeta/]);
    const expected: Array<[string, string, string]> = [
      ['ready-e', 'Ready', 'Test'],
      ['unverified-e', 'Sign-in not confirmed', 'Test'],
      ['needs-login-e', 'Sign in required', 'Sign in'],
      ['error-e', 'Problem', 'Check again'],
      ['not-installed-e', 'Not installed', 'Install'],
    ];
    for (const [id, pill, action] of expected) {
      const card = dialog.locator(`[data-engine-card=${id}]`);
      await expect(card.locator('[data-engine-state]')).toHaveText(pill);
      await expect(card.locator(`[data-engine-primary=${id}]`)).toHaveText(action);
    }
    await expect(dialog.locator('[data-engine-card=ready-e]')).toContainText('Vendor A · v3.1.4');
  });

  test('a row opens as a proper disclosure; ready rows load models and check the connection', async ({ page }) => {
    const calls = await stubProviders(page, FIVE_STATES);
    const dialog = await openAiSettings(page);
    const toggle = dialog.locator('[data-engine-card=ready-e] [aria-expanded]');
    await expect(toggle).toHaveAttribute('aria-expanded', 'false');
    await toggle.click();
    await expect(toggle).toHaveAttribute('aria-expanded', 'true');
    const panelId = await toggle.getAttribute('aria-controls');
    await expect(page.locator(`[id="${panelId}"]`)).toBeVisible();
    await expect(dialog.getByText('Fixture Model One')).toBeVisible();
    await expect(dialog.getByText('Fixture Model Two')).toBeVisible();
    await dialog.locator('[data-engine-check=ready-e]').click();
    await expect(dialog.getByRole('status').filter({ hasText: 'Connected' })).toBeVisible();
    await expect(dialog.locator('[data-engine-usage]')).toContainText('75%');
    expect(calls.some((c) => c.method === 'engines.test')).toBe(false);
  });

  test('not installed: numbered install, sign-in and check steps; Open terminal falls back to the command when it cannot launch', async ({ page }) => {
    const calls = await stubProviders(page, FIVE_STATES);
    const dialog = await openAiSettings(page);
    await expandRow(dialog, 'not-installed-e');
    const card = dialog.locator('[data-engine-card=not-installed-e]');
    const steps = card.getByRole('listitem');
    await expect(steps).toHaveCount(3);
    await expect(steps.nth(0)).toContainText('Install Zeta');
    await expect(steps.nth(0)).toContainText('npm install -g stub-cli');
    await expect(steps.nth(1)).toContainText('Sign in to Zeta');
    await expect(steps.nth(2)).toContainText('Check again');
    await expect(card.getByRole('link', { name: 'Documentation' })).toHaveAttribute('href', 'https://example.com/docs');
    await card.locator('[data-engine-open-setup="not-installed-e:install"]').click();
    await expect(card.getByRole('status').filter({ hasText: 'Could not open a terminal from here' })).toBeVisible();
    await expect(steps.nth(0)).toContainText('npm install -g stub-cli'); // the copyable command stays
    expect(calls.find((c) => c.method === 'engines.open_setup')?.params).toEqual({ engine_id: 'not-installed-e', step: 'install' });
  });

  test('needs sign-in: only the sign-in and check steps; a launched terminal is reported', async ({ page }) => {
    const calls = await stubProviders(page, FIVE_STATES, { 'engines.open_setup': () => ({ launched: true, reason: null }) });
    const dialog = await openAiSettings(page);
    const card = dialog.locator('[data-engine-card=needs-login-e]');
    await card.locator('[data-engine-primary=needs-login-e]').click(); // the primary action opens the row and the terminal
    const steps = card.getByRole('listitem');
    await expect(steps).toHaveCount(2);
    await expect(steps.nth(0)).toContainText('Sign in to Delta');
    await expect(steps.nth(1)).toContainText('Check again');
    await expect(card.getByText('Install Delta')).toHaveCount(0);
    await expect(card.getByRole('status').filter({ hasText: 'A terminal window was opened' })).toBeVisible();
    expect(calls.find((c) => c.method === 'engines.open_setup')?.params).toEqual({ engine_id: 'needs-login-e', step: 'login' });
  });

  test('Send test message shows latency and the reply, or a readable failure', async ({ page }) => {
    let ok = true;
    const calls = await stubProviders(page, FIVE_STATES, {
      'engines.test': (params) =>
        ok
          ? { engine_id: params.engine_id, model_id: 'm1', ok: true, duration_ms: 1200, reply_preview: 'OK from stub', error_code: null, error_message: null }
          : { engine_id: params.engine_id, model_id: 'm1', ok: false, duration_ms: 60000, reply_preview: null, error_code: 'timeout', error_message: 'timed out' },
    });
    const dialog = await openAiSettings(page);
    await expandRow(dialog, 'ready-e');
    expect(calls.some((c) => c.method === 'engines.test')).toBe(false);
    await dialog.locator('[data-engine-test=ready-e]').click();
    await expect(dialog.locator('[data-engine-test-result=ok]')).toContainText('1.2 s');
    await expect(dialog.locator('[data-engine-test-result=ok]')).toContainText('OK from stub');
    ok = false;
    await dialog.locator('[data-engine-test=ready-e]').click();
    const failed = dialog.locator('[data-engine-test-result=failed]');
    await expect(failed).toContainText('took too long');
    await expect(failed).not.toContainText('timeout');
    expect(calls.filter((c) => c.method === 'engines.test')).toHaveLength(2);
  });

  test('Refresh forces a re-check and the Composer follows without a reload', async ({ page }) => {
    let readyNow = false;
    const calls = await stubProviders(page, FIVE_STATES, {
      'engines.status': (params) => ({
        engines: [
          {
            engine_id: 'needs-login-e',
            title: 'Delta',
            vendor: 'Vendor D',
            state: params.force && readyNow ? 'ready' : 'needs_login',
            install_state: 'installed',
            auth_state: 'unknown',
            version: null,
            executable: null,
            detail: null,
          },
        ],
      }),
    });
    const dialog = await openAiSettings(page);
    await expect(dialog.locator('[data-engine-card=needs-login-e] [data-engine-state]')).toHaveText('Sign in required');
    readyNow = true;
    await dialog.locator('[data-providers-refresh]').click();
    await expect(dialog.locator('[data-engine-card=needs-login-e] [data-engine-state]')).toHaveText('Ready');
    expect(calls.filter((c) => c.method === 'engines.status' && c.params.force === true).length).toBeGreaterThan(0);
    await page.keyboard.press('Escape');
    await page.getByRole('button', { name: 'Add', exact: true }).click();
    await expect(page.locator('#rb-engine option[value=needs-login-e]')).toBeEnabled();
  });

  test('the default provider and model chosen in Settings show up in the Composer', async ({ page }) => {
    await stubProviders(page, FIVE_STATES);
    const dialog = await openAiSettings(page);
    await dialog.locator('#ai-default-engine').selectOption('ready-e');
    await expect(dialog.locator('#ai-default-model')).toBeEnabled();
    await dialog.locator('#ai-default-model').selectOption('m2');
    await page.keyboard.press('Escape');
    await page.getByRole('button', { name: 'Add', exact: true }).click();
    await expect(page.locator('#rb-engine')).toHaveValue('ready-e');
    await expect(page.locator('#rb-model')).toHaveValue('m2');
    // Friendly titles, and providers that are not usable are disabled with the reason.
    await expect(page.locator('#rb-engine option[value=ready-e]')).toHaveText('Alpha');
    await expect(page.locator('#rb-engine option[value=not-installed-e]')).toBeDisabled();
    await expect(page.locator('#rb-engine option[value=not-installed-e]')).toHaveText('Zeta (not installed)');
    await expect(page.locator('#rb-engine option[value=needs-login-e]')).toBeDisabled();
    await expect(page.locator('#rb-engine option[value=error-e]')).toBeDisabled();
    await expect(page.locator('#rb-engine option[value=unverified-e]')).toBeEnabled();
  });

  test('clicking a model marks it as the provider default and is remembered', async ({ page }) => {
    await stubProviders(page, FIVE_STATES);
    const dialog = await openAiSettings(page);
    await expandRow(dialog, 'unverified-e');
    const two = dialog.locator('[data-engine-card=unverified-e] [data-engine-model=m2]');
    await expect(two).toHaveAttribute('aria-pressed', 'false');
    await two.click();
    await expect(two).toHaveAttribute('aria-pressed', 'true');
    await expect(two).toContainText('free');
    await expect(two).toContainText('Default');
    const stored = await page.evaluate(() => localStorage.getItem('agentos.rusebot.preferredModels.v1'));
    expect(JSON.parse(stored ?? '{}')).toMatchObject({ 'unverified-e': 'm2' });
  });

  test('"Manage providers…" in the + menu opens Settings on the AI page', async ({ page }) => {
    await stubProviders(page, FIVE_STATES);
    await open(page);
    await page.getByRole('button', { name: 'Add', exact: true }).click();
    await page.getByRole('menuitem', { name: 'Manage providers…' }).click();
    const dialog = page.getByRole('dialog');
    await expect(dialog.locator('[data-settings-block=ai]')).toBeVisible();
    await expect(dialog.locator('[data-engine-card]')).toHaveCount(5);
  });

  test('the New agent dialog sends the chosen provider and model', async ({ page }) => {
    const calls = await stubProviders(page, FIVE_STATES, {
      'profiles.create': (params) => ({
        id: 'stub-profile',
        name: params.name,
        role_type: 'custom',
        description: params.description,
        instructions: params.instructions,
        default_model_id: params.default_model_id ?? null,
        engine: params.engine ?? null,
      }),
    });
    await open(page, '/?ui=rusebot', { asAgent: false });
    await openDrawerIfNarrow(page);
    await page.locator('[data-sidebar-new]').click();
    await page.locator('[data-new-agent]').click();
    await expect(page.locator('#na-engine')).toHaveValue('mock'); // starts from the current default
    await page.locator('#na-engine').selectOption('ready-e');
    await expect(page.locator('#na-model')).toBeEnabled();
    await page.locator('#na-model').selectOption('m2');
    await page.locator('#na-name').fill('Picker');
    await page.locator('#na-instructions').fill('Pick things.');
    await page.getByRole('button', { name: 'Create agent' }).click();
    await expect.poll(() => calls.find((c) => c.method === 'profiles.create')?.params).toMatchObject({ engine: 'ready-e', default_model_id: 'm2' });
  });
  // ---- review findings: provider selection, reconciliation, refresh, checks ----------------

  const READY_AND_UNVERIFIED: StubEngine[] = [
    { engine_id: 'ready-e', title: 'Alpha', vendor: 'Vendor A', state: 'ready' },
    { engine_id: 'unverified-e', title: 'Gamma', vendor: 'Vendor G', state: 'unverified' },
  ];

  test('R1: a slow model list for an earlier provider cannot bring that provider back', async ({ page }) => {
    await stubProviders(page, READY_AND_UNVERIFIED, {
      'engines.models': async (params) => {
        if (params.engine_id === 'ready-e') await sleep(1500);
        return { models: MODELS };
      },
    });
    const dialog = await openAiSettings(page);
    await dialog.locator('#ai-default-engine').selectOption('ready-e'); // slow
    await dialog.locator('#ai-default-engine').selectOption('unverified-e'); // fast, chosen last
    await expect(dialog.locator('#ai-default-model')).toBeEnabled();
    await page.waitForTimeout(2200); // the slow answer has landed by now
    await expect(dialog.locator('#ai-default-engine')).toHaveValue('unverified-e');
    await expect(dialog.locator('#ai-default-model')).toHaveValue('m1');
  });

  test('R1: the first default pick does not overwrite a provider the user picked while it was loading', async ({ page }) => {
    await stubProviders(page, READY_AND_UNVERIFIED, {
      'engines.capabilities': capsWithoutMock(READY_AND_UNVERIFIED),
      'engines.models': async (params) => {
        if (params.engine_id === 'ready-e') await sleep(2500);
        return { models: MODELS };
      },
    });
    const dialog = await openAiSettings(page, true);
    await dialog.locator('#ai-default-engine').selectOption('unverified-e'); // while the automatic pick still waits
    await page.waitForTimeout(3200);
    await expect(dialog.locator('#ai-default-engine')).toHaveValue('unverified-e');
  });

  test('R2: with no usable provider nothing is chosen and sending is disabled with a pointer to Settings', async ({ page }) => {
    const engines: StubEngine[] = [FIVE_STATES[0], FIVE_STATES[2]]; // not installed, needs sign-in
    await stubProviders(page, engines, { 'engines.capabilities': capsWithoutMock(engines) });
    await openBare(page);
    await page.getByRole('textbox', { name: 'Message Loqman' }).fill('hello');
    await expect(page.locator('[data-composer-no-provider]')).toContainText('Settings > AI');
    await expect(page.getByRole('button', { name: 'Send' })).toBeDisabled();
    await page.getByRole('button', { name: 'Add', exact: true }).click();
    await expect(page.locator('#rb-engine')).toHaveValue(''); // no engine is preselected
  });

  test('R2: a remembered choice that is no longer usable is cleared when the replacement has no models', async ({ page }) => {
    await page.addInitScript(() => localStorage.setItem('agentos.rusebot.engine.v1', JSON.stringify({ engine: 'needs-login-e', model: 'm1' })));
    const engines: StubEngine[] = [FIVE_STATES[2], FIVE_STATES[4]]; // needs sign-in, ready
    await stubProviders(page, engines, {
      'engines.capabilities': capsWithoutMock(engines),
      'engines.models': (params) => ({ models: params.engine_id === 'ready-e' ? [] : MODELS }),
    });
    await openBare(page);
    await page.getByRole('textbox', { name: 'Message Loqman' }).fill('hello');
    await expect(page.locator('[data-composer-no-provider]')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Send' })).toBeDisabled();
  });

  test('R3: Refresh reloads model lists that were loaded, including one that came back empty', async ({ page }) => {
    let models: Array<{ id: string; name: string }> = [];
    await stubProviders(page, FIVE_STATES, { 'engines.models': () => ({ models }) });
    const dialog = await openAiSettings(page);
    await expandRow(dialog, 'ready-e');
    await expect(dialog.getByText('This provider reported no models.')).toBeVisible();
    models = [{ id: 'm1', name: 'Account Model' }];
    await dialog.locator('[data-providers-refresh]').click();
    await expect(dialog.locator('[data-engine-model=m1]')).toContainText('Account Model');
  });

  test('R4: when engine discovery fails no real engine is treated as ready', async ({ page }) => {
    await stubProviders(page, READY_AND_UNVERIFIED, {
      'engines.capabilities': capsWithoutMock(READY_AND_UNVERIFIED),
      'engines.status': () => {
        throw new Error('discovery down');
      },
    });
    const dialog = await openAiSettings(page, true);
    await expect(dialog.locator('[data-engine-card=ready-e] [data-engine-state]')).toHaveText('Problem');
    await expect(dialog.locator('#ai-default-engine option[value=ready-e]')).toBeDisabled();
    await page.keyboard.press('Escape');
    await page.getByRole('textbox', { name: 'Message Loqman' }).fill('hello');
    await expect(page.locator('[data-composer-no-provider]')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Send' })).toBeDisabled();
  });

  test('R6: Check connection reports what it found and updates the shared status', async ({ page }) => {
    let reply: unknown = {};
    await stubProviders(page, FIVE_STATES, { 'engines.check': () => reply });
    const dialog = await openAiSettings(page);
    await expandRow(dialog, 'ready-e');
    const note = dialog.getByRole('status').filter({ hasText: /\S/ }).filter({ hasNotText: 'Checked at' });
    const installed = { install_state: 'installed', auth_state: 'authenticated' };
    const cases: Array<[unknown, RegExp]> = [
      [{ engine_id: 'ready-e', error_code: null, runtime: null, discovery: installed }, /did not report its health/],
      [{ engine_id: 'ready-e', error_code: null, runtime: { status: 'offline' }, discovery: installed }, /did not respond/],
      [{ engine_id: 'ready-e', error_code: null, runtime: { status: 'degraded' }, discovery: installed }, /reports a problem/],
    ];
    for (const [next, text] of cases) {
      reply = next;
      await dialog.locator('[data-engine-check=ready-e]').click();
      await expect(note.filter({ hasText: text })).toBeVisible();
      await expect(dialog.getByText('Connected.')).toHaveCount(0);
    }
    reply = { engine_id: 'ready-e', error_code: null, runtime: null, discovery: { install_state: 'not_installed', auth_state: 'unknown' } };
    await dialog.locator('[data-engine-check=ready-e]').click();
    await expect(dialog.locator('[data-engine-card=ready-e] [data-engine-state]')).toHaveText('Not installed');
    await expect(dialog.getByText(/no longer installed/)).toBeVisible();
    await page.keyboard.press('Escape');
    await page.getByRole('button', { name: 'Add', exact: true }).click();
    await expect(page.locator('#rb-engine option[value=ready-e]')).toBeDisabled();
  });

  test('R7: an unverified provider also offers the sign-in step, below its models and test', async ({ page }) => {
    const calls = await stubProviders(page, FIVE_STATES);
    const dialog = await openAiSettings(page);
    await expandRow(dialog, 'unverified-e');
    const card = dialog.locator('[data-engine-card=unverified-e]');
    await expect(card.locator('[data-engine-model=m1]')).toBeVisible();
    await expect(card.locator('[data-engine-test=unverified-e]')).toBeVisible();
    const signIn = card.locator('[data-engine-signin=unverified-e]');
    await expect(signIn).toContainText('Sign in to Gamma');
    await expect(signIn).toContainText('stub login');
    await signIn.locator('[data-engine-open-setup="unverified-e:login"]').click();
    expect(calls.find((c) => c.method === 'engines.open_setup')?.params).toEqual({ engine_id: 'unverified-e', step: 'login' });
    await expect(signIn.getByText('Install Gamma')).toHaveCount(0);
  });

  test('R8: a remembered model that is gone is dropped and never sent to the test', async ({ page }) => {
    await page.addInitScript(() => localStorage.setItem('agentos.rusebot.preferredModels.v1', JSON.stringify({ 'ready-e': 'gone-model' })));
    const calls = await stubProviders(page, FIVE_STATES);
    const dialog = await openAiSettings(page);
    await expandRow(dialog, 'ready-e');
    await expect(dialog.locator('[data-engine-model=m1]')).toHaveAttribute('aria-pressed', 'true');
    await dialog.locator('[data-engine-test=ready-e]').click();
    await expect(dialog.locator('[data-engine-test-result=ok]')).toBeVisible();
    expect(calls.find((c) => c.method === 'engines.test')?.params).toEqual({ engine_id: 'ready-e', model_id: 'm1' });
    const stored = await page.evaluate(() => localStorage.getItem('agentos.rusebot.preferredModels.v1'));
    expect(JSON.parse(stored ?? '{}')).not.toHaveProperty('ready-e');
  });
});

// ---------------------------------------------------------------------------------------
// Chat defects: timestamps, long conversations, delayed responses, long CEO prompts, agent profiles.
// ---------------------------------------------------------------------------------------
async function seedChat(page: Page, title: string, content = 'seed message') {
  const conv = await call<{ id: string }>(page, 'conversations.create', { title });
  await call(page, 'conversations.add_message', { conversation_id: conv.id, role: 'user', content });
  return conv.id;
}

async function openChatByTitle(page: Page, title: string) {
  await openDrawerIfNarrow(page);
  await page.locator('[data-sidebar-thread-row]').filter({ hasText: title }).click();
}

const reqOf = (route: Route) => JSON.parse(route.request().postData() ?? '{}') as { method: string; params?: Record<string, unknown> };
const okJson = (route: Route, result: unknown) => route.fulfill({ contentType: 'application/json', body: JSON.stringify({ result }) });

test.describe('chat: timestamps', () => {
  test.use({ timezoneId: 'Asia/Tokyo' });

  test('worker times are UTC: shown in local time with or without an offset', async ({ page }) => {
    const title = `tz-${uid()}`;
    const cid = await seedChat(page, title);
    await page.route('**/__agentos/call', async (route: Route) => {
      const req = reqOf(route);
      if (req.method === 'conversations.messages' && req.params?.conversation_id === cid) {
        const base = { conversation_id: cid, client_message_id: null };
        return okJson(route, {
          messages: [
            { ...base, seq: 1, role: 'user', content: 'older shape', created_at: '2026-03-10T23:30:00' },
            { ...base, seq: 2, role: 'assistant', content: 'newer shape', created_at: '2026-03-10T23:45:00+00:00' },
          ],
        });
      }
      return route.continue();
    });
    await open(page, '/?ui=rusebot', { asAgent: false });
    await openChatByTitle(page, title);
    // 23:30 UTC on Mar 10 is 08:30 on Mar 11 in Tokyo.
    await expect(page.getByText(/Mar 11\s+8:30\s?AM/)).toBeVisible();
    await expect(page.locator('[data-message-row][data-role=assistant]')).toContainText(/8:45\s?AM/);
  });
});

test.describe('chat: long conversations', () => {
  test('a conversation of more than 500 messages shows its newest messages', async ({ page }) => {
    const title = `long-${uid()}`;
    const cid = await seedChat(page, title);
    const total = 1100;
    let pages = 0;
    await page.route('**/__agentos/call', async (route: Route) => {
      const req = reqOf(route);
      if (req.method === 'conversations.messages' && req.params?.conversation_id === cid) {
        pages++;
        const after = Number(req.params.after ?? 0);
        const limit = Math.min(Number(req.params.limit ?? 200), 500);
        const messages = [];
        for (let seq = after + 1; seq <= total && messages.length < limit; seq++) {
          messages.push({
            seq,
            conversation_id: cid,
            client_message_id: null,
            role: seq % 2 ? 'user' : 'assistant',
            content: `msg-${seq}-end`,
            created_at: '2026-03-10T10:00:00+00:00',
          });
        }
        return okJson(route, { messages });
      }
      return route.continue();
    });
    await open(page, '/?ui=rusebot', { asAgent: false });
    await openChatByTitle(page, title);
    await expect(page.getByRole('log').getByText(`msg-${total}-end`, { exact: true })).toBeVisible();
    expect(pages).toBeGreaterThanOrEqual(3);
  });
});

test.describe('chat: responses that arrive late', () => {
  test('a delayed send response does not replace the transcript of the chat now open', async ({ page }) => {
    const other = `other-${uid()}`;
    const otherId = await seedChat(page, other, 'message that belongs to the other chat');
    let started = false;
    let heldCalls = 0;
    let release: () => void = () => undefined;
    const gate = new Promise<void>((resolve) => (release = resolve));
    await page.route('**/__agentos/call', async (route: Route) => {
      const req = reqOf(route);
      if (req.method === 'runs.start_turn') started = true;
      if (started && req.method === 'conversations.messages' && req.params?.conversation_id !== otherId) {
        heldCalls++;
        await gate;
      }
      return route.continue();
    });
    await open(page);
    const text = `late-${uid()}`;
    await send(page, text);
    await expect.poll(() => heldCalls).toBeGreaterThan(0);
    await openChatByTitle(page, other);
    const rows = page.locator('[data-message-row]');
    await expect(rows.filter({ hasText: 'message that belongs to the other chat' })).toHaveCount(1);
    release();
    await page.waitForTimeout(800);
    await expect(rows.filter({ hasText: text })).toHaveCount(0);
    await expect(rows.filter({ hasText: 'message that belongs to the other chat' })).toHaveCount(1);
  });
});

test.describe('chat: Loqman prompts', () => {
  test('a prompt over the 2,000 character limit is refused before anything is saved or cleared', async ({ page }) => {
    await open(page, '/?ui=rusebot', { asAgent: false });
    const methods: string[] = [];
    page.on('request', (r) => {
      if (r.url().endsWith('/__agentos/call')) methods.push(JSON.parse(r.postData() ?? '{}').method);
    });
    const text = `long-${uid()} ${'x'.repeat(2500)}`;
    const box = page.getByRole('textbox', { name: 'Message Loqman' });
    await box.fill(text);
    await box.press('Enter');
    await expect(page.getByRole('alert')).toContainText('2,000');
    await expect(box).toHaveValue(text);
    expect(methods.filter((m) => ['ceo.ask', 'conversations.add_message', 'conversations.create'].includes(m))).toEqual([]);
  });

  test('a failed ceo.ask keeps the draft and saves no message', async ({ page }) => {
    await open(page, '/?ui=rusebot', { asAgent: false });
    const saved: unknown[] = [];
    await page.route('**/__agentos/call', async (route: Route) => {
      const req = reqOf(route);
      if (req.method === 'ceo.ask')
        return route.fulfill({ status: 502, contentType: 'application/json', body: JSON.stringify({ status: 502, detail: 'CEO ask failed.' }) });
      if (req.method === 'conversations.add_message') saved.push(req.params);
      return route.continue();
    });
    const text = `fails-${uid()}`;
    const box = page.getByRole('textbox', { name: 'Message Loqman' });
    await box.fill(text);
    await box.press('Enter');
    await expect(page.getByRole('alert')).toContainText('CEO ask failed.');
    await expect(box).toHaveValue(text);
    expect(saved).toEqual([]);
  });
});

test.describe('chat: Loqman chats keep separate memory', () => {
  test('ceo.ask carries the id of the conversation that stores the exchange', async ({ page }) => {
    await open(page, '/?ui=rusebot', { asAgent: false });
    const asked: Array<Record<string, unknown>> = [];
    const created: string[] = [];
    await page.route('**/__agentos/call', async (route: Route) => {
      const req = JSON.parse(route.request().postData() ?? '{}');
      if (req.method === 'ceo.ask') asked.push(req.params);
      if (req.method === 'conversations.create') {
        const res = await route.fetch();
        const body = await res.json();
        created.push(body.result.id);
        return route.fulfill({ response: res, body: JSON.stringify(body) });
      }
      return route.continue();
    });
    const box = page.getByRole('textbox', { name: 'Message Loqman' });
    await box.fill(`memory-${uid()}`);
    await box.press('Enter');
    await expect.poll(() => asked.length, { timeout: 20000 }).toBe(1);
    expect(created).toHaveLength(1);
    expect(asked[0].conversation_id).toBe(created[0]);
  });
});

test.describe('chat: agents keep their profile', () => {
  test('an agent chat is bound to the agent and the first message is stored as typed', async ({ page }) => {
    await open(page);
    const text = `bound-${uid()}`;
    await send(page, text);
    await expect(page.locator('[data-chat-bubble]').filter({ hasText: MOCK_REPLY })).toBeVisible({ timeout: 20000 });
    const profiles = await call<{ profiles: Array<{ id: string; name: string }> }>(page, 'profiles.list');
    const agent = profiles.profiles.find((p) => p.name === agentName)!;
    const conversations = await call<{ conversations: Array<{ id: string; title: string; profile_id: string | null }> }>(page, 'conversations.list');
    const conv = conversations.conversations.find((c) => c.title.startsWith(text))!;
    expect(conv.profile_id).toBe(agent.id);
    const msgs = await call<{ messages: Array<{ role: string; content: string }> }>(page, 'conversations.messages', { conversation_id: conv.id });
    expect(msgs.messages[0]).toMatchObject({ role: 'user', content: text });
  });
});


test.describe('review fixes: durable Loqman exchange, unbound agent chats, paged results, mission polling', () => {
  test('a Loqman exchange is stored by ceo.ask, so the page adds no messages itself', async ({ page }) => {
    await open(page, '/?ui=rusebot', { asAgent: false });
    const added: unknown[] = [];
    const asked: Array<Record<string, unknown>> = [];
    await page.route('**/__agentos/call', async (route: Route) => {
      const req = reqOf(route);
      if (req.method === 'conversations.add_message') added.push(req.params);
      if (req.method === 'ceo.ask') asked.push(req.params ?? {});
      return route.continue();
    });
    const text = `durable-${uid()} hello`;
    const box = page.getByRole('textbox', { name: 'Message Loqman' });
    await box.fill(text);
    await box.press('Enter');
    await expect(page.locator('[data-chat-bubble]').filter({ hasText: /\S/ }).last()).toBeVisible({ timeout: 20000 });
    await expect.poll(() => asked.length).toBe(1);
    await expect(page.locator('[data-message-row]').filter({ hasText: text })).toHaveCount(1, { timeout: 20000 });
    await expect(page.locator('[data-message-row]')).toHaveCount(2, { timeout: 20000 });
    const stored = await call<{ messages: Array<{ role: string; content: string }> }>(page, 'conversations.messages', {
      conversation_id: asked[0].conversation_id as string,
    });
    expect(stored.messages.map((m) => m.role)).toEqual(['user', 'assistant']);
    expect(stored.messages[0].content).toBe(text);
    expect(added).toEqual([]);
  });

  async function legacyAgentChat(page: Page) {
    await open(page);
    const profiles = await call<{ profiles: Array<{ id: string; name: string }> }>(page, 'profiles.list');
    const agent = profiles.profiles.find((p) => p.name === agentName)!;
    const title = `old-${uid()}`;
    const conv = await call<{ id: string; profile_id: string | null }>(page, 'conversations.create', { title }); // no profile: made before chats were bound
    expect(conv.profile_id).toBeNull();
    await page.evaluate(([id, agentId]) => {
      const key = 'agentos.rusebot.convAgents.v1';
      const map = JSON.parse(localStorage.getItem(key) ?? '{}');
      map[id] = agentId;
      localStorage.setItem(key, JSON.stringify(map));
    }, [conv.id, agent.id]);
    await page.reload();
    await expect(page.locator('[data-rusebot-shell]')).toBeVisible();
    await openChatByTitle(page, title);
    await expect(page.getByRole('textbox', { name: `Message ${agentName}` })).toBeVisible();
    return { agent, conv };
  }

  test('an old agent chat without a profile is bound before its first message', async ({ page }) => {
    const turns: Array<Record<string, unknown>> = [];
    await page.route('**/__agentos/call', async (route: Route) => {
      const req = reqOf(route);
      if (req.method === 'runs.start_turn') turns.push(req.params ?? {});
      return route.continue();
    });
    const { agent, conv } = await legacyAgentChat(page);
    await send(page, `bind-me-${uid()}`);
    await expect.poll(() => turns.length, { timeout: 20000 }).toBe(1);
    const listed = await call<{ conversations: Array<{ id: string; profile_id: string | null }> }>(page, 'conversations.list');
    expect(listed.conversations.find((c) => c.id === conv.id)?.profile_id).toBe(agent.id);
    expect(String(turns[0].prompt)).not.toContain('[[contract]]');
  });

  test('if binding fails, the first message of an old agent chat carries the contract', async ({ page }) => {
    const turns: Array<Record<string, unknown>> = [];
    await page.route('**/__agentos/call', async (route: Route) => {
      const req = reqOf(route);
      if (req.method === 'conversations.bind_profile')
        return route.fulfill({ status: 500, contentType: 'application/json', body: JSON.stringify({ status: 500, detail: 'no' }) });
      if (req.method === 'runs.start_turn') turns.push(req.params ?? {});
      return route.continue();
    });
    await legacyAgentChat(page);
    await send(page, `fallback-${uid()}`);
    await expect.poll(() => turns.length, { timeout: 20000 }).toBe(1);
    expect(String(turns[0].prompt)).toContain('[[contract]]');
    expect(String(turns[0].prompt)).toContain('You are a test agent. Answer briefly.');
  });

  test('Results reads every page of deliverables', async ({ page }) => {
    const cursors: unknown[] = [];
    const draft = (n: number) => ({ id: `d${n}`, run_id: `r${n}`, filename: `f${n}.md`, mime: 'text/markdown', status: 'pending_review', content_sha256: 'a'.repeat(64), size_bytes: 3, downloadable: false, content: null, conversation_id: null });
    await page.route('**/__agentos/call', async (route: Route) => {
      const req = reqOf(route);
      if (req.method !== 'deliverables.list_all') return route.continue();
      cursors.push(req.params?.cursor ?? null);
      if (!req.params?.cursor) return okJson(route, { drafts: [draft(1), draft(2)], next_cursor: 'c1' });
      return okJson(route, { drafts: [draft(3)], next_cursor: null });
    });
    await open(page, '/?ui=rusebot', { asAgent: false });
    await openDrawerIfNarrow(page);
    await page.getByRole('button', { name: 'Results', exact: true }).click();
    await expect(page.locator('[data-view=results]')).toContainText('3 deliverables');
    expect(cursors).toEqual([null, 'c1']);
  });

  test('mission polling never overlaps requests', async ({ page }) => {
    let slow = false;
    let inflight = 0;
    let maxInflight = 0;
    await page.route('**/__agentos/call', async (route: Route) => {
      if (reqOf(route).method !== 'missions.list') return route.continue();
      const missions = [{ id: 'm-run', title: 'Slow mission', objective: 'x', status: 'running', final_summary: null, error_summary: null, tasks: [] }];
      if (slow) {
        inflight += 1;
        maxInflight = Math.max(maxInflight, inflight);
        await sleep(4500);
        inflight -= 1;
      }
      return okJson(route, { missions });
    });
    await open(page, '/?ui=rusebot', { asAgent: false });
    await openDrawerIfNarrow(page);
    await page.getByRole('button', { name: 'Team', exact: true }).click();
    await expect(page.locator('[data-mission=m-run]')).toBeVisible();
    slow = true;
    await page.waitForTimeout(11000);
    expect(maxInflight).toBe(1);
  });
});

test.describe('routing: the worker default decides, Loqman sends the chosen engine and model', () => {
  const READY_AND_UNVERIFIED: StubEngine[] = [
    { engine_id: 'ready-e', title: 'Alpha', vendor: 'Vendor A', state: 'ready' },
    { engine_id: 'unverified-e', title: 'Gamma', vendor: 'Vendor G', state: 'unverified' },
  ];
  const CEO_REPLY = { reply: 'ok from the stub', actions_proposed: [], agents_created: [], iterations: 1, fast: true };

  test('Loqman asks with the Composer engine and model and never lists free models', async ({ page }) => {
    const asked: Array<Record<string, unknown>> = [];
    const calls = await stubProviders(page, READY_AND_UNVERIFIED, {
      'engines.capabilities': capsWithoutMock(READY_AND_UNVERIFIED),
      'ceo.ask': (params) => {
        asked.push(params);
        return CEO_REPLY;
      },
    });
    await openBare(page);
    const box = page.getByRole('textbox', { name: 'Message Loqman' });
    await box.fill(`routing-${uid()}`);
    await expect(page.getByRole('button', { name: 'Send' })).toBeEnabled();
    await box.press('Enter');
    await expect.poll(() => asked.length, { timeout: 20000 }).toBe(1);
    expect(asked[0]).toMatchObject({ engine: 'ready-e', model_id: 'm1' });
    expect(calls.some((c) => c.method === 'models.free')).toBe(false);
  });

  test('a default chosen in Settings is saved in the worker and restored from it on the next start', async ({ page }) => {
    let saved: { engine: string | null; model_id: string | null } = { engine: null, model_id: null };
    const calls = await stubProviders(page, READY_AND_UNVERIFIED, {
      'engines.capabilities': capsWithoutMock(READY_AND_UNVERIFIED),
      'preferences.get': () => saved,
      'preferences.set_default': (params) => {
        saved = { engine: String(params.engine), model_id: String(params.model_id) };
        return saved;
      },
    });
    let dialog = await openAiSettings(page, true);
    await dialog.locator('#ai-default-engine').selectOption('unverified-e');
    await expect(dialog.locator('#ai-default-model')).toBeEnabled();
    await dialog.locator('#ai-default-model').selectOption('m2');
    await expect.poll(() => saved).toEqual({ engine: 'unverified-e', model_id: 'm2' });
    expect(calls.filter((c) => c.method === 'preferences.set_default').at(-1)?.params).toEqual({ engine: 'unverified-e', model_id: 'm2' });
    // The browser cache is gone: only the worker still knows the default.
    await page.evaluate(() => {
      localStorage.removeItem('agentos.rusebot.engine.v1');
      localStorage.removeItem('agentos.rusebot.preferredModels.v1');
    });
    await page.reload();
    await expect(page.locator('[data-rusebot-shell]')).toBeVisible();
    await openDrawerIfNarrow(page);
    await page.getByRole('button', { name: 'Settings', exact: true }).click();
    dialog = page.getByRole('dialog');
    await dialog.locator('[data-settings-page=ai]').click();
    await expect(dialog.locator('#ai-default-engine')).toHaveValue('unverified-e');
    await expect(dialog.locator('#ai-default-model')).toHaveValue('m2');
  });

  test('the worker default wins over a stale remembered choice, and a failed save is shown', async ({ page }) => {
    await page.addInitScript(() => localStorage.setItem('agentos.rusebot.engine.v1', JSON.stringify({ engine: 'ready-e', model: 'm1' })));
    await stubProviders(page, READY_AND_UNVERIFIED, {
      'engines.capabilities': capsWithoutMock(READY_AND_UNVERIFIED),
      'preferences.get': () => ({ engine: 'unverified-e', model_id: 'm2' }),
      'preferences.set_default': () => {
        throw new Error('disk is full');
      },
    });
    const dialog = await openAiSettings(page, true);
    await expect(dialog.locator('#ai-default-engine')).toHaveValue('unverified-e');
    await expect(dialog.locator('#ai-default-model')).toHaveValue('m2');
    await dialog.locator('#ai-default-model').selectOption('m1');
    await expect(dialog.getByRole('alert').filter({ hasText: 'Could not save the default' })).toBeVisible();
  });
});

// ---------------------------------------------------------------------------------------
// P3 (UI): startup race, backup, cancel mission, Loqman progress, empty CEO history, no Loqman flash.
// ---------------------------------------------------------------------------------------
test.describe('P3: startup and Settings', () => {
  const ENGINES: StubEngine[] = [
    { engine_id: 'ready-e', title: 'Alpha', vendor: 'Vendor A', state: 'ready' },
    { engine_id: 'unverified-e', title: 'Gamma', vendor: 'Vendor G', state: 'unverified' },
  ];

  test('a model chosen while the worker default is still loading is not overwritten by it', async ({ page }) => {
    let answered = false;
    await stubProviders(page, ENGINES, {
      'engines.capabilities': capsWithoutMock(ENGINES),
      'preferences.get': async () => {
        await sleep(2500);
        answered = true;
        return { engine: 'ready-e', model_id: 'm1' };
      },
    });
    await page.goto('/?ui=rusebot');
    await expect(page.locator('[data-rusebot-shell]')).toBeVisible();
    // The user's choice arrives while preferences.get is pending (the store module is the one the app uses).
    await page.evaluate(async () => {
      const store = await import(/* @vite-ignore */ '/src/rusebot/state/useProviders.ts');
      store.setProviderChoice({ engine: 'unverified-e', model: 'm2' });
    });
    await expect.poll(() => answered, { timeout: 15000 }).toBe(true);
    await page.waitForTimeout(800); // the late worker answer has been handled
    await expect(page.locator('[data-chathead-pill] [title="unverified-e · m2"]')).toBeVisible();
    await expect(page.locator('[data-chathead-pill] [title="ready-e · m1"]')).toHaveCount(0);
  });

  const BRIDGE_INIT = (mode: string) => {
    const w = window as unknown as { agentos: Record<string, unknown> };
    let release: () => void = () => undefined;
    (window as unknown as { __releaseBackup: () => void }).__releaseBackup = () => release();
    w.agentos = {
      call: async (method: string, params: Record<string, unknown>) => {
        const res = await fetch('/__agentos/call', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ method, params }) });
        const body = await res.json();
        if (!res.ok) throw new Error(JSON.stringify({ status: res.status, detail: body.detail }));
        return body.result;
      },
      openStream: async () => {
        throw new Error('no streams in this test');
      },
      saveBackup: async () => {
        await new Promise<void>((r) => {
          release = r;
        });
        if (mode === 'cancel') return { saved: false, cancelled: true };
        if (mode === 'error') throw new Error(JSON.stringify({ status: null, detail: 'The disk is full.' }));
        return { saved: true, path: 'C:\\Backups\\agentos-backup.zip', size: 1572864 };
      },
    };
  };
  const releaseBackup = (page: Page) => page.evaluate(() => (window as unknown as { __releaseBackup: () => void }).__releaseBackup());

  async function openGeneral(page: Page) {
    await openBare(page);
    await openDrawerIfNarrow(page);
    await page.getByRole('button', { name: 'Settings', exact: true }).click();
    const dialog = page.getByRole('dialog');
    await expect(dialog.locator('[data-settings-block=general]')).toBeVisible();
    return dialog;
  }

  test('without the desktop bridge the backup button is disabled with a note', async ({ page }) => {
    const dialog = await openGeneral(page);
    await expect(dialog.getByRole('button', { name: 'Back up data…', exact: true })).toBeDisabled();
    await expect(dialog.getByText('Backups are available in the desktop app.')).toBeVisible();
  });

  test('desktop: backup shows progress, then where it was saved and how big it is', async ({ page }) => {
    await page.addInitScript(BRIDGE_INIT, 'saved');
    const dialog = await openGeneral(page);
    const button = dialog.getByRole('button', { name: 'Back up data…', exact: true });
    await expect(button).toBeEnabled();
    await expect(dialog.getByText('Backups are available in the desktop app.')).toHaveCount(0);
    await button.click();
    await expect(dialog.getByRole('status').filter({ hasText: 'Saving the backup' })).toBeVisible();
    await expect(dialog.locator('[data-backup-button]')).toBeDisabled();
    await releaseBackup(page);
    await expect(dialog.getByRole('status').filter({ hasText: 'Saved to C:\\Backups\\agentos-backup.zip (1.5 MB)' })).toBeVisible();
    await expect(dialog.locator('[data-backup-button]')).toBeEnabled();
  });

  test('desktop: cancelling the save dialog shows nothing', async ({ page }) => {
    await page.addInitScript(BRIDGE_INIT, 'cancel');
    const dialog = await openGeneral(page);
    await dialog.locator('[data-backup-button]').click();
    await expect(dialog.getByRole('status').filter({ hasText: 'Saving the backup' })).toBeVisible();
    await releaseBackup(page);
    await expect(dialog.locator('[data-backup-button]')).toBeEnabled();
    await expect(dialog.getByRole('status')).toHaveCount(0);
    await expect(dialog.getByRole('alert')).toHaveCount(0);
  });

  test('desktop: a failed backup says why', async ({ page }) => {
    await page.addInitScript(BRIDGE_INIT, 'error');
    const dialog = await openGeneral(page);
    await dialog.locator('[data-backup-button]').click();
    await releaseBackup(page);
    await expect(dialog.getByRole('alert')).toContainText('Could not back up. The disk is full.');
    await expect(dialog.locator('[data-backup-button]')).toBeEnabled();
  });
});

test.describe('P3: missions and Loqman', () => {
  const mission = (id: string, status: string) => ({ id, title: `Mission ${id}`, objective: 'x', status, final_summary: null, error_summary: null, tasks: [] });

  test('an active mission can be cancelled after a confirm step; errors are readable', async ({ page }) => {
    let status = 'running';
    let failing = true;
    const cancelled: Array<Record<string, unknown>> = [];
    await page.route('**/__agentos/call', async (route: Route) => {
      const req = reqOf(route);
      if (req.method === 'missions.list') return okJson(route, { missions: [mission('m-run', status), mission('m-done', 'succeeded')] });
      if (req.method === 'missions.cancel') {
        cancelled.push(req.params ?? {});
        if (failing) return route.fulfill({ status: 500, contentType: 'application/json', body: JSON.stringify({ status: 500, detail: 'The engine is busy.' }) });
        status = 'cancelled';
        return okJson(route, mission('m-run', 'cancelled'));
      }
      return route.continue();
    });
    await openBare(page);
    await openDrawerIfNarrow(page);
    await page.getByRole('button', { name: 'Team', exact: true }).click();
    const row = page.locator('[data-mission=m-run]');
    await expect(row).toBeVisible();
    await expect(page.locator('[data-mission=m-done] [data-mission-cancel]')).toHaveCount(0);
    await row.locator('[data-mission-cancel]').click();
    await expect(row.locator('[data-mission-confirm]')).toBeVisible();
    expect(cancelled).toEqual([]); // nothing is sent before the confirm
    await row.getByRole('button', { name: 'Keep running', exact: true }).click();
    await expect(row.locator('[data-mission-confirm]')).toHaveCount(0);
    await row.locator('[data-mission-cancel]').click();
    await row.getByRole('button', { name: 'Yes, cancel mission', exact: true }).click();
    await expect(row.getByRole('alert')).toContainText('Could not cancel. The engine is busy.');
    failing = false;
    await row.getByRole('button', { name: 'Yes, cancel mission', exact: true }).click();
    await expect(row).toContainText('cancelled');
    await expect(row.locator('[data-mission-cancel]')).toHaveCount(0);
    expect(cancelled).toEqual([{ mission_id: 'm-run' }, { mission_id: 'm-run' }]);
  });

  test('while Loqman answers, the current step is shown; polling never overlaps and stops with the answer', async ({ page }) => {
    let polls = 0;
    let inflight = 0;
    let maxInflight = 0;
    const asked: Array<Record<string, unknown>> = [];
    await page.route('**/__agentos/call', async (route: Route) => {
      const req = reqOf(route);
      if (req.method === 'ceo.ask') {
        asked.push(req.params ?? {});
        await sleep(4200);
        return okJson(route, { reply: 'done', actions_proposed: [], agents_created: [], iterations: 2, fast: false });
      }
      if (req.method === 'ceo.ask_progress') {
        polls += 1;
        inflight += 1;
        maxInflight = Math.max(maxInflight, inflight);
        await sleep(300);
        inflight -= 1;
        const first = polls === 1;
        return okJson(route, { stage: first ? 'thinking' : 'tool', detail: first ? '' : 'web_search', iteration: first ? 1 : 2, done: false });
      }
      return route.continue();
    });
    await openBare(page);
    const box = page.getByRole('textbox', { name: 'Message Loqman' });
    await box.fill(`progress-${uid()}`);
    await box.press('Enter');
    const working = page.locator('[data-working]');
    await expect(working).toContainText('Working');
    await expect(working).toContainText('Thinking', { timeout: 8000 });
    await expect(working).toContainText('Using web_search (step 2)', { timeout: 8000 });
    expect(String(asked[0].request_id)).toMatch(/^ceo-/);
    await expect(working).toHaveCount(0, { timeout: 10000 });
    const after = polls;
    await page.waitForTimeout(3500);
    expect(polls).toBe(after);
    expect(maxInflight).toBe(1);
  });

  const HISTORY = { id: 'legacy-ceo-history', title: 'CEO history', workspace_id: null, profile_id: null, created_at: '2026-11-01T10:00:00+00:00', updated_at: '2026-11-01T10:00:00+00:00' };
  async function stubHistory(page: Page, messages: unknown[]) {
    await page.route('**/__agentos/call', async (route: Route) => {
      const req = reqOf(route);
      if (req.method === 'conversations.list') {
        const body = (await (await route.fetch()).json()) as { result: { conversations: Array<{ id: string }> } };
        body.result.conversations = [...body.result.conversations.filter((c) => c.id !== HISTORY.id), HISTORY];
        return okJson(route, body.result);
      }
      if (req.method === 'conversations.messages' && req.params?.conversation_id === HISTORY.id) return okJson(route, { messages });
      return route.continue();
    });
  }

  test('the CEO history conversation is hidden while it has no messages', async ({ page }) => {
    await stubHistory(page, []);
    await openBare(page);
    await openDrawerIfNarrow(page);
    await expect(page.locator('[data-sidebar-thread-row]').filter({ hasText: 'CEO history' })).toHaveCount(0);
  });

  test('the CEO history conversation shows once it has messages', async ({ page }) => {
    const msg = { seq: 1, conversation_id: HISTORY.id, role: 'user', content: 'old question', client_message_id: 'ceo:g1', created_at: '2026-11-01T10:00:00+00:00' };
    await stubHistory(page, [msg]);
    await openBare(page);
    await openDrawerIfNarrow(page);
    await expect(page.locator('[data-sidebar-thread-row]').filter({ hasText: 'CEO history' })).toHaveCount(1);
  });

  test('the remembered chat is not shown as Loqman while the app is still loading', async ({ page }) => {
    await open(page); // creates an agent (agentName)
    const profiles = await call<{ profiles: Array<{ id: string; name: string }> }>(page, 'profiles.list');
    const agent = profiles.profiles.find((p) => p.name === agentName)!;
    const conv = await call<{ id: string }>(page, 'conversations.create', { title: `flash-${uid()}`, profile_id: agent.id });
    await page.evaluate(
      ([id, agentId]) => {
        localStorage.setItem('agentos.rusebot.selected.v1', JSON.stringify(id));
        localStorage.setItem('agentos.rusebot.convAgents.v1', JSON.stringify({ [id]: agentId }));
      },
      [conv.id, agent.id]
    );
    await page.route('**/__agentos/call', async (route: Route) => {
      if (reqOf(route).method === 'profiles.list') await sleep(1500);
      return route.continue();
    });
    await page.reload();
    await expect(page.locator('[data-rusebot-shell]')).toBeVisible();
    for (let i = 0; i < 6; i++) {
      expect(await page.getByRole('textbox', { name: 'Message Loqman' }).count()).toBe(0); // no retry: a single look at each moment
      await page.waitForTimeout(150);
    }
    await expect(page.getByRole('textbox', { name: `Message ${agentName}` })).toBeVisible({ timeout: 15000 });
    await expect(page.getByRole('textbox', { name: 'Message Loqman' })).toHaveCount(0);
  });
});

// ---------------------------------------------------------------------------------------------
// Streaming chat: the worker's run stream is replaced by a fake EventSource the test drives, and
// runs.* is stubbed, so no model is ever called. Everything else talks to the real mock worker.
// ---------------------------------------------------------------------------------------------
test.describe('chat: streaming replies', () => {
  const RUN_ID = 'run-live-1';

  async function fakeStream(page: Page) {
    await page.addInitScript(() => {
      type Listener = (ev: { data: string }) => void;
      type Fake = { listeners: Record<string, Listener[]>; url: string; closed: boolean };
      const w = window as unknown as { __streams: Fake[]; EventSource: unknown };
      w.__streams = [];
      const Real = window.EventSource;
      w.EventSource = function (url: string) {
        if (!/__agentos\/stream/.test(String(url))) return new Real(url);
        const fake: Fake & Record<string, unknown> = {
          url: String(url),
          closed: false,
          listeners: {},
          addEventListener(name: string, fn: Listener) {
            (fake.listeners[name] ??= []).push(fn);
          },
          close() {
            fake.closed = true;
          },
        };
        w.__streams.push(fake);
        return fake;
      } as unknown;
    });
  }

  const emit = (page: Page, event: string, data: unknown, id = 1) =>
    page.evaluate(
      ([event, data, id]) => {
        const w = window as unknown as { __streams: Array<{ listeners: Record<string, Array<(ev: { data: string }) => void>>; closed: boolean }> };
        const msg = { type: 'stream', stream_id: 's1', event, id, data };
        for (const s of w.__streams) if (!s.closed) for (const fn of s.listeners.stream ?? []) fn({ data: JSON.stringify(msg) });
      },
      [event, data, id] as const
    );

  const driver = (type: string, itemId: string | null, data: Record<string, unknown>) => ({
    version: 1, type, item_id: itemId, run_id: RUN_ID, turn_id: 't1', seq: 1, engine: 'claude-code', data,
  });
  const delta = (page: Page, itemId: string, text: string) =>
    emit(page, 'content.delta', driver('content.delta', itemId, { stream_kind: 'assistant_text', delta: text }));

  /** Starts a chat whose run is a stub; the stored assistant message appears once the run ends as succeeded. */
  async function startRun(page: Page, text: string) {
    await fakeStream(page);
    const state = { cid: '', stored: false, cancelled: false, status: 'running', answer: 'The stored final answer.' };
    const snap = () => ({
      id: RUN_ID, conversation_id: state.cid, engine: 'mock', model_id: 'mock', status: state.status, user_message_seq: 1,
      assistant_message_seq: state.stored ? 2 : null, assistant_content: null, error: null, tool_mode: 'none',
      created_at: '2026-11-15T00:00:00', started_at: null, finished_at: null,
    });
    await page.route('**/__agentos/call', async (route: Route) => {
      const { method, params } = reqOf(route);
      if (method === 'runs.start_turn') {
        state.cid = String(params?.conversation_id);
        return okJson(route, snap());
      }
      if (method === 'runs.get') return okJson(route, snap());
      if (method === 'runs.list') return okJson(route, { runs: state.cid ? [snap()] : [] });
      if (method === 'runs.cancel') {
        state.cancelled = true;
        state.status = 'cancelled';
        return okJson(route, snap());
      }
      if (method === 'conversations.messages' && state.cid && params?.conversation_id === state.cid) {
        const base = { conversation_id: state.cid, client_message_id: null, created_at: '2026-11-15T00:00:00+00:00' };
        const after = Number(params?.after ?? 0);
        const all = [
          { ...base, seq: 1, role: 'user', content: text },
          ...(state.stored ? [{ ...base, seq: 2, role: 'assistant', content: state.answer }] : []),
        ];
        return okJson(route, { messages: all.filter((m) => m.seq > after) });
      }
      return route.fallback();
    });
    await open(page);
    await send(page, text);
    await expect(page.locator('[data-working]')).toBeVisible();
    await expect.poll(() => page.evaluate(() => (window as unknown as { __streams: unknown[] }).__streams.length)).toBeGreaterThan(0);
    await emit(page, 'running', {}, 1);
    return {
      state,
      finish: async (status: 'succeeded' | 'cancelled' | 'failed', id = 99) => {
        state.status = status;
        if (status === 'succeeded') state.stored = true;
        await emit(page, status, status === 'failed' ? { error: 'boom' } : {}, id);
      },
    };
  }

  const live = (page: Page) => page.locator('[data-live-bubble]');

  test('the chat run stream asks for normalised events', async ({ page }) => {
    await startRun(page, `fmt-${uid()}`);
    const urls = await page.evaluate(() => (window as unknown as { __streams: Array<{ url: string }> }).__streams.map((s) => s.url));
    expect(urls.some((u) => /topic=run/.test(u) && /format=normalised-v1/.test(u))).toBe(true);
  });

  test('live text grows in order and the stored message replaces it after the terminal', async ({ page }) => {
    const run = await startRun(page, `grow-${uid()}`);
    await delta(page, 'a1', 'Hel');
    await expect(live(page)).toContainText('Hel');
    await delta(page, 'a1', 'lo wor');
    await expect(live(page)).toContainText('Hello wor');
    await delta(page, 'a1', 'ld');
    await expect(live(page).locator('[data-live-item]')).toHaveText('Hello world');
    await expect(page.locator('[data-working]')).toHaveCount(0);
    await expect(page.getByRole('button', { name: 'Stop', exact: true })).toBeVisible();
    await emit(page, 'item.completed', driver('item.completed', 'a1', { item_type: 'assistant', phase: 'final', text: 'Hello world' }));
    await run.finish('succeeded');
    await expect(live(page)).toHaveCount(0);
    await expect(page.locator('[data-chat-bubble]').filter({ hasText: 'The stored final answer.' })).toHaveCount(1);
    await expect(page.getByRole('button', { name: 'Send', exact: true })).toBeVisible();
    await expect(page.getByRole('alert')).toHaveCount(0);
  });

  test('a tool step shows while it runs and as done after; commentary is dimmed', async ({ page }) => {
    const run = await startRun(page, `tool-${uid()}`);
    await delta(page, 'a1', 'Let me look. ');
    await emit(page, 'item.completed', driver('item.completed', 'a1', { item_type: 'assistant', phase: 'commentary', text: 'Let me look. ' }));
    await emit(page, 'item.started', driver('item.started', 'tool1', { item_type: 'tool', title: 'Read', status: 'running' }));
    const chip = live(page).locator('[data-live-tool]');
    await expect(chip).toContainText('Read');
    await expect(chip).toHaveAttribute('data-status', 'running');
    await expect(live(page).locator('[data-live-item][data-phase=commentary]')).toContainText('Let me look.');
    await emit(page, 'item.completed', driver('item.completed', 'tool1', { item_type: 'tool', status: 'completed', ok: true }));
    await expect(chip).toHaveAttribute('data-status', 'done');
    await delta(page, 'a2', 'Done.');
    await expect(live(page).locator('[data-live-item]').nth(1)).toHaveText('Done.');
    await run.finish('succeeded');
    await expect(live(page)).toHaveCount(0);
  });

  test('Stop freezes the text at once and shows it as stopped', async ({ page }) => {
    const run = await startRun(page, `stop-${uid()}`);
    await delta(page, 'a1', 'Partial answer');
    await expect(live(page)).toContainText('Partial answer');
    await page.getByRole('button', { name: 'Stop', exact: true }).click();
    await expect(live(page)).toHaveAttribute('data-stopped', 'true');
    await delta(page, 'a1', ' that must not appear');
    await page.waitForTimeout(250);
    await expect(live(page).locator('[data-live-item]')).toHaveText('Partial answer');
    await run.finish('cancelled');
    await expect(page.getByRole('button', { name: 'Send', exact: true })).toBeVisible();
    await expect(live(page)).toContainText('Partial answer');
    await expect(live(page).locator('[data-live-stopped]')).toHaveText('Stopped');
    await expect(page.getByRole('alert')).toHaveCount(0);
    expect(run.state.cancelled).toBe(true);
  });

  test('a run without deltas looks as before and ignores the single completed item', async ({ page }) => {
    const run = await startRun(page, `plain-${uid()}`);
    await emit(page, 'item.completed', driver('item.completed', 'legacy-1', { item_type: 'assistant', phase: 'final', text: 'whole reply' }));
    await page.waitForTimeout(200);
    await expect(live(page)).toHaveCount(0);
    await expect(page.locator('[data-working]')).toBeVisible();
    await run.finish('succeeded');
    await expect(page.locator('[data-working]')).toHaveCount(0);
    await expect(page.locator('[data-chat-bubble]').filter({ hasText: 'The stored final answer.' })).toHaveCount(1);
    await expect(live(page)).toHaveCount(0);
  });

  test('unknown and non-lifecycle events never end the run', async ({ page }) => {
    const run = await startRun(page, `unk-${uid()}`);
    for (const type of ['turn.completed', 'usage', 'runtime.notice', 'runtime.error', 'session.started', 'future.event', 'done', 'finished']) {
      await emit(page, type, driver(type, null, { anything: 1 }));
    }
    await emit(page, 'weird', 'not an object');
    await page.waitForTimeout(250);
    await expect(page.locator('[data-working]')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Stop', exact: true })).toBeVisible();
    await delta(page, 'a1', 'still streaming');
    await expect(live(page)).toContainText('still streaming');
    await run.finish('succeeded');
    await expect(page.getByRole('button', { name: 'Send', exact: true })).toBeVisible();
    await expect(live(page)).toHaveCount(0);
  });

  test('hundreds of deltas are coalesced into few renders', async ({ page }) => {
    const run = await startRun(page, `burst-${uid()}`);
    const mutations = await page.evaluate(async () => {
      const w = window as unknown as { __streams: Array<{ listeners: Record<string, Array<(ev: { data: string }) => void>> }> };
      let count = 0;
      const obs = new MutationObserver((list) => {
        count += list.length;
      });
      obs.observe(document.body, { childList: true, subtree: true, characterData: true });
      for (let i = 0; i < 400; i++) {
        const msg = {
          type: 'stream', stream_id: 's1', event: 'content.delta', id: 2 + i,
          data: { type: 'content.delta', item_id: 'a1', data: { stream_kind: 'assistant_text', delta: 'x' } },
        };
        for (const s of w.__streams) for (const fn of s.listeners.stream ?? []) fn({ data: JSON.stringify(msg) });
      }
      await new Promise((r) => setTimeout(r, 400));
      obs.disconnect();
      return count;
    });
    await expect(live(page).locator('[data-live-item]')).toHaveText('x'.repeat(400));
    expect(mutations).toBeLessThan(400);
    await run.finish('succeeded');
  });
});
