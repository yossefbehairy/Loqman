# AgentOS — Test Suite Readiness (superseded)

This file used to report the readiness of the Playwright suite for the removed interface: eight spec files with 43 tests, per-spec run commands and a last-run result. That interface and those specs were deleted, so the report no longer applies.

Where the current information is:

- Run commands and test counts: `README.md` (Testing) and `frontend/README.md`.
- Test layers, what each covers and the quality gates: `TEST_INFRA.md`.
- The only end-to-end suite: `frontend/e2e/rusebot_ui.spec.ts`, run with `npx playwright test` from `frontend/`. It runs against the mock runtime (`RUNTIME_MODE=mock`) through the Vite dev bridge.

Counts are static counts of test definitions at the time of writing, not recorded run results.
