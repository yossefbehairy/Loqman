import { defineConfig, devices } from '@playwright/test';

// Windows uses the `py` launcher; Linux/macOS (CI) use plain `python`.
const backendCmd =
  process.platform === 'win32'
    ? 'py -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000'
    : 'python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000';

export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: 0,
  workers: 1,
  reporter: 'list',
  use: {
    baseURL: 'http://127.0.0.1:5173',
    trace: 'on-first-retry',
    headless: true,
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
  webServer: [
    {
      command: 'npm run dev',
      url: 'http://127.0.0.1:5173',
      reuseExistingServer: true,
      timeout: 30000,
    },
    {
      command: backendCmd,
      url: 'http://127.0.0.1:8000/api/runtime/health',
      reuseExistingServer: true,
      timeout: 30000,
      cwd: '..',
      env: { ...process.env, RUNTIME_MODE: 'mock' } as Record<string, string>,
    },
  ],
});
