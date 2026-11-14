import { useEffect, useState, type ReactNode } from 'react';
import { ArrowUpRight } from 'lucide-react';
import { engineSetup, type EngineSetupInfo } from '../../data/worker';
import { messageOf } from '../../state/useChat';
import { refreshProviders, type ProviderEntry } from '../../state/useProviders';
import { STATE_LABEL } from '../../state/providerText';
import { CopyCommand } from './CopyCommand';
import type { LaunchResult, SetupStep } from './useProviderActions';

type Loaded = { key: string; info: EngineSetupInfo | null; error: string | null };

function safeUrl(url: string | null | undefined): string | null {
  return url && /^https?:\/\//i.test(url) ? url : null;
}

function ExternalLink({ href, children }: { href: string; children: ReactNode }) {
  return (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      className="inline-flex items-center gap-1 text-[12.5px] font-medium text-accent-text hover:underline"
    >
      {children}
      <ArrowUpRight size={13} aria-hidden="true" />
    </a>
  );
}

function Step({ n, title, children }: { n: number; title: string; children: ReactNode }) {
  return (
    <li className="flex min-w-0 gap-3">
      <span
        aria-hidden="true"
        className="mt-0.5 flex size-6 shrink-0 items-center justify-center rounded-full bg-control text-[12px] font-semibold tabular-nums text-ink"
      >
        {n}
      </span>
      <div className="flex min-w-0 flex-1 flex-col gap-2">
        <div className="text-[13.5px] font-medium text-ink">{title}</div>
        {children}
      </div>
    </li>
  );
}

function LaunchNote({ result, step }: { result: LaunchResult | null; step: SetupStep }) {
  if (!result || result.step !== step) return null;
  const text = result.launched
    ? 'A terminal window was opened. Finish there, then come back and check again.'
    : result.failure
      ? `Could not open a terminal: ${result.failure}`
      : `Could not open a terminal from here${result.reason ? ` (${result.reason})` : ''}. Copy the command and run it yourself.`;
  return (
    <p role="status" className="text-[12.5px] text-ink-secondary">
      {text}
    </p>
  );
}

/**
 * Numbered setup steps for a provider that is not ready. Only the steps that still apply are shown.
 * `signInOnly` is the secondary section of an unverified provider: just the sign-in command and a re-check.
 */
export function SetupSteps({
  entry,
  busy,
  launch,
  onLaunch,
  signInOnly = false,
}: {
  signInOnly?: boolean;
  entry: ProviderEntry;
  busy: SetupStep | null;
  launch: LaunchResult | null;
  onLaunch: (step: SetupStep) => void;
}) {
  const [attempt, setAttempt] = useState(0);
  const [loaded, setLoaded] = useState<Loaded | null>(null);
  const [checking, setChecking] = useState(false);
  const [checkedOnce, setCheckedOnce] = useState(false);
  const key = `${entry.id}:${entry.state}:${attempt}`;

  useEffect(() => {
    let alive = true;
    engineSetup(entry.id)
      .then((info) => alive && setLoaded({ key, info, error: null }))
      .catch((e: unknown) => alive && setLoaded({ key, info: null, error: messageOf(e) }));
    return () => {
      alive = false;
    };
  }, [entry.id, key]);

  const current = loaded?.key === key ? loaded : null;
  const info = current?.info ?? null;
  const installCmd = info?.install?.command?.join(' ') ?? null;
  const installUrl = safeUrl(info?.install?.url);
  const docsUrl = safeUrl(info?.docs_url);
  const loginCmd = (info?.login?.command ?? info?.command)?.join(' ') ?? null;
  const needInstall = !signInOnly && (entry.state === 'not_installed' || entry.state === 'error');
  const needLogin = signInOnly || entry.state === 'needs_login' || (entry.state === 'not_installed' && Boolean(loginCmd));

  const checkAgain = async () => {
    setChecking(true);
    try {
      await refreshProviders(true);
    } finally {
      setChecking(false);
      setCheckedOnce(true);
    }
  };

  const steps: Array<{ id: string; title: string; body: ReactNode }> = [];
  if (needInstall) {
    steps.push({
      id: 'install',
      title: `Install ${entry.title}`,
      body: (
        <>
          {installCmd && <CopyCommand command={installCmd} />}
          <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
            {installCmd && (
              <button
                type="button"
                disabled={busy !== null}
                onClick={() => onLaunch('install')}
                className="ui-button ui-button-sm"
                data-engine-open-setup={`${entry.id}:install`}
              >
                {busy === 'install' ? 'Opening…' : 'Open terminal'}
              </button>
            )}
            {installUrl && <ExternalLink href={installUrl}>Download page</ExternalLink>}
            {docsUrl && <ExternalLink href={docsUrl}>Documentation</ExternalLink>}
          </div>
          {!installCmd && !installUrl && !docsUrl && (
            <p className="text-[12.5px] text-ink-secondary">Install it from the provider&apos;s own website, then check again.</p>
          )}
          <LaunchNote result={launch} step="install" />
        </>
      ),
    });
  }
  if (needLogin) {
    steps.push({
      id: 'login',
      title: `Sign in to ${entry.title}`,
      body: (
        <>
          {loginCmd && <CopyCommand command={loginCmd} />}
          <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
            {loginCmd && (
              <button
                type="button"
                disabled={busy !== null}
                onClick={() => onLaunch('login')}
                className="ui-button ui-button-sm"
                data-engine-open-setup={`${entry.id}:login`}
              >
                {busy === 'login' ? 'Opening…' : 'Open terminal'}
              </button>
            )}
            {!loginCmd && docsUrl && <ExternalLink href={docsUrl}>Documentation</ExternalLink>}
          </div>
          {!loginCmd && !docsUrl && <p className="text-[12.5px] text-ink-secondary">Open the app and sign in with your account.</p>}
          <LaunchNote result={launch} step="login" />
        </>
      ),
    });
  }
  steps.push({
    id: 'check',
    title: 'Check again',
    body: (
      <div className="flex flex-wrap items-center gap-3">
        <button type="button" disabled={checking} onClick={() => void checkAgain()} className="ui-button ui-button-sm">
          {checking ? 'Checking…' : 'Check again'}
        </button>
        {checkedOnce && !checking && (
          <span role="status" className="text-[12.5px] text-ink-secondary">
            Checked. Status: {STATE_LABEL[entry.state]}.
          </span>
        )}
      </div>
    ),
  });

  return (
    <div className="flex flex-col gap-3">
      {!current && <p className="text-[12.5px] text-ink-secondary">Loading setup steps…</p>}
      {current?.error && (
        <div role="alert" className="flex flex-wrap items-center gap-3 text-[12.5px] text-danger">
          <span>Could not load the setup steps. {current.error}</span>
          <button type="button" className="ui-button ui-button-sm text-ink" onClick={() => setAttempt((a) => a + 1)}>
            Try again
          </button>
        </div>
      )}
      {!signInOnly && info?.instructions && <p className="text-[12.5px] leading-relaxed text-ink-secondary">{info.instructions}</p>}
      {current && (
        <ol className="flex flex-col gap-4">
          {steps.map((s, i) => (
            <Step key={s.id} n={i + 1} title={s.title}>
              {s.body}
            </Step>
          ))}
        </ol>
      )}
    </div>
  );
}
