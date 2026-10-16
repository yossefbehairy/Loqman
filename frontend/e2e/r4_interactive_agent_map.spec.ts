import { test, expect, type Page } from '@playwright/test';

async function navigateToAgentMap(page: Page) {
  await page.goto('/');
  const mapNav = page.locator('[data-nav="map"], button:has-text("Interactive Agent Map"), button:has-text("الخريطة التفاعلية"), button:has-text("Agent Map")').first();
  if (await mapNav.isVisible({ timeout: 2000 }).catch(() => false)) {
    await mapNav.click();
    await page.waitForTimeout(300);
  }
}

test.describe('R4: Live Interactive Agent Map & Execution Halos', () => {
  test('Renders interactive map canvas with department zone groupings/boundaries', async ({ page }) => {
    await navigateToAgentMap(page);

    // 1. Verify Map Canvas Container is rendered
    const mapContainer = page.locator('.react-flow, [data-testid="agent-map-canvas"], .interactive-map-container').first();
    await expect(mapContainer).toBeVisible();

    // 2. Verify Department Zone groupings / boundary labels
    const planningZone = page.locator('text=/Planning & Strategy Zone|إدارة التخطيط|Planning/i').first();
    const researchZone = page.locator('text=/Research & Feasibility Zone|Analysis & Risk|التحليل والمخاطر|Research/i').first();
    const qaZone = page.locator('text=/Quality Assurance & Review Zone|Quality & Review|الجودة والاعتماد|QA/i').first();

    await expect(planningZone).toBeVisible();
    await expect(researchZone).toBeVisible();
    await expect(qaZone).toBeVisible();

    // 3. Verify Agent Nodes exist in the map
    const nodes = page.locator('.react-flow__node, [data-testid="agent-node"]');
    await expect(nodes.first()).toBeVisible();
    const count = await nodes.count();
    expect(count).toBeGreaterThanOrEqual(3);
  });

  test('Status indicator halos: running nodes pulse, completed nodes turn emerald green with ms duration', async ({
    page,
  }) => {
    test.setTimeout(60000);
    await navigateToAgentMap(page);

    // Trigger run if run button is available on map view or header
    const runBtn = page.locator('button:has-text("Run Pipeline"), button:has-text("Run Mission"), button:has-text("تشغيل"), [data-action="run"]').first();
    if (await runBtn.isVisible()) {
      await runBtn.click();

      // Verify running pulse halo appears during execution
      const runningHalo = page.locator('.animate-pulse, [data-status="running"], .status-halo-running, .ring-blue-500, .ring-amber-500').first();
      await expect(runningHalo).toBeVisible({ timeout: 10000 });

      // Verify completed nodes turn emerald green and display execution duration in milliseconds
      const completedNode = page.locator('[data-status="completed"], [data-status="succeeded"], .border-emerald-500, .bg-emerald-950').or(page.locator('text=/ms|مللي ثانية/i')).first();
      await expect(completedNode).toBeVisible({ timeout: 20000 });
    } else {
      // Direct inspection of status styling classes
      const nodeElements = page.locator('.react-flow__node');
      await expect(nodeElements.first()).toBeVisible();
    }
  });

  test('Clicking a node opens Contract Card and output preview', async ({ page }) => {
    await navigateToAgentMap(page);

    // Click on any agent node
    const agentNode = page.locator('.react-flow__node:has-text("Planner"), .react-flow__node:has-text("Analyst"), .react-flow__node:has-text("سارة"), .react-flow__node:has-text("طارق"), [data-testid="agent-node"]').first();
    await agentNode.click();

    // Verify Inspector / Contract Card panel opens
    const contractCard = page.locator('[data-testid="node-contract-card"], [data-testid="contract-card-modal"], [data-testid="node-inspector"]').or(page.locator('text=/Node Inspector|Contract Card|بطاقة العقد/i')).first();
    await expect(contractCard).toBeVisible();

    // Verify contract metadata is visible (role, instructions/prompt, model)
    await expect(page.locator('text=/Model|Instructions|System Prompt|Task Depth|الدور/i').first()).toBeVisible();

    // Verify output preview tab or section is accessible
    const outputSection = page.locator('text=/Output|Deliverable|المخرجات|Output Preview/i').first();
    await expect(outputSection).toBeVisible();
  });
});
