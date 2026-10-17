import { test, expect, type Page } from '@playwright/test';
import { DEPARTMENTS, createTeam, gotoCompany, switchCompanyView, teamChip } from './helpers';

/**
 * R3: Custom System Prompts & Strict Execution Contracts.
 *
 * The former "Company & Contracts" view is now the Company view; execution
 * contracts are edited from the detail inspector's Contract modal. The
 * retry bound ("max attempts") lives in Settings as "Retries on failure".
 */
async function openManagerContract(page: Page): Promise<void> {
  await gotoCompany(page);
  const planning = DEPARTMENTS.planning;
  await createTeam(page, planning.name, planning.manager, planning.managerRole);
  await switchCompanyView(page, 'org');
  await page.getByRole('button', { name: /د\. سارة/ }).first().click();
  await page.getByRole('button', { name: 'Edit contract' }).click();
  await expect(page.getByRole('heading', { name: 'Edit Contract' })).toBeVisible();
}

function contractModal(page: Page) {
  return page.locator('div.fixed:has(h3:has-text("Edit Contract"))');
}

test.describe('R3: Custom System Prompts & Strict Execution Contracts', () => {
  test('Company view renders department members and their execution contracts', async ({ page }) => {
    await gotoCompany(page);
    const planning = DEPARTMENTS.planning;
    await createTeam(page, planning.name, planning.manager, planning.managerRole);

    // Verify the department desk and its member are rendered
    await expect(teamChip(page, 'Planning & Strategy')).toBeVisible();
    await switchCompanyView(page, 'org');
    await page.getByRole('button', { name: /د\. سارة/ }).first().click();

    // Execution contract surface: inspector metadata + Edit contract action
    const panel = page.locator('main aside.os-card');
    await expect(panel.getByText('Model', { exact: true })).toBeVisible();
    await expect(panel.getByText('System Prompt', { exact: true })).toBeVisible();
    const editContractBtn = page.getByRole('button', { name: 'Edit contract' });
    await expect(editContractBtn).toBeVisible();

    // The contract editor exposes the member's contract values
    await editContractBtn.click();
    await expect(page.getByRole('heading', { name: 'Edit Contract' })).toBeVisible();
    await expect(page.getByPlaceholder('e.g. Layla Haddad...')).toHaveValue('د. سارة');
    await expect(page.getByPlaceholder('e.g. Planning Specialist, Risk Analyst...')).toHaveValue(
      'مدير التخطيط والإستراتيجية'
    );
  });

  test('Editing System Prompt for a persona persists instructions', async ({ page }) => {
    await openManagerContract(page);

    // Locate System Prompt textarea
    const promptTextarea = page.getByPlaceholder(
      'Instructions and operating boundaries for this employee...'
    );
    await expect(promptTextarea).toBeVisible();

    // Modify prompt
    const customPrompt =
      'You are an elite AI director. Ensure all milestones are strictly verified and conform to enterprise security protocols.';
    await promptTextarea.fill(customPrompt);

    // Click Save
    await page.getByRole('button', { name: 'Save contract' }).click();
    await expect(page.getByRole('heading', { name: 'Edit Contract' })).not.toBeVisible();
    await expect(page.getByRole('status')).toContainText('Contract updated successfully');

    // Reopen the contract and verify the custom instructions persisted
    await page.getByRole('button', { name: 'Edit contract' }).click();
    await expect(page.getByRole('heading', { name: 'Edit Contract' })).toBeVisible();
    await expect(promptTextarea).toHaveValue(customPrompt);
  });

  test('Editing Execution Contract: strict JSON format, max attempts bound, timeout limit', async ({
    page,
  }) => {
    await openManagerContract(page);
    const modal = contractModal(page);

    // 1. Strict JSON Format select
    const strictJson = modal.locator('div:has(> label:text-is("Strict JSON Output")) > select');
    await expect(strictJson).toBeVisible();
    await expect(strictJson).toHaveValue('yes');
    await strictJson.selectOption('no');

    // 2. Timeout Limit (seconds)
    const timeoutInput = modal.locator('div:has(> label:text-is("Timeout (seconds)")) > input');
    await expect(timeoutInput).toBeVisible();
    await timeoutInput.fill('90');

    // Save Contract
    await modal.getByRole('button', { name: 'Save contract' }).click();
    await expect(page.getByRole('heading', { name: 'Edit Contract' })).not.toBeVisible();

    // Reopen to verify both values persisted
    await page.getByRole('button', { name: 'Edit contract' }).click();
    await expect(page.getByRole('heading', { name: 'Edit Contract' })).toBeVisible();
    await expect(strictJson).toHaveValue('no');
    await expect(timeoutInput).toHaveValue('90');
    await modal.getByRole('button', { name: 'Cancel' }).click();
    await expect(page.getByRole('heading', { name: 'Edit Contract' })).not.toBeVisible();

    // 3. Maximum attempts bound lives in Settings as "Retries on failure"
    await page.goto('/#settings');
    await expect(page.getByRole('heading', { name: 'Settings' })).toBeVisible();
    const retriesInput = page.locator('div:has(> div > b:text-is("Retries on failure")) > input');
    await expect(retriesInput).toBeVisible();
    await expect(retriesInput).toHaveAttribute('min', '0');
    await expect(retriesInput).toHaveAttribute('max', '5');
    await retriesInput.fill('3');
    await expect(retriesInput).toHaveValue('3');
  });

  test('Zero-cost verified models in dropdown with reasoning variants (low, high, max)', async ({
    page,
  }) => {
    await openManagerContract(page);
    const modal = contractModal(page);

    // Verify model select contains the free DeepSeek and Muse Spark options
    const modelSelect = modal.locator('div:has(> label:text-is("Model")) > select');
    await expect(modelSelect).toBeVisible();
    await expect(modelSelect.locator('option[value="deepseek-v4.1-flash-free"]')).toBeAttached();
    await expect(modelSelect.locator('option[value="muse-spark-1.3-contributor-free"]')).toBeAttached();

    // Select the DeepSeek free model
    await modelSelect.selectOption('deepseek-v4.1-flash-free');
    await expect(modelSelect).toHaveValue('deepseek-v4.1-flash-free');

    // Verify reasoning variants and choose 'max'
    const variantSelect = modal.locator('div:has(> label:text-is("Reasoning")) > select');
    await expect(variantSelect).toBeVisible();
    await expect(variantSelect.locator('option[value="low"]')).toBeAttached();
    await expect(variantSelect.locator('option[value="high"]')).toBeAttached();
    await expect(variantSelect.locator('option[value="max"]')).toBeAttached();
    await variantSelect.selectOption('max');
    await expect(variantSelect).toHaveValue('max');

    // Cancel without saving
    await modal.getByRole('button', { name: 'Cancel' }).click();
    await expect(page.getByRole('heading', { name: 'Edit Contract' })).not.toBeVisible();
  });
});
