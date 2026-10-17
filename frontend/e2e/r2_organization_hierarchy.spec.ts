import { test, expect } from '@playwright/test';
import {
  DEPARTMENTS,
  addSpecialist,
  createTeam,
  gotoCompany,
  seedDepartments,
  switchCompanyView,
  teamChip,
} from './helpers';

/**
 * R2: Hierarchical Organization Structure & Natural Naming.
 *
 * The workspace is clean on launch, so the three core departments are created
 * through the New team modal (natural Arabic manager names drive the seeded
 * execution profiles). Hierarchy is rendered as Executive / Manager /
 * Specialist tags instead of crown/briefcase emoji badges, and the team focus
 * chips act as the department filter.
 */
test.describe('R2: Hierarchical Organization Structure & Natural Naming', () => {
  test('Renders all three core company departments: Planning, Research, Quality Assurance', async ({
    page,
  }) => {
    await seedDepartments(page, ['planning', 'research', 'quality']);

    // 1. Planning & Strategy department
    await expect(teamChip(page, 'Planning & Strategy')).toBeVisible();

    // 2. Research & Feasibility department
    await expect(teamChip(page, 'Research & Feasibility')).toBeVisible();

    // 3. Quality Assurance & Review department
    await expect(teamChip(page, 'Quality & Review')).toBeVisible();

    // Header stats confirm all three departments with one manager each
    await expect(page.getByText('0 specialists · 1 CEO · 3 managers · 3 teams')).toBeVisible();

    // Org chart renders the three department desks
    await switchCompanyView(page, 'org');
    for (const dept of ['Planning & Strategy', 'Research & Feasibility', 'Quality & Review']) {
      await expect(page.getByText(dept, { exact: true }).first()).toBeVisible();
    }
  });

  test('Enforces visual hierarchy with Manager tags for Managers vs Specialist tags for Specialists', async ({
    page,
  }) => {
    await gotoCompany(page);
    const planning = DEPARTMENTS.planning;
    await createTeam(page, planning.name, planning.manager, planning.managerRole);
    await addSpecialist(page, 'أحمد', 'أخصائي البرمجة والتنفيذ');
    await switchCompanyView(page, 'org');

    // 1. Verify Manager badge (amber tag) prominence on the department lead
    const managerBadges = page.locator('span.os-tag.os-tag-amber:has-text("Manager")');
    await expect(managerBadges.first()).toBeVisible();
    expect(await managerBadges.count()).toBeGreaterThanOrEqual(1);

    // 2. The CEO carries the Executive tag in the detail inspector
    await page.getByRole('button', { name: 'Select Adel Mansour' }).click();
    await expect(
      page.locator('main aside span.os-tag.os-tag-mono:text-is("Executive")')
    ).toBeVisible();

    // 3. Individual contributors carry the green Specialist tag
    await page.getByRole('button', { name: /أحمد/ }).first().click();
    await expect(
      page.locator('main aside span.os-tag.os-tag-green:text-is("Specialist")')
    ).toBeVisible();
  });

  test('Displays full natural naming support across managers and specialist employees', async ({
    page,
  }) => {
    await gotoCompany(page);

    // Manager with natural Arabic name — Planning & Strategy
    await createTeam(
      page,
      DEPARTMENTS.planning.name,
      DEPARTMENTS.planning.manager,
      DEPARTMENTS.planning.managerRole
    );
    await addSpecialist(page, 'أحمد', 'أخصائي البرمجة والتنفيذ');

    // Manager with natural Arabic name — Research & Feasibility
    await createTeam(
      page,
      DEPARTMENTS.research.name,
      DEPARTMENTS.research.manager,
      DEPARTMENTS.research.managerRole
    );
    await addSpecialist(page, 'خالد', 'أخصائي البحث والاستكشاف');

    // Manager with natural Arabic name — Quality & Review
    await createTeam(
      page,
      DEPARTMENTS.quality.name,
      DEPARTMENTS.quality.manager,
      DEPARTMENTS.quality.managerRole
    );
    await addSpecialist(page, 'ليلى', 'أخصائية استخراج البيانات');

    await switchCompanyView(page, 'org');

    // Verify Natural Arabic Names for Department Managers
    await expect(page.getByText(/د\. سارة/).first()).toBeVisible();
    await expect(page.getByText(/م\. طارق/).first()).toBeVisible();
    await expect(page.getByText(/أ\. ياسمين/).first()).toBeVisible();

    // Verify Natural Names for Specialist Employees
    await expect(page.getByText(/أحمد/).first()).toBeVisible();
    await expect(page.getByText(/خالد/).first()).toBeVisible();
    await expect(page.getByText(/ليلى/).first()).toBeVisible();
  });

  test('Department focus chips segment members correctly', async ({ page }) => {
    await seedDepartments(page, ['planning', 'research']);
    await switchCompanyView(page, 'org');

    const chip = (name: string) => teamChip(page, name);
    const teamCard = (name: string) =>
      page
        .locator('div.os-card')
        .filter({ has: page.getByRole('button', { name: 'Focus' }) })
        .filter({ has: page.getByText(name, { exact: true }) });

    // Focus Planning & Strategy — its chip becomes active and the other desk dims
    await chip('Planning & Strategy').click();
    await expect(chip('Planning & Strategy')).toHaveClass(/border-\[var\(--acc\)\]/);
    await expect(teamCard('Planning & Strategy')).not.toHaveClass(/opacity-40/);
    await expect(teamCard('Research & Feasibility')).toHaveClass(/opacity-40/);

    // Switch to Research & Feasibility — focus moves to that desk
    await chip('Research & Feasibility').click();
    await expect(chip('Research & Feasibility')).toHaveClass(/border-\[var\(--acc\)\]/);
    await expect(teamCard('Research & Feasibility')).not.toHaveClass(/opacity-40/);
    await expect(teamCard('Planning & Strategy')).toHaveClass(/opacity-40/);

    // Click again to un-focus (show all)
    await chip('Research & Feasibility').click();
    await expect(chip('Research & Feasibility')).not.toHaveClass(/border-\[var\(--acc\)\]/);
    await expect(teamCard('Planning & Strategy')).not.toHaveClass(/opacity-40/);
    await expect(teamCard('Research & Feasibility')).not.toHaveClass(/opacity-40/);
  });
});
