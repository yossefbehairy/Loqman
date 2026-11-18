# AgentOS frontend

The React 19 and TypeScript UI of the Loqman desktop app. It is built with Vite 8 and styled with Tailwind CSS 4, and it ships inside the Electron app. In Electron it talks to the Python worker over IPC (`window.agentos`). In a browser it talks to the same worker through a dev-only Vite bridge.

## Scripts

Defined in `package.json`:

| Script | What it does |
| --- | --- |
| `npm run dev` | Starts the Vite dev server on `http://127.0.0.1:5173`. The dev bridge starts the worker (`RUNTIME_MODE`, default `mock`; data in `.agentos-dev-data`). |
| `npm run build` | `tsc -b && vite build`, output in `dist/`. `vite.config.ts` sets `base: './'` so the build loads from `file://` inside Electron. |
| `npm run lint` | `oxlint`. |
| `npm run preview` | Serves the built `dist/` with `vite preview`. |
| `npm test` / `npm run test:e2e` | `playwright test`. |
| `npm run desktop` | Builds, then runs `electron .`. |
| `npm run desktop:dist` | Builds, then runs `electron-builder --win --x64` to produce the NSIS installer in `release/`. It needs `build-resources/agentos-backend.exe` (see `../DESKTOP.md`). |

Node unit tests for the Electron layer are not in `package.json`. Run them from this folder with:

```powershell
node --test electron/transport.test.cjs electron/worker-client.test.cjs electron/bridge.test.cjs
```

## Where the code lives

- `src/main.tsx` mounts the UI from `src/rusebot/entry.tsx`.
- `src/rusebot/` is the UI. `Shell.tsx` holds the layout and navigation. `components/` has the chat, team, results, settings and dialog views, and `components/ai/` the AI providers page. `state/` holds the React state (`useChat`, `useCompany`, `useProviders`). `data/worker.ts` holds the typed wrappers around worker methods. `lib/` has small helpers, and `styles.css` is the Tailwind entry.
- `src/api/transport.ts` provides `call()` and `openStream()`, which both Electron and the dev bridge use.
- `src/types/agentos.ts` holds shared types.
- `electron/` holds the main process (`main.cjs`), the preload bridge (`preload.cjs`), the worker client (`worker-client.cjs`), the splash screen, and the Node tests.
- `vite-agentos-bridge.mjs` is the dev-only Vite plugin. It serves `/__agentos/call` and `/__agentos/stream` and forwards them to the worker.
- `e2e/rusebot_ui.spec.ts` is the only Playwright spec.

The UI's look and layout are ported from RuseBot 0.1.127 under the Apache License 2.0. See `src/rusebot/ATTRIBUTION.md` and `src/rusebot/NOTICE.txt`.

## End-to-end tests in mock mode

`playwright.config.ts` sets up the run:

- Playwright starts `npm run dev` on `http://127.0.0.1:5173`, or reuses a server already running there.
- It sets `RUNTIME_MODE=mock`, so no model is called, and `AGENTOS_DATA_DIR=./.agentos-e2e-data` in the repository root, so the real app database is not touched.
- It runs one Chromium project with one worker and no retries. Traces are kept on the first retry.
- Each test starts in a fresh browser context, with the browser-stored key `agentos_ceo_threads_v1` set to an empty list.

```powershell
npx playwright install chromium   # first time only
npx playwright test
npx playwright test e2e/rusebot_ui.spec.ts
```

The spec has 59 tests at the time of writing (a static count of test definitions).
