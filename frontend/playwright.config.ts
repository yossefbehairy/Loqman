import { defineConfig, devices } from '@playwright/test';

const E2E_ORIGIN = 'http://127.0.0.1:5173';

// The product now ships with two seeded teams (Compass + Forge). The e2e suite
// is written against a clean workspace (one CEO, no teams) and creates its own
// departments through the UI, so every test context starts from that baseline.
// `agentos_org_seed_v1` opts out of the one-time Loqman-org migration.
const E2E_CLEAN_WORKSPACE = {
  id: 'a1',
  name: 'Loqman',
  role: 'الرئيس التنفيذي · CEO',
  team: null,
  mgr: true,
  model: 'deepseek-v4.1-flash-free',
  reason: 'low',
  timeout: 180,
  json: true,
  prompt: 'You are Loqman (لقمان), CEO of the company.',
  status: 'Active',
  profileId: 'custom',
};

export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: 0,
  workers: 1,
  reporter: 'list',
  use: {
    baseURL: E2E_ORIGIN,
    trace: 'on-first-retry',
    headless: true,
    storageState: {
      cookies: [],
      origins: [
        {
          origin: E2E_ORIGIN,
          localStorage: [
            { name: 'agentos_org_seed_v1', value: '1' },
            { name: 'agentos_teams_v2', value: '[]' },
            { name: 'agentos_agents_v2', value: JSON.stringify([E2E_CLEAN_WORKSPACE]) },
            { name: 'agentos_ceo_threads_v1', value: '[]' },
          ],
        },
      ],
    },
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
  webServer: [
    {
      // `vite dev` hosts the dev bridge that spawns the Python worker (stdin/stdout, no HTTP backend).
      command: 'npm run dev',
      url: 'http://127.0.0.1:5173',
      reuseExistingServer: true,
      timeout: 30000,
      // Isolated data dir: e2e must never write into the real app database
      // (and vice versa) — mock events leaked into the live ledger before.
      env: {
        ...process.env,
        RUNTIME_MODE: 'mock',
        AGENTOS_DATA_DIR: './.agentos-e2e-data',
      } as Record<string, string>,
    },
  ],
});
