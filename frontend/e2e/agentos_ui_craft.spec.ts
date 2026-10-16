import { test, expect } from '@playwright/test';

test.describe('AgentOS Impeccable UI Verification', () => {
  test('should load company view with graph canvas and floating detail inspector', async ({ page }) => {
    await page.goto('/#company');
    await expect(page).toHaveTitle('AgentOS');

    // Verify Brand Logo
    await expect(page.locator('text=AgentOS').first()).toBeVisible();

    // Verify Navigation Links
    await expect(page.locator('button:has-text("Company")')).toBeVisible();
    await expect(page.locator('button:has-text("Inbox")')).toBeVisible();
    await expect(page.locator('button:has-text("Conversations")')).toBeVisible();
    await expect(page.locator('button:has-text("Results")')).toBeVisible();
    await expect(page.locator('button:has-text("Calendar")')).toBeVisible();
    await expect(page.locator('button:has-text("Settings")')).toBeVisible();

    // Verify Company Graph Canvas
    const canvas = page.locator('canvas').first();
    await expect(canvas).toBeVisible();

    // Verify Department Focus Chips
    await expect(page.locator('button:has-text("Planning & Strategy")')).toBeVisible();
    await expect(page.locator('button:has-text("Research & Feasibility")')).toBeVisible();
    await expect(page.locator('button:has-text("Quality & Review")')).toBeVisible();
  });

  test('should switch between Graph and Org chart views seamlessly', async ({ page }) => {
    await page.goto('/#company');

    // Switch to Org Chart
    await page.click('button:has-text("Org chart")');
    await expect(page.locator('text=Adel Mansour').first()).toBeVisible();
    await expect(page.locator('text=Planning & Strategy').first()).toBeVisible();
    await expect(page.locator('text=Dr. Sara').first()).toBeVisible();
    await expect(page.locator('text=Eng. Tarek').first()).toBeVisible();
    await expect(page.locator('text=Prof. Yasmin').first()).toBeVisible();

    // Switch back to Graph
    await page.click('button:has-text("Graph")');
    await expect(page.locator('canvas').first()).toBeVisible();
  });

  test('should navigate to Inbox and verify reviews and notices', async ({ page }) => {
    await page.goto('/#inbox');
    await expect(page.locator('h1:has-text("Inbox")')).toBeVisible();
    await expect(page.locator('text=Capstone plan v1')).toBeVisible();
    await expect(page.locator('text=Risk matrix v2')).toBeVisible();
    await expect(page.locator('text=Ahmed needs attention')).toBeVisible();

    // Test filter tabs
    await page.click('button:has-text("Reviews")');
    await expect(page.locator('text=Capstone plan v1')).toBeVisible();

    await page.click('button:has-text("Notices")');
    await expect(page.locator('text=Ahmed needs attention')).toBeVisible();

    // Test Mark all as read
    await page.click('button:has-text("Mark all as read")');
    await expect(page.locator('text=All notices marked as read')).toBeVisible();
  });

  test('should interact with Conversations and simulate auto-replies', async ({ page }) => {
    await page.goto('/#conversations');
    await expect(page.locator('h1:has-text("Conversations")')).toBeVisible();

    // Verify Adel Mansour CEO is selected
    await expect(page.locator('div:has-text("Adel Mansour")').first()).toBeVisible();

    // Type a message
    const textarea = page.locator('textarea[aria-label="Message text"]');
    await textarea.fill('Prepare risk matrix and quality checks for new mission.');
    await page.click('button:has-text("Send")');

    // Verify user message appeared
    await expect(page.locator('text=Prepare risk matrix and quality checks for new mission.').first()).toBeVisible();

    // Wait for simulated response from Adel Mansour
    await expect(page.locator('text=Understood. I will route this mission across Planning, Research, and Quality teams').first()).toBeVisible({
      timeout: 5000,
    });
  });

  test('should display Results metrics, bar chart, and receipt table', async ({ page }) => {
    await page.goto('/#results');
    await expect(page.locator('h1:has-text("Results")')).toBeVisible();
    await expect(page.locator('text=Pipeline runs')).toBeVisible();
    await expect(page.locator('text=Success rate')).toBeVisible();
    await expect(page.locator('text=Token cost')).toBeVisible();
    await expect(page.locator('text=Runs per day')).toBeVisible();
    await expect(page.locator('text=Execution Run Receipts')).toBeVisible();
  });

  test('should toggle Calendar schedules and open Settings', async ({ page }) => {
    await page.goto('/#calendar');
    await expect(page.locator('h1:has-text("Calendar")')).toBeVisible();
    await expect(page.locator('b:has-text("Capstone Multi-Agent Pipeline")').first()).toBeVisible();

    await page.goto('/#settings');
    await expect(page.locator('h1:has-text("Settings")')).toBeVisible();
    await expect(page.locator('text=OpenCode Engine')).toBeVisible();
    await expect(page.locator('text=Online').first()).toBeVisible();
    await expect(page.locator('text=Default model')).toBeVisible();
  });

  test('should open and close Contract modal and Help modal', async ({ page }) => {
    await page.goto('/#company');

    // Click on Dr. Sara manager card in Org chart
    await page.click('button:has-text("Org chart")');
    await page.click('button:has-text("Dr. Sara")');

    // Click Edit contract button in the inspector
    await page.click('button:has-text("Edit contract")');
    await expect(page.locator('h3:has-text("Edit Contract")')).toBeVisible();
    await expect(page.locator('input[value="Dr. Sara"]')).toBeVisible();

    // Close modal
    await page.click('button:has-text("Cancel")');
    await expect(page.locator('h3:has-text("Edit Contract")')).not.toBeVisible();

    // Open Help modal with shortcut key 'h'
    await page.keyboard.press('h');
    await expect(page.locator('h3:has-text("How to Read the Company Graph")')).toBeVisible();
    await page.click('button:has-text("Got it")');
    await expect(page.locator('h3:has-text("How to Read the Company Graph")')).not.toBeVisible();
  });

  test('should click on review item in Inbox and inspect active task & inter-agent dialogue', async ({ page }) => {
    await page.goto('/#inbox');
    await expect(page.locator('h1:has-text("Inbox")')).toBeVisible();

    // Click on Capstone plan v1 review card
    await page.click('text=Capstone plan v1');

    // Verify TaskInspectionModal opened
    await expect(page.locator('h2:has-text("Capstone plan v1")')).toBeVisible();
    await expect(page.locator('text=حوار ومناقشة الفريق')).toBeVisible();
    await expect(page.locator('text=Turn 1')).toBeVisible();
    await expect(page.locator('text=تكليف إداري: قمنا بتفكيك متطلبات منظومة تسجيل المقررات')).toBeVisible();

    // Switch to output JSON tab
    await page.click('button:has-text("المخرجات والتقرير الفني")');
    await expect(page.locator('text=Structured Deliverable JSON Schema')).toBeVisible();

    // Close modal
    await page.click('button:has-text("Close")');
    await expect(page.locator('h2:has-text("Capstone plan v1")')).not.toBeVisible();
  });

  test('should inspect agent active task from graph/org chart inspector', async ({ page }) => {
    await page.goto('/#company');
    await page.click('button:has-text("Org chart")');

    // Click on Eng. Tarek
    await page.click('button:has-text("Eng. Tarek")');

    // Click "View active task & dialogue (ماذا يفعل الآن)" button in detail panel
    await page.click('button:has-text("View active task & dialogue")');

    // Verify task modal opens with Eng. Tarek's active work
    await expect(page.locator('h2:has-text("Risk matrix v2")')).toBeVisible();
    await expect(page.locator('text=Operational & Security Feasibility')).toBeVisible();
    await expect(page.locator('text=Omar').first()).toBeVisible();

    // Close modal
    await page.click('button:has-text("Close")');
    await expect(page.locator('h2:has-text("Risk matrix v2")')).not.toBeVisible();
  });

  test('should open Team Stream page and verify inter-agent dialogue turns', async ({ page }) => {
    await page.goto('/#stream');
    await expect(page.locator('h1:has-text("Team Dialogue Stream")')).toBeVisible();
    await expect(page.locator('text=Live Inter-Agent Stream')).toBeVisible();
    await expect(page.locator('text=Turn 1').first()).toBeVisible();
    await expect(page.locator('text=Turn 2').first()).toBeVisible();
  });
});
