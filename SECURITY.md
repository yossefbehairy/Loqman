# Security Policy

## Supported Versions

| Version | Supported |
| ------- | --------- |
| 0.2.x   | ✅        |
| < 0.2   | ❌        |

## Design Principles

Loqman (AgentOS) is **local-first**:

- Backend binds to `127.0.0.1` only by default. Never bind to `0.0.0.0` unless you
  understand the exposure and protect it with auth + firewall rules.
- SQLite lives on the local machine (`agentos.db` in the app data dir in desktop
  mode). Database files (`*.db`, `*.db-wal`, `*.db-shm`) are **never committed**.
- Environment files (`.env`) are **never committed**. Use `.env.example` as template.
- The AI runtime (OpenCode on `127.0.0.1:49100`) is expected on the same machine
  and is **not bundled** with the installer.
- Electron renderer runs with `contextIsolation: ON`, `nodeIntegration: OFF`.
  The preload bridge exposes only `window.__AGENTOS_API__` and
  `window.__AGENTOS_DESKTOP__`. All network calls must go through
  `frontend/src/api/client.ts`.
- CORS allowlist is restricted to local Vite ports + `null` (file:// origin).
  Do not add wildcards or public origins.
- Runtime errors that may contain credentials are redacted via `_redact()`
  in `backend/app/runtime/opencode_runtime.py`. Keep that behavior for any new
  error logging near model transport.

## Reporting a Vulnerability

1. **Do not** open a public issue for security bugs.
2. Open a private security advisory on GitHub
   (Repo → Security → Advisories → New draft advisory),
   or email the maintainer with `[SECURITY]` in the subject.
3. Include: affected version, reproduction steps, impact, and any logs
   (redact tokens/keys first).

We aim to acknowledge within 72 hours and ship a fix + disclosure note.

## Hardening Checklist (for contributors)

- [ ] No secrets/keys/tokens in code, tests, or fixtures.
- [ ] No new `0.0.0.0` binds, no `allow_origins=["*"]`.
- [ ] New backend endpoints validate input with Pydantic schemas.
- [ ] No `eval`/`exec`/shell injection; subprocess calls use arg arrays, never strings.
- [ ] Frontend handles untrusted model output as text (no `dangerouslySetInnerHTML`
      without sanitization).
- [ ] `npm audit` / `pip audit` clean for new dependencies.
