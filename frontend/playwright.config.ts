import { rmSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { defineConfig, devices } from '@playwright/test';

const E2E_ORIGIN = 'http://127.0.0.1:5173';

// Every test creates its own agent and chats, so a shared data folder grows run after run
// (it reached ~700 agents and ~1000 chats, and page loads then outran the 30 s test limit).
// Each run starts from an empty folder of its own. The dev bridge resolves AGENTOS_DATA_DIR
// against the repo root. Only the main process clears and picks the folder: Playwright also
// loads this file in its test workers, which must not delete anything.
const E2E_ROOT = '.agentos-e2e-data';
if (!process.env.TEST_WORKER_INDEX && !process.env.AGENTOS_E2E_RUN) {
  const abs = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', E2E_ROOT);
  try {
    rmSync(abs, { recursive: true, force: true });
  } catch {
    /* a dev server from an earlier run still holds it open: the new run uses its own folder anyway */
  }
  process.env.AGENTOS_E2E_RUN = String(Date.now());
}
const E2E_DATA_DIR = `./${E2E_ROOT}/run-${process.env.AGENTOS_E2E_RUN}`;

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
            // The only browser-stored key the UI reads (earlier CEO chats from the
            // classic interface, src/rusebot/data/legacy.ts). Start every test with none.
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
      // Never reuse a server already on 5173: it may be a real-mode dev server on other data.
      reuseExistingServer: false,
      timeout: 30000,
      // Isolated data dir: e2e must never write into the real app database
      // (and vice versa) — mock events leaked into the live ledger before.
      env: {
        ...process.env,
        RUNTIME_MODE: 'mock',
        AGENTOS_DATA_DIR: E2E_DATA_DIR,
      } as Record<string, string>,
    },
  ],
});
