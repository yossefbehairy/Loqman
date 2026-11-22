import { expect, test, type Page, type Route } from '@playwright/test';

type Chat = { id: string; title: string };
type Run = { id: string; status: string };
type Rpc = { method: string; params: Record<string, unknown> };

function gate() {
  let release!: () => void;
  const wait = new Promise<void>(resolve => { release = resolve; });
  return { wait, release };
}

async function rpc<T>(page: Page, method: string, params: object) {
  const response = await page.request.post('/__agentos/call', { data: { method, params } });
  expect(response.ok(), method).toBe(true);
  return (await response.json() as { result: T }).result;
}

const cards = (page: Page) => page.getByRole('log').locator('[data-approval-card=tool]');
const button = (page: Page, name: string) => cards(page).getByRole('button', { name, exact: true });

async function selectChat(page: Page, chat: Chat) {
  await page.locator('[data-sidebar-thread-row="' + chat.id + '"]').click();
  await expect(page.locator('[data-chathead-conversation]')).toHaveText(chat.title);
}

async function setup(page: Page, holdA: boolean) {
  const agents = await Promise.all(['A', 'B'].map(letter => rpc<{ id: string }>(page, 'profiles.create', {
    name: 'UX-08 Agent ' + letter, role_type: 'custom', instructions: 'Reply briefly.',
  })));
  const [a, b] = await Promise.all(agents.map((agent, i) => rpc<Chat>(page, 'conversations.create', {
    title: 'UX-08 chat ' + (i ? 'B' : 'A'), profile_id: agent.id,
  })));
  const runs: Run[] = [];
  const decided: Rpc[] = [];
  const captured = gate();
  const release = gate();
  const delivered = gate();
  const pending = new Map<string, { id: string; run_id: string; action_summary: string; scope: string; status: string; expires_at: null }>();
  const ok = (route: Route, result: unknown) => route.fulfill({ json: { result } });
  const dispose = async () => {
    release.release();
    await Promise.all(runs.map(async run => {
      const actual = await rpc<Run>(page, 'runs.get', { run_id: run.id });
      if (['running', 'queued'].includes(actual.status)) await rpc(page, 'runs.cancel', { run_id: run.id });
    }));
  };
  try {
    for (const [i, chat] of [a, b].entries()) {
      const run = await rpc<Run>(page, 'runs.start_turn', {
        conversation_id: chat.id, engine: 'mock', model_id: 'deepseek-v4.1-flash-free',
        prompt: 'Wait for UX-08 permission [mock:delay=30000]',
      });
      runs.push(run);
      pending.set(run.id, {
        id: 'ux08-approval-' + i, run_id: run.id, action_summary: 'Permission for chat ' + (i ? 'B' : 'A'),
        scope: 'workspace_write', status: 'pending', expires_at: null,
      });
    }
    await page.route('**/__agentos/call', async route => {
      const call = route.request().postDataJSON() as Rpc;
      if (call.method === 'approvals.list') return ok(route, { approvals: [...pending.values()] });
      if (call.method === 'approvals.decide') {
        decided.push(call);
        const runId = String(call.params.run_id);
        const item = pending.get(runId);
        if (!item || item.id !== call.params.approval_id) return route.fulfill({ status: 409, json: { detail: 'Already decided or wrong run' } });
        pending.delete(runId);
        if (holdA && runId === runs[0].id) {
          captured.release();
          await release.wait;
        }
        await ok(route, { ...item, status: call.params.decision === 'approve' ? 'approved' : 'denied' });
        if (runId === runs[0].id) delivered.release();
        return;
      }
      return route.continue();
    });
    await page.addInitScript(id => localStorage.setItem('agentos.rusebot.selected.v1', JSON.stringify(id)), a.id);
    await page.goto('/');
    await expect(cards(page)).toHaveText(/UX-08 Agent A wants permission.*Permission for chat A/s);
    return { a, b, runs, decided, captured, release, delivered, dispose };
  } catch (error) {
    await dispose();
    throw error;
  }
}

for (const holdA of [false, true]) {
  test('UX-08: ' + (holdA ? 'late approval acknowledgement stays in its own chat' : 'decided approval stays in its own chat and survives return'), async ({ page }) => {
    const f = await setup(page, holdA);
    try {
      await button(page, 'Approve').click();
      if (holdA) await f.captured.wait;
      else await expect(cards(page)).toContainText('Approved');
      await selectChat(page, f.b);
      if (holdA) {
        await expect(cards(page)).toHaveCount(1);
        await expect(button(page, 'Deny')).toBeEnabled();
        await selectChat(page, f.a);
        await expect(cards(page)).toContainText('Permission for chat A');
        await expect(button(page, 'Approve')).toBeDisabled();
        await selectChat(page, f.b);
        f.release.release();
        await f.delivered.wait;
      }
      await expect(cards(page)).toHaveCount(1);
      await expect(cards(page)).toContainText('Permission for chat B');
      await expect(cards(page)).not.toContainText('Permission for chat A');
      await expect(button(page, 'Deny')).toBeEnabled();
      expect(f.decided).toHaveLength(1);

      await button(page, 'Deny').click();
      await expect(cards(page)).toContainText('Denied');
      await selectChat(page, f.a);
      await expect(cards(page)).toHaveCount(1);
      await expect(cards(page)).toContainText('Permission for chat A');
      await expect(cards(page)).toContainText('Approved');
      await expect(button(page, 'Approve')).toHaveCount(0);
      await expect(button(page, 'Deny')).toHaveCount(0);
      await selectChat(page, f.b);
      await expect(cards(page)).toHaveCount(1);
      await expect(cards(page)).toContainText('Denied');
      await expect(button(page, 'Approve')).toHaveCount(0);
      expect(f.decided).toEqual([
        { method: 'approvals.decide', params: { run_id: f.runs[0].id, approval_id: 'ux08-approval-0', decision: 'approve' } },
        { method: 'approvals.decide', params: { run_id: f.runs[1].id, approval_id: 'ux08-approval-1', decision: 'deny' } },
      ]);
    } finally {
      await f.dispose();
    }
  });
}
