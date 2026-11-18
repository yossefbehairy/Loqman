/* Plain-English wording for provider states and errors, shared by Settings, the Composer and the New agent dialog. */
import type { EngineCheckResult, EngineStateId, EngineStatus } from '../data/worker';

export const STATE_LABEL: Record<EngineStateId, string> = {
  ready: 'Ready',
  unverified: 'Sign-in not confirmed',
  needs_login: 'Sign in required',
  error: 'Problem',
  not_installed: 'Not installed',
};

/** Short reason used in option labels where a state is not selectable. */
export const STATE_SHORT: Record<EngineStateId, string> = {
  ready: 'ready',
  unverified: 'sign-in not confirmed',
  needs_login: 'sign-in required',
  error: 'problem',
  not_installed: 'not installed',
};

export const STATE_ORDER: EngineStateId[] = ['ready', 'unverified', 'needs_login', 'error', 'not_installed'];

const STATES = new Set<string>(STATE_ORDER);

/** The worker's `state`, or the same rules applied to install_state / auth_state when it is missing. */
export function deriveState(e: Pick<EngineStatus, 'state' | 'install_state' | 'auth_state'>): EngineStateId {
  if (e.state && STATES.has(e.state)) return e.state;
  if (e.install_state === 'installed') {
    if (e.auth_state === 'authenticated') return 'ready';
    if (e.auth_state === 'unauthenticated') return 'needs_login';
    return 'unverified';
  }
  if (e.install_state === 'unknown') return 'error';
  return 'not_installed';
}

/** A provider the user can pick for a chat or an agent. "unverified" may still work. */
export function selectable(state: EngineStateId): boolean {
  return state === 'ready' || state === 'unverified';
}

const FALLBACK_TITLES: Record<string, { title: string; vendor: string }> = {
  'claude-code': { title: 'Claude', vendor: 'Anthropic' },
  codex: { title: 'ChatGPT (Codex)', vendor: 'OpenAI' },
  opencode: { title: 'OpenCode', vendor: 'OpenCode' },
  antigravity: { title: 'Gemini (Antigravity)', vendor: 'Google' },
  mock: { title: 'mock (test mode)', vendor: 'Built in' },
};

export function titleOf(engineId: string, status?: Pick<EngineStatus, 'title' | 'vendor' | 'display_name' | 'name'> | null) {
  const fb = FALLBACK_TITLES[engineId];
  return {
    title: status?.title ?? fb?.title ?? status?.display_name ?? status?.name ?? engineId,
    vendor: status?.vendor ?? fb?.vendor ?? '',
  };
}

/** "1.2.3" out of strings like "claude 1.2.3 (Claude Code)". */
export function versionOf(version: string | null | undefined): string | null {
  return version?.match(/\d+\.\d+(?:\.\d+)?(?:[-+][\w.-]+)?/)?.[0] ?? null;
}

/** Turn a worker error code (or a transport message) into something a person can act on. Never returns a bare code. */
export function explainError(code: string | null | undefined, message?: string | null): string {
  const tokens = (code ?? '').toLowerCase().split(/[^a-z0-9]+/).filter(Boolean);
  const has = (...words: string[]) => tokens.some((t) => words.some((w) => t === w || t.startsWith(w)));
  if (has('timeout', 'timed', 'deadline')) return 'The provider took too long to answer. Try again in a moment.';
  if (has('rate', 'quota', 'limit', 'exhaust', 'billing', 'credit'))
    return 'The provider says a rate limit or quota was reached. Wait a while or check your plan.';
  if (has('auth', 'unauth', 'login', 'credential', 'token', 'forbidden', '401', '403'))
    return 'You are not signed in, or the sign-in expired. Sign in again, then retry.';
  if (has('model')) return 'That model is not available on this account. Pick another model and retry.';
  if (has('unsupported', 'supported', 'adapter')) return 'This provider does not support that yet.';
  if (has('not', 'notfound', 'missing', 'offline', 'unreachable', 'spawn', 'enoent', 'connect', 'network'))
    return 'The provider could not be reached. Check that it is installed and that you are online.';
  const text = (message ?? '').trim();
  return text ? `The provider reported a problem: ${text.slice(0, 200)}` : 'The provider reported a problem. Try again, or check its sign-in.';
}

export function formatDuration(ms: number): string {
  if (!Number.isFinite(ms) || ms < 0) return '';
  return ms < 1000 ? `${Math.round(ms)} ms` : `${(ms / 1000).toFixed(ms < 10_000 ? 1 : 0)} s`;
}

/** The one sentence shown after "Check connection": discovery first (is the tool still there and signed in?), then the runtime's own health. */
export function explainCheck(out: EngineCheckResult, title: string): string {
  if (out.error_code) return explainError(out.error_code);
  const state = out.discovery ? deriveState(out.discovery) : null;
  if (state === 'not_installed') return `${title} is no longer installed. Install it again, then check once more.`;
  if (state === 'error') return `${title} could not be checked. Make sure it is installed correctly, then check again.`;
  if (state === 'needs_login') return `${title} is installed, but you are signed out. Sign in, then check again.`;
  const status = out.runtime?.status;
  if (!out.runtime) return `${title} is installed, but it did not report its health. Try again, or check its sign-in.`;
  if (status === 'offline') return `${title} is installed, but it did not respond. Check that you are online, then try again.`;
  if (status === 'degraded') return `${title} responds, but it reports a problem, so some models may not work. Send a test message to find out.`;
  if (status !== 'ok') return `${title} is installed, but it did not report a clear status. Send a test message to find out.`;
  return state === 'unverified'
    ? 'Connected. The tool responds, but your sign-in is not confirmed. Send a test message to confirm it can answer.'
    : 'Connected. The tool responds. Send a test message to confirm it can answer.';
}
