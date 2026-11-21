import { expect, test, type Page } from '@playwright/test';

const assignment = 'Draft the first project checklist.';
const replacement = 'Review the next project milestone.';
const rejected = 'The mock worker could not accept the assignment.';

function deferred() {
  let release!: () => void;
  const promise = new Promise<void>(resolve => { release = resolve; });
  return { promise, release };
}

async function openDelegate(page: Page) {
  const response = await page.request.post('/__agentos/call', {
    data: {
      method: 'profiles.create',
      params: {
        name: 'Delegation draft specialist',
        role_type: 'custom',
        instructions: 'Write project checklists.',
      },
    },
  });
  expect(response.ok()).toBe(true);
  const { result: profile } = await response.json() as { result: { id: string; name: string } };
  await page.goto('/?ui=rusebot');
  await page.getByRole('button', { name: 'Team', exact: true }).click();
  await page.locator(`[data-bot-id="${profile.id}"]`).click();
  const panel = page.locator(`[data-agent-panel="${profile.id}"]`);
  return {
    profile,
    panel,
    task: panel.getByRole('textbox', { name: 'Delegate a task' }),
    delegate: panel.getByRole('button', { name: 'Delegate', exact: true }),
  };
}

for (const scenario of [
  { name: 'newer text survives an earlier acknowledgement', edits: [replacement], expected: replacement, fails: false },
  { name: 'identical text entered again is a new draft', edits: [replacement, assignment], expected: assignment, fails: false },
  { name: 'a failed assignment preserves its draft', edits: [], expected: assignment, fails: true },
  { name: 'success clears an untouched submitted draft', edits: [], expected: '', fails: false },
]) {
  test('UX-02: ' + scenario.name, async ({ page }) => {
    const arrived = deferred();
    const reply = deferred();
    const calls: Record<string, unknown>[] = [];
    await page.route('**/__agentos/call', async route => {
      const call = route.request().postDataJSON() as { method: string; params: Record<string, unknown> };
      if (call.method !== 'missions.delegate') return route.continue();
      calls.push(call.params);
      const response = scenario.fails ? null : await route.fetch();
      arrived.release();
      await reply.promise;
      if (response) return route.fulfill({ response });
      return route.fulfill({ status: 503, json: { status: 503, detail: rejected } });
    });
    const { profile, panel, task, delegate } = await openDelegate(page);
    try {
      await task.fill(assignment);
      await delegate.click();
      await arrived.promise;
      await expect(delegate).toBeDisabled();
      for (const edit of scenario.edits) await task.fill(edit);
      reply.release();
      if (scenario.fails) await expect(panel.getByRole('alert')).toHaveText(rejected);
      else await expect(panel.getByRole('status')).toContainText('Delegated. Mission');
      expect(calls).toEqual([{
        conversation_id: 'team-panel:' + profile.id,
        employee_role: 'custom',
        agent_profile_id: profile.id,
        title: assignment,
        instructions: assignment,
      }]);
      await expect(task).toHaveValue(scenario.expected);
    } finally {
      reply.release();
    }
  });
}
