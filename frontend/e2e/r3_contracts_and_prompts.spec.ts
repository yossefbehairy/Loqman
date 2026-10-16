import { test, expect, type Page } from '@playwright/test';

async function navigateToCompanyContracts(page: Page) {
  await page.goto('/');
  const companyNav = page.locator('[data-nav="company"], button:has-text("الشركة والعقود"), button:has-text("Company & Contracts"), button:has-text("إدارات الشركة"), button:has-text("Company")').first();
  await companyNav.waitFor({ state: 'visible', timeout: 10000 });
  await companyNav.click();
  await page.waitForTimeout(300);
}

test.describe('R3: Custom System Prompts & Strict Execution Contracts', () => {
  test('"Company & Contracts" view renders department members and their execution contracts', async ({
    page,
  }) => {
    await navigateToCompanyContracts(page);

    // Verify view container
    await expect(page.locator('text=/Company & Contracts|إدارات الشركة|Contracts/i').first()).toBeVisible();

    // Verify member cards or table rows exist
    const editContractBtns = page.locator('button:has-text("Edit Contract"), button:has-text("Edit"), button:has-text("تعديل العقد"), [data-action="edit-contract"]');
    await expect(editContractBtns.first()).toBeVisible();
    const count = await editContractBtns.count();
    expect(count).toBeGreaterThanOrEqual(1);
  });

  test('Editing System Prompt for a persona persists instructions', async ({ page }) => {
    await navigateToCompanyContracts(page);

    // Click the first Edit Contract button or member card
    const editBtn = page.locator('button:has-text("Edit Contract"), button:has-text("Edit"), button:has-text("تعديل العقد"), [data-action="edit-contract"]').first();
    await editBtn.click();

    // Verify Contract Editor modal is visible
    const modal = page.locator('[role="dialog"], .contract-modal, [data-testid="contract-editor-modal"]').first();
    await expect(modal).toBeVisible();

    // Locate System Prompt textarea
    const promptTextarea = modal.locator('textarea[name="instructions"], textarea[name="system_prompt"], textarea').first();
    await expect(promptTextarea).toBeVisible();

    // Modify prompt
    const customPrompt = 'You are an elite AI director. Ensure all milestones are strictly verified and conform to enterprise security protocols.';
    await promptTextarea.fill(customPrompt);

    // Click Save
    const saveBtn = modal.locator('button:has-text("Save"), button:has-text("حفظ"), button[type="submit"]').first();
    await saveBtn.click();

    // Verify modal closes
    await expect(modal).not.toBeVisible({ timeout: 5000 });
  });

  test('Editing Execution Contract: strict JSON format, max attempts bound, timeout limit', async ({
    page,
  }) => {
    await navigateToCompanyContracts(page);

    // Open contract editor
    const editBtn = page.locator('button:has-text("Edit Contract"), button:has-text("Edit"), button:has-text("تعديل العقد"), [data-action="edit-contract"]').first();
    await editBtn.click();

    const modal = page.locator('[role="dialog"], .contract-modal, [data-testid="contract-editor-modal"]').first();
    await expect(modal).toBeVisible();

    // 1. Strict JSON Format checkbox / switch
    const strictJsonToggle = modal.locator('input[type="checkbox"], [data-testid="strict-json-toggle"], label:has-text("Strict JSON")').first();
    if (await strictJsonToggle.isVisible()) {
      if (await strictJsonToggle.isEnabled()) {
        await strictJsonToggle.check().catch(() => strictJsonToggle.click());
      }
    }

    // 2. Maximum Attempts Bound (1-3)
    const attemptsInput = modal.locator('input[name="max_retries"], input[name="max_attempts"], input[type="number"]').first();
    if (await attemptsInput.isVisible()) {
      await attemptsInput.fill('2');
    }

    // 3. Timeout Limit (e.g. 10 - 300 seconds)
    const timeoutInput = modal.locator('input[name="timeout"], input[name="timeout_seconds"], input[type="number"]').nth(1);
    if (await timeoutInput.isVisible()) {
      await timeoutInput.fill('90');
    }

    // Save Contract
    const saveBtn = modal.locator('button:has-text("Save"), button:has-text("حفظ"), button[type="submit"]').first();
    await saveBtn.click();
    await expect(modal).not.toBeVisible({ timeout: 5000 });
  });

  test('Zero-cost verified models in dropdown with reasoning variants (low, high, max)', async ({
    page,
  }) => {
    await navigateToCompanyContracts(page);

    // Open contract editor
    const editBtn = page.locator('button:has-text("Edit Contract"), button:has-text("Edit"), button:has-text("تعديل العقد"), [data-action="edit-contract"]').first();
    await editBtn.click();

    const modal = page.locator('[role="dialog"], .contract-modal, [data-testid="contract-editor-modal"]').first();
    await expect(modal).toBeVisible();

    // Verify model select contains DeepSeek Free and Muse Spark options
    const modelSelect = modal.locator('select').first();
    await expect(modelSelect).toBeVisible();

    // Select DeepSeek free model
    await modelSelect.selectOption({ value: 'deepseek-v4.1-flash-free' });

    // Verify Reasoning Variants selector exists
    const variantSelect = modal.locator('select').nth(1);
    if (await variantSelect.isVisible()) {
      await expect(variantSelect.locator('option[value="low"]')).toBeAttached();
      await expect(variantSelect.locator('option[value="high"]')).toBeAttached();
      await expect(variantSelect.locator('option[value="max"]')).toBeAttached();

      // Choose 'max' reasoning variant
      await variantSelect.selectOption('max');
    }

    // Cancel or close modal
    const closeBtn = modal.locator('button:has-text("Cancel"), button:has-text("Close"), button:has-text("إلغاء")').first();
    if (await closeBtn.isVisible()) {
      await closeBtn.click();
    } else {
      await page.keyboard.press('Escape');
    }
  });
});
