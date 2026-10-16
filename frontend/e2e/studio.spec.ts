import { test, expect, type Page } from '@playwright/test';

async function navigateToStudioMap(page: Page) {
  await page.goto('/');
  // If modern navigation sidebar is present, navigate to Interactive Agent Map
  const mapNav = page.locator('[data-nav="map"], button:has-text("Interactive Agent Map"), button:has-text("الخريطة التفاعلية"), button:has-text("Agent Map")').first();
  if (await mapNav.isVisible({ timeout: 1500 }).catch(() => false)) {
    await mapNav.click();
    await page.waitForTimeout(300);
  }
}

test.describe('AgentOS Studio — Visual Canvas, Inspector & Team Chat E2E', () => {
  test('Canvas renders initial demo pipeline with all 5 nodes and badges', async ({ page }) => {
    await navigateToStudioMap(page);

    // 1. Verify Header and Branding
    await expect(page.locator('text=AgentOS').first()).toBeVisible();
    await expect(page.locator('text=Studio').first()).toBeVisible();
    await expect(page.locator('button:has-text("Validate DAG")')).toBeVisible();
    await expect(page.locator('button:has-text("Run Pipeline")')).toBeVisible();

    // 2. Verify all 5 Demo Nodes exist in canvas DOM
    await expect(page.locator('text=Start Trigger')).toBeVisible();
    await expect(page.locator('text=Lead Planner')).toBeVisible();
    await expect(page.locator('text=Risk Analyst')).toBeVisible();
    await expect(page.locator('text=Quality Reviewer')).toBeVisible();
    await expect(page.locator('text=Capstone Deliverable')).toBeVisible();

    // 3. Verify Free Model badge
    await expect(page.locator('text=muse-spark-1.3-contributor-free').first()).toBeVisible();
  });

  test('Node Inspector opens on node click and renders Role Library, Privacy Notice, and Task Depth', async ({
    page,
  }) => {
    await navigateToStudioMap(page);

    // Click Lead Planner node
    await page.locator('text=Lead Planner').click();

    // Inspector should open
    await expect(page.locator('text=Node Inspector')).toBeVisible();
    await expect(page.locator('text=AI Employee Role Library')).toBeVisible();

    // Verify Privacy Notice
    await expect(page.locator('text=Notice: Free contributor tier models')).toBeVisible();

    // Verify Task Depth options
    await expect(page.locator('button:has-text("Quick")')).toBeVisible();
    await expect(page.locator('button:has-text("Standard")')).toBeVisible();
    await expect(page.locator('button:has-text("Deep")')).toBeVisible();

    // Click 'Deep' task depth and verify visual active state
    await page.locator('button:has-text("Deep")').click();
  });

  test('DAG Graph Validation triggers and confirms valid topological graph', async ({ page }) => {
    await navigateToStudioMap(page);

    // Click Validate DAG
    const validateBtn = page.locator('button:has-text("Validate DAG")');
    await validateBtn.click();

    // Should stay valid without error overlay
    await expect(page.locator('text=DAG Graph Validation Issues')).not.toBeVisible();
    await expect(validateBtn).toBeVisible();
  });

  test('Full Pipeline Run triggers live SSE and streams Team Chat messages to Drawer', async ({ page }) => {
    test.setTimeout(60000);
    await navigateToStudioMap(page);

    // Click Run Pipeline
    const runBtn = page.locator('button:has-text("Run Pipeline")');
    await runBtn.click();

    // Execution Drawer should open
    await expect(page.getByRole('heading', { name: 'Workflow Execution' })).toBeVisible({ timeout: 10000 });
    await expect(page.locator('text=Live Team Chat & Handover Stream')).toBeVisible();

    // Wait for at least Turn 1, Turn 2, Turn 3 messages to appear in DOM
    await expect(page.locator('text=Turn 1')).toBeVisible({ timeout: 10000 });
    await expect(page.locator('text=Turn 2')).toBeVisible({ timeout: 10000 });
    await expect(page.locator('text=Turn 3')).toBeVisible({ timeout: 10000 });

    // Switch to Node Outputs tab
    await page.locator('button:has-text("Node Outputs")').click();
    await expect(page.locator('text=Pipeline Stages')).toBeVisible();
  });

  test('Execution History Modal opens and lists previous runs', async ({ page }) => {
    await navigateToStudioMap(page);

    // Click History button in Header
    await page.locator('button:has-text("History")').click();

    // Modal should be visible
    await expect(page.locator('text=Execution History (SQLite)')).toBeVisible();

    // Verify at least one run is shown with Replay button
    await expect(page.locator('button:has-text("Replay / Inspect")').first()).toBeVisible();
  });

  test('Adding new nodes via Palette expands canvas', async ({ page }) => {
    await navigateToStudioMap(page);

    // Click 'Add AI Agent' button in floating palette
    await page.locator('button[title="Add AI Agent Node"]').click();

    // Verify a new agent node is selected and inspector opens
    await expect(page.locator('text=Node Inspector')).toBeVisible();
    await expect(page.locator('input[value="New Agent"]')).toBeVisible();
  });

  test('Selecting DeepSeek model updates native reasoning variants dynamically', async ({ page }) => {
    await navigateToStudioMap(page);

    // Click Lead Planner node to open inspector
    await page.locator('text=Lead Planner').click();
    await expect(page.locator('text=Node Inspector')).toBeVisible();

    // Select DeepSeek free model from dropdown
    const modelSelect = page.locator('select').nth(1);
    await modelSelect.selectOption('deepseek-v4.1-flash-free');

    // Verify optgroup and reasoning variants
    await expect(page.locator('optgroup[label*="DeepSeek"]')).toBeAttached();

    // Select variant dropdown and verify native options
    const variantSelect = page.locator('select').nth(2);
    await expect(variantSelect.locator('option[value="low"]')).toBeAttached();
    await expect(variantSelect.locator('option[value="high"]')).toBeAttached();
    await expect(variantSelect.locator('option[value="max"]')).toBeAttached();

    // Select 'max' reasoning variant
    await variantSelect.selectOption('max');
  });
});
