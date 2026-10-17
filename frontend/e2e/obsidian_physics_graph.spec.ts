import { test, expect } from '@playwright/test';
import { createTeam } from './helpers';

test.describe('Obsidian-Style Physics Graph (react-force-graph)', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/#company');
    await page.waitForLoadState('networkidle');
  });

  test('should mount react-force-graph canvas with Obsidian physics status indicator', async ({ page }) => {
    // 1. Verify canvas rendered by react-force-graph-2d
    const canvas = page.locator('canvas').first();
    await expect(canvas).toBeVisible();

    // 2. Verify Obsidian bottom indicator
    const indicator = page.locator('text=/Obsidian Physics Graph/i');
    await expect(indicator).toBeVisible();

    // 3. Verify Canvas HUD controls
    await expect(page.locator('button[aria-label="Zoom in"]')).toBeVisible();
    await expect(page.locator('button[aria-label="Zoom out"]')).toBeVisible();
    await expect(page.locator('button[aria-label="Reset camera"]')).toBeVisible();
    await expect(page.locator('button[aria-label="Reheat simulation"]')).toBeVisible();
    await expect(page.locator('button[aria-label="Physics controls"]')).toBeVisible();
  });

  test('should open Obsidian Forces tuning drawer and adjust physics parameters', async ({ page }) => {
    // Click on Forces button
    const forcesBtn = page.locator('button[aria-label="Physics controls"]');
    await forcesBtn.click();

    // Verify Obsidian Physics modal / drawer appears
    await expect(page.locator('text=Obsidian Physics (Forces)')).toBeVisible();
    await expect(page.locator('text=/Repulsion|قوة التنافر/')).toBeVisible();
    await expect(page.locator('text=/Link Distance|طول الروابط/')).toBeVisible();
    await expect(page.locator('text=/Center Gravity|الجاذبية/')).toBeVisible();
    await expect(page.locator('text=Neural Pulse Speed')).toBeVisible();
    await expect(page.locator('text=Always Show Labels')).toBeVisible();

    // Adjust Repulsion slider
    const repulsionSlider = page.locator('input[type="range"]').nth(0);
    await repulsionSlider.fill('-360');

    // Adjust Link Distance slider
    const linkSlider = page.locator('input[type="range"]').nth(1);
    await linkSlider.fill('110');

    // Click Reheat simulation button
    const reheatBtn = page.locator('button:has-text("Reheat Simulation")');
    await reheatBtn.click();

    // Toggle always show labels
    const labelsToggle = page.locator('input[type="checkbox"]');
    await labelsToggle.click();

    // Canvas remains functional and visible
    const canvas = page.locator('canvas').first();
    await expect(canvas).toBeVisible();
  });

  test('should support zooming, reset camera, and clicking reheat button', async ({ page }) => {
    const zoomIn = page.locator('button[aria-label="Zoom in"]');
    const zoomOut = page.locator('button[aria-label="Zoom out"]');
    const resetCam = page.locator('button[aria-label="Reset camera"]');
    const reheat = page.locator('button[aria-label="Reheat simulation"]');

    await zoomIn.click();
    await page.waitForTimeout(200);
    await zoomOut.click();
    await page.waitForTimeout(200);
    await reheat.click();
    await page.waitForTimeout(200);
    await resetCam.click();
    await page.waitForTimeout(300);

    const canvas = page.locator('canvas').first();
    await expect(canvas).toBeVisible();
  });

  test('should interact with department chips and highlight clusters', async ({ page }) => {
    // The workspace starts clean — create two department desks so their
    // focus chips exist.
    await createTeam(page, 'Planning & Strategy', 'د. سارة', 'مدير التخطيط والإستراتيجية');
    await createTeam(page, 'Research & Feasibility', 'م. طارق', 'مدير التحليل والمخاطر');

    // Click Planning & Strategy chip
    const planningChip = page.locator('button:has-text("Planning & Strategy")');
    await expect(planningChip).toBeVisible();
    await planningChip.click();

    // Chip should become active
    await expect(planningChip).toHaveClass(/border-\[var\(--acc\)\]/);

    // Switch to Research & Feasibility
    const researchChip = page.locator('button:has-text("Research & Feasibility")');
    await researchChip.click();
    await expect(researchChip).toHaveClass(/border-\[var\(--acc\)\]/);

    // Click again to un-focus (show all)
    await researchChip.click();
  });
});
