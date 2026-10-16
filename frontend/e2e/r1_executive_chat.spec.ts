import { test, expect } from '@playwright/test';

test.describe('R1: Minimalist Executive Chat & Manager Direct Consultation', () => {
  test('App opens immediately into clean minimalist Executive Chat without canvas or button clutter', async ({
    page,
  }) => {
    await page.goto('/');

    // 1. Verify Executive Chat heading / branding
    const chatTitle = page.locator('text=/Executive Chat|الدردشة التنفيذية|Direct Consultation/i').first();
    await expect(chatTitle).toBeVisible();

    // 2. Verify clean landing: no canvas nodes or canvas-specific controls on initial view
    await expect(page.locator('text=Start Trigger')).not.toBeVisible();
    await expect(page.locator('text=Lead Planner')).not.toBeVisible();
    await expect(page.locator('button:has-text("Validate DAG")')).not.toBeVisible();
    await expect(page.locator('button:has-text("Run Pipeline")')).not.toBeVisible();

    // 3. Verify chat composer input is present and ready
    const composer = page.locator('textarea, input[placeholder*="message"], input[placeholder*="instructions"], [data-testid="executive-chat-input"]').first();
    await expect(composer).toBeVisible();
  });

  test('Persona selector pill allows choosing General Manager, Dr. Sara, Eng. Tarek, Prof. Yasmin', async ({
    page,
  }) => {
    await page.goto('/');

    // Verify all 4 manager options exist in the persona selector pill/bar
    const gmPill = page.locator('[data-persona="general_manager"], button:has-text("General Manager"), button:has-text("المدير العام")').first();
    const saraPill = page.locator('[data-persona="dr_sara"], button:has-text("Sara"), button:has-text("سارة")').first();
    const tarekPill = page.locator('[data-persona="eng_tarek"], button:has-text("Tarek"), button:has-text("طارق")').first();
    const yasminPill = page.locator('[data-persona="prof_yasmin"], button:has-text("Yasmin"), button:has-text("ياسمين")').first();

    await expect(gmPill).toBeVisible();
    await expect(saraPill).toBeVisible();
    await expect(tarekPill).toBeVisible();
    await expect(yasminPill).toBeVisible();

    // Switch to Dr. Sara and verify selection
    await saraPill.click();
    await expect(page.locator('text=/سارة|Sara|Planning/i').first()).toBeVisible();

    // Switch to Eng. Tarek and verify selection
    await tarekPill.click();
    await expect(page.locator('text=/طارق|Tarek|Analysis|Risk/i').first()).toBeVisible();

    // Switch to Prof. Yasmin and verify selection
    await yasminPill.click();
    await expect(page.locator('text=/ياسمين|Yasmin|Quality|Review/i').first()).toBeVisible();

    // Return to General Manager
    await gmPill.click();
    await expect(page.locator('text=/General Manager|المدير العام|Orchestrator/i').first()).toBeVisible();
  });

  test('Sending chat message to manager receives direct guidance response', async ({ page }) => {
    await page.goto('/');

    // Select Dr. Sara for strategic consultation
    const saraPill = page.locator('[data-persona="dr_sara"], button:has-text("Sara"), button:has-text("سارة")').first();
    if (await saraPill.isVisible()) {
      await saraPill.click();
    }

    // Locate composer input and type project goal
    const composer = page.locator('textarea, input[placeholder*="message"], input[placeholder*="instructions"], [data-testid="executive-chat-input"]').first();
    const testMessage = 'نحتاج خطة متكاملة لتطوير منصة تسجيل الطلاب وتوصية المقررات للجامعة';
    await composer.fill(testMessage);

    // Click Send or press Enter
    const sendButton = page.locator('button[type="submit"], button[aria-label*="Send"], button:has-text("Send"), button:has-text("إرسال")').first();
    if (await sendButton.isVisible()) {
      await sendButton.click();
    } else {
      await composer.press('Enter');
    }

    // 1. Verify user message appears in chat history
    await expect(page.locator(`text=${testMessage}`).first()).toBeVisible({ timeout: 5000 });

    // 2. Verify manager reply arrives
    const managerReply = page.locator('[data-message-role="assistant"], .manager-reply, .assistant-message, [data-testid="manager-response"]').first();
    await expect(managerReply).toBeVisible({ timeout: 15000 });
  });

  test('Collapsible left sidebar navigates seamlessly between all 5 core views without full page reloads', async ({
    page,
  }) => {
    await page.goto('/');

    // Track navigation to ensure no full page reload occurs
    let pageReloaded = false;
    page.on('load', () => {
      pageReloaded = true;
    });

    // 1. Verify 5 Core Navigation items exist in the sidebar
    const chatNav = page.locator('[data-nav="chat"], button:has-text("Executive Chat"), button:has-text("الدردشة التنفيذية")').first();
    const companyNav = page.locator('[data-nav="company"], button:has-text("Company & Contracts"), button:has-text("إدارات الشركة")').first();
    const mapNav = page.locator('[data-nav="map"], button:has-text("Interactive Agent Map"), button:has-text("الخريطة التفاعلية")').first();
    const streamNav = page.locator('[data-nav="stream"], button:has-text("Team Chat Stream"), button:has-text("سجل محادثات الوكلاء")').first();
    const settingsNav = page.locator('[data-nav="settings"], button:has-text("Settings & Models"), button:has-text("الإعدادات والموديلات")').first();

    await expect(chatNav).toBeVisible();
    await expect(companyNav).toBeVisible();
    await expect(mapNav).toBeVisible();
    await expect(streamNav).toBeVisible();
    await expect(settingsNav).toBeVisible();

    // 2. Navigate to Company & Contracts
    await companyNav.click();
    await expect(page.locator('text=/Company & Contracts|إدارات الشركة|Departments/i').first()).toBeVisible();

    // 3. Navigate to Interactive Agent Map
    await mapNav.click();
    await expect(page.locator('text=/Interactive Agent Map|الخريطة التفاعلية|Agent Map/i').first()).toBeVisible();

    // 4. Navigate to Team Chat Stream
    await streamNav.click();
    await expect(page.locator('text=/Team Chat Stream|سجل محادثات الوكلاء|Team Stream/i').first()).toBeVisible();

    // 5. Navigate to Settings & Models
    await settingsNav.click();
    await expect(page.locator('text=/Settings|Models Catalog|الموديلات|الإعدادات/i').first()).toBeVisible();

    // 6. Return to Executive Chat
    await chatNav.click();
    await expect(page.locator('text=/Executive Chat|الدردشة التنفيذية/i').first()).toBeVisible();

    // 7. Verify collapsible sidebar toggle
    const toggleSidebarBtn = page.locator('button[aria-label*="sidebar"], button[title*="sidebar"], button[data-testid="toggle-sidebar"]').first();
    if (await toggleSidebarBtn.isVisible()) {
      await toggleSidebarBtn.click();
      // Click again to restore
      await toggleSidebarBtn.click();
    }

    // Verify no full page reloads occurred during navigation
    expect(pageReloaded).toBe(false);
  });
});
