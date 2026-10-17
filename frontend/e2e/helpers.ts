import { expect, type Locator, type Page } from '@playwright/test';

/**
 * Shared helpers for the AgentOS E2E suite.
 *
 * The workspace starts clean (one CEO, no teams). These helpers create
 * departments, specialists and delegated missions through the real UI so
 * specs can exercise the current application surface.
 */

const ESCAPE_RE = /[.*+?^${}()|[\]\\]/g;

export function escapeRegExp(text: string): string {
  return text.replace(ESCAPE_RE, '\\$&');
}

/** Department presets wired to the seeded backend employee profiles. */
export const DEPARTMENTS = {
  planning: { name: 'Planning & Strategy', manager: 'د. سارة', managerRole: 'مدير التخطيط والإستراتيجية' },
  research: { name: 'Research & Feasibility', manager: 'م. طارق', managerRole: 'مدير التحليل والمخاطر' },
  quality: { name: 'Quality & Review', manager: 'أ. ياسمين', managerRole: 'مدير الجودة والاعتماد' },
} as const;

export type DeptKey = keyof typeof DEPARTMENTS;

/** The department focus chip renders as "{name} {memberCount}". */
export function teamChip(page: Page, name: string): Locator {
  return page.getByRole('button', { name: new RegExp(escapeRegExp(name)) });
}

export async function gotoCompany(page: Page): Promise<void> {
  await page.goto('/#company');
  await expect(page.getByRole('heading', { name: 'Company' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'New team' })).toBeVisible();
}

/** Create a department (team + manager) through the New team modal. */
export async function createTeam(
  page: Page,
  name: string,
  managerName: string,
  managerRole: string
): Promise<void> {
  await page.getByRole('button', { name: 'New team' }).click();
  await expect(page.getByRole('heading', { name: 'New Department Team' })).toBeVisible();
  await page.getByPlaceholder('e.g. Marketing & Growth, DevOps & Infra...').fill(name);
  await page.getByPlaceholder('e.g. Layla, Dr. Omar...').fill(managerName);
  await page.getByPlaceholder('e.g. Head of Growth...').fill(managerRole);
  await page.getByRole('button', { name: 'Create team' }).click();
  await expect(page.getByRole('heading', { name: 'New Department Team' })).not.toBeVisible();
  await expect(teamChip(page, name)).toBeVisible();
}

/** Create the given departments in order (starts from the Company view). */
export async function seedDepartments(page: Page, depts: DeptKey[]): Promise<void> {
  await gotoCompany(page);
  for (const key of depts) {
    const dept = DEPARTMENTS[key];
    await createTeam(page, dept.name, dept.manager, dept.managerRole);
  }
}

/**
 * Add a specialist to the team of the currently selected manager
 * (managers are auto-selected right after team creation).
 */
export async function addSpecialist(page: Page, name: string, roleTitle: string): Promise<void> {
  await page.getByRole('button', { name: 'Add specialist' }).first().click();
  await expect(page.getByRole('heading', { name: 'New Employee Contract' })).toBeVisible();
  await page.getByPlaceholder('e.g. Layla Haddad...').fill(name);
  await page.getByPlaceholder('e.g. Planning Specialist, Risk Analyst...').fill(roleTitle);
  await page.getByRole('button', { name: 'Save contract' }).click();
  await expect(page.getByRole('heading', { name: 'New Employee Contract' })).not.toBeVisible();
}

export async function switchCompanyView(page: Page, mode: 'graph' | 'org'): Promise<void> {
  await page.getByRole('button', { name: mode === 'org' ? 'Org chart' : 'Graph', exact: true }).click();
}

/** The org-chart detail inspector aside (contract & summary card). */
export function detailPanel(page: Page): Locator {
  return page.locator('main aside.os-card');
}

export async function openConversations(page: Page): Promise<void> {
  await page.goto('/#conversations');
  await expect(page.getByRole('heading', { name: 'OpenCode Chat' })).toBeVisible();
  // The free-model catalog drives delegation; its name replacing the raw id
  // in the header subtitle means the catalog is loaded.
  await expect(
    page.getByText('Primary agent: build · DeepSeek V4.1 Flash Free (Mock)')
  ).toBeVisible({ timeout: 15000 });
}

/** Select a persona (agent) in the Conversations agent list. */
export async function selectPersona(page: Page, name: string): Promise<void> {
  await page.getByRole('button', { name: new RegExp(escapeRegExp(name)) }).first().click();
  await expect(page.getByText(`OpenCode · ${name}`, { exact: true })).toBeVisible();
}

/**
 * Send a chat message from the Conversations composer. A message whose text
 * matches the delegation grammar ("Delegate this mission to planner: ...")
 * starts a real backend employee mission.
 */
export async function sendMessage(page: Page, text: string, persona?: string): Promise<void> {
  if (persona) await selectPersona(page, persona);
  await page.locator('textarea[aria-label="Message text"]').fill(text);
  await page.getByRole('button', { name: 'Send message' }).click();
  await expect(page.getByText(text).first()).toBeVisible();
}

/** Full delegation flow: open Conversations and send the mission message. */
export async function sendDelegatedMission(page: Page, text: string, persona?: string): Promise<void> {
  await openConversations(page);
  await sendMessage(page, text, persona);
}

/** The app acknowledges the assignment naming the assignee. */
export async function expectAssignedAck(page: Page, assignee?: string): Promise<void> {
  const pattern = assignee
    ? new RegExp(`assigned to ${escapeRegExp(assignee)}`)
    : /assigned to/;
  await expect(page.getByText(pattern).first()).toBeVisible({ timeout: 20000 });
}

/** The mission poller reports the finished employee task in the same thread. */
export async function expectTaskCompletion(page: Page): Promise<void> {
  await expect(page.getByText(/Employee task \S+ succeeded/).first()).toBeVisible({ timeout: 40000 });
}

/** Navigate to the Inbox and return the newest review card (its inspect target). */
export async function openNewestInboxReview(page: Page, timeout = 30000): Promise<Locator> {
  await page.goto('/#inbox');
  await expect(page.getByRole('heading', { name: 'Inbox' })).toBeVisible();
  const card = page.locator('div[role="button"][aria-label^="Inspect"]').first();
  await expect(card).toBeVisible({ timeout });
  return card;
}

/** The task/deliverable inspection dialog. */
export function inspectDialog(page: Page): Locator {
  return page.locator('[role="dialog"]');
}
