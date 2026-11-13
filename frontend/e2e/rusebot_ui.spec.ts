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

  test('settings: AI providers page groups ready and needs-setup engines and checks a connection (engine calls stubbed)', async ({ page }) => {
    await page.route('**/__agentos/call', async (route: Route) => {
      const req = JSON.parse(route.request().postData() ?? '{}');
      const reply = (result: unknown) => route.fulfill({ contentType: 'application/json', body: JSON.stringify({ result }) });
      if (req.method === 'engines.status') return reply({ engines: [{ engine_id: 'codex', name: 'Codex', install_state: 'installed' }, { engine_id: 'opencode', name: 'OpenCode', install_state: 'missing' }] });
      if (req.method === 'engines.check') return reply({ engine_id: req.params.engine_id, error_code: null, runtime: {} });
      if (req.method === 'engines.models') return reply({ models: [{ id: 'm1', name: 'Fixture Model One' }] });
      return route.continue();
    });
    await open(page);
    await openDrawerIfNarrow(page);
    await page.getByRole('button', { name: 'Settings', exact: true }).click();
    const dialog = page.getByRole('dialog');
    await dialog.locator('[data-settings-page=ai]').click();
    await expect(dialog.getByRole('heading', { name: 'Model providers and accounts' })).toBeVisible();
    await expect(dialog.locator('[data-engine-card=codex]')).toContainText('Ready');
    await expect(dialog.locator('[data-engine-card=opencode]')).toContainText('Needs setup');
    await dialog.locator('[data-engine-card=codex] summary').click();
    await dialog.locator('[data-engine-check=codex]').click();
    await expect(dialog.getByRole('status').filter({ hasText: 'Connected' })).toBeVisible();
    await expect(dialog.getByText('Fixture Model One')).toBeVisible();
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
