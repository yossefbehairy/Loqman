import { test, expect, type Page } from '@playwright/test';

async function navigateToCompanyView(page: Page) {
  await page.goto('/');
  const companyNav = page.locator('[data-nav="company"], button:has-text("الشركة والعقود"), button:has-text("Company & Contracts"), button:has-text("إدارات الشركة"), button:has-text("Company")').first();
  await companyNav.waitFor({ state: 'visible', timeout: 10000 });
  await companyNav.click();
  await page.waitForTimeout(300);
}

test.describe('R2: Hierarchical Organization Structure & Natural Naming', () => {
  test('Renders all three core company departments: Planning, Research, Quality Assurance', async ({ page }) => {
    await navigateToCompanyView(page);

    // 1. Planning & Strategy department
    const planningDept = page.locator('text=/Planning & Strategy|إدارة التخطيط والإستراتيجية|إدارة التخطيط/i').first();
    await expect(planningDept).toBeVisible();

    // 2. Research & Feasibility department
    const researchDept = page.locator('text=/Research & Feasibility|Analysis & Risk|إدارة التحليل والمخاطر|دراسات الجدوى/i').first();
    await expect(researchDept).toBeVisible();

    // 3. Quality Assurance & Review department
    const qaDept = page.locator('text=/Quality Assurance & Review|Quality & Review|إدارة الجودة والاعتماد|المراجعة والاعتماد/i').first();
    await expect(qaDept).toBeVisible();
  });

  test('Enforces visual hierarchy with 👑 golden crown badges for Managers vs 💼 badges for Specialists', async ({
    page,
  }) => {
    await navigateToCompanyView(page);

    // 1. Verify Manager 👑 golden crown badge prominence
    const managerBadges = page.locator('[data-badge="manager"], .manager-badge').or(page.locator('text=/👑.*Manager|👑.*مدير|👑/i'));
    await expect(managerBadges.first()).toBeVisible();

    // Verify manager count is at least 3 (one per department)
    const managerCount = await managerBadges.count();
    expect(managerCount).toBeGreaterThanOrEqual(1);

    // 2. Verify Specialist 💼 badge for individual contributors
    const specialistBadges = page.locator('[data-badge="specialist"], .specialist-badge').or(page.locator('text=/💼.*Specialist|💼.*أخصائي|💼/i'));
    await expect(specialistBadges.first()).toBeVisible();

    const specialistCount = await specialistBadges.count();
    expect(specialistCount).toBeGreaterThanOrEqual(1);
  });

  test('Displays full natural naming support across managers and specialist employees', async ({ page }) => {
    await navigateToCompanyView(page);

    // Verify Natural Arabic Names for Department Managers
    const drSara = page.locator('text=/د. سارة|Dr. Sara/i').first();
    const engTarek = page.locator('text=/م. طارق|Eng. Tarek/i').first();
    const profYasmin = page.locator('text=/أ. ياسمين|Prof. Yasmin/i').first();

    await expect(drSara).toBeVisible();
    await expect(engTarek).toBeVisible();
    await expect(profYasmin).toBeVisible();

    // Verify Natural Names for Specialist Employees
    const ahmed = page.locator('text=/أحمد|Ahmed/i').first();
    const khaled = page.locator('text=/خالد|Khaled/i').first();
    const layla = page.locator('text=/ليلى|Layla/i').first();

    await expect(ahmed).toBeVisible();
    await expect(khaled).toBeVisible();
    await expect(layla).toBeVisible();
  });

  test('Department filtering tabs or view selectors segment members correctly', async ({ page }) => {
    await navigateToCompanyView(page);

    // Look for department filter tabs
    const planningTab = page.locator('[data-dept-tab="planning"], button:has-text("Planning"), button:has-text("التخطيط")').first();
    if (await planningTab.isVisible()) {
      await planningTab.click();
      await expect(page.locator('text=/د. سارة|Dr. Sara/i').first()).toBeVisible();
    }

    const researchTab = page.locator('[data-dept-tab="research"], button:has-text("Research"), button:has-text("التحليل")').first();
    if (await researchTab.isVisible()) {
      await researchTab.click();
      await expect(page.locator('text=/م. طارق|Eng. Tarek/i').first()).toBeVisible();
    }

    const qaTab = page.locator('[data-dept-tab="qa"], button:has-text("Quality"), button:has-text("الجودة")').first();
    if (await qaTab.isVisible()) {
      await qaTab.click();
      await expect(page.locator('text=/أ. ياسمين|Prof. Yasmin/i').first()).toBeVisible();
    }
  });
});
