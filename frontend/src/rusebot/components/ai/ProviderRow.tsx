import { useId, useState } from 'react';
import { ChevronDown } from 'lucide-react';
import { cn } from '../../lib/cn';
import type { EngineStateId, UsageByEngine } from '../../data/worker';
import { loadProviderModels, refreshProviders, useProviders, validModelFor, type ProviderEntry } from '../../state/useProviders';
import { STATE_LABEL, versionOf } from '../../state/providerText';
import { EngineMark } from '../ProviderIcons';
import { ReadyPanel } from './ReadyPanel';
import { SetupSteps } from './SetupSteps';
import { useEngineCheck, useEngineTest, useSetupLauncher } from './useProviderActions';

const PILL: Record<EngineStateId, { box: string; dot: string }> = {
  ready: { box: 'bg-success/10 text-success', dot: 'bg-success' },
  unverified: { box: 'bg-control text-ink', dot: 'bg-warning' },
  needs_login: { box: 'bg-control text-ink', dot: 'bg-warning' },
  error: { box: 'bg-danger/10 text-danger', dot: 'bg-danger' },
  not_installed: { box: 'bg-control text-ink-secondary', dot: 'bg-ink-secondary' },
};

export function StatePill({ state }: { state: EngineStateId }) {
  return (
    <span
      data-engine-state={state}
      className={cn('inline-flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded-full px-2.5 py-1 text-[11.5px] font-medium', PILL[state].box)}
    >
      <span aria-hidden="true" className={cn('size-1.5 rounded-full', PILL[state].dot)} />
      {STATE_LABEL[state]}
    </span>
  );
}

export function ProviderRow({ entry, usage }: { entry: ProviderEntry; usage: UsageByEngine | undefined }) {
  const [open, setOpen] = useState(false);
  const panelId = useId();
  const s = useProviders();
  const launcher = useSetupLauncher(entry.id);
  const test = useEngineTest(entry.id);
  const check = useEngineCheck(entry.id, entry.title);
  const version = versionOf(entry.status?.version);
  const ready = entry.state === 'ready' || entry.state === 'unverified';

  // The test model is checked against the provider's current list, never a remembered id that may be gone.
  const runTest = () =>
    void loadProviderModels(entry.id).then((list) => test.run(validModelFor(entry.id, list)));
  const primary: { label: string; busy: boolean; run: () => void } =
    entry.state === 'not_installed'
      ? { label: 'Install', busy: launcher.busy === 'install', run: () => void launcher.run('install') }
      : entry.state === 'needs_login'
        ? { label: 'Sign in', busy: launcher.busy === 'login', run: () => void launcher.run('login') }
        : entry.state === 'error'
          ? { label: 'Check again', busy: s.refreshing, run: () => void refreshProviders(true) }
          : { label: 'Test', busy: test.state.phase === 'running', run: runTest };

  return (
    <li data-engine-card={entry.id} className="min-w-0 rounded-2xl border border-hairline/40 bg-card">
      <div className="relative flex flex-wrap items-center gap-x-3 gap-y-2 p-3 sm:p-4">
        <button
          type="button"
          aria-expanded={open}
          aria-controls={panelId}
          onClick={() => setOpen((v) => !v)}
          className="flex min-w-0 flex-1 basis-[13rem] items-center gap-3 rounded-xl text-left after:absolute after:inset-0 after:rounded-2xl after:content-[''] focus-visible:outline-none focus-visible:after:outline focus-visible:after:outline-2 focus-visible:after:outline-accent"
        >
          <span aria-hidden="true" className="flex size-10 shrink-0 items-center justify-center rounded-xl border border-hairline/30 bg-panel">
            <EngineMark engineId={entry.id} size={22} />
          </span>
          <span className="min-w-0 flex-1">
            <span dir="auto" className="block truncate text-[14.5px] font-semibold tracking-[-0.01em] text-ink" title={entry.title}>
              {entry.title}
            </span>
            <span className="mt-0.5 block truncate text-[12px] text-ink-secondary">
              {[entry.vendor, version ? `v${version}` : null].filter(Boolean).join(' · ')}
            </span>
          </span>
        </button>
        <div className="pointer-events-none relative z-10 ml-auto flex shrink-0 items-center gap-2">
          <StatePill state={entry.state} />
          <button
            type="button"
            disabled={primary.busy}
            aria-label={`${primary.label} ${entry.title}`}
            onClick={() => {
              setOpen(true);
              primary.run();
            }}
            className={cn('pointer-events-auto ui-button ui-button-md', !ready && 'ui-button-primary')}
            data-engine-primary={entry.id}
          >
            {primary.busy ? `${primary.label}…` : primary.label}
          </button>
          <ChevronDown
            size={16}
            aria-hidden="true"
            className={cn('pointer-events-none shrink-0 text-ink-secondary transition-transform motion-reduce:transition-none', open && 'rotate-180')}
          />
        </div>
      </div>
      {open && (
        <div id={panelId} className="min-w-0 border-t border-hairline/40 p-3 sm:p-4">
          {ready ? (
            <ReadyPanel entry={entry} test={test.state} onTest={runTest} check={check.state} onCheck={() => void check.run()} usage={usage} />
          ) : (
            <>
              {check.state.text && (
                <p role="status" className="mb-3 text-[12.5px] text-ink-secondary">
                  {check.state.text}
                </p>
              )}
              <SetupSteps entry={entry} busy={launcher.busy} launch={launcher.result} onLaunch={(step) => void launcher.run(step)} />
            </>
          )}
          {entry.state === 'unverified' && (
            <section aria-label="Sign in" className="mt-5 flex flex-col gap-2 border-t border-hairline/40 pt-4" data-engine-signin={entry.id}>
              <h4 className="text-[12px] font-semibold text-ink-secondary">Not signed in?</h4>
              <SetupSteps
                signInOnly
                entry={entry}
                busy={launcher.busy}
                launch={launcher.result}
                onLaunch={(step) => void launcher.run(step)}
              />
            </section>
          )}
        </div>
      )}
    </li>
  );
}
