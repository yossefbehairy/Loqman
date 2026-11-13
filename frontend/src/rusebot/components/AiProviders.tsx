/*
 * "AI" settings page. Card, grouping and header markup are the RuseBot 0.1.127
 * EngineLibrary.tsx / EnginesSettings.tsx ones (Apache-2.0, Copyright 2026 Milind Soni
 * and OpenMausBot contributors): "Model providers and accounts", a "Check again" action,
 * Ready / Needs setup groups of expandable provider cards. Data comes from the AgentOS
 * worker (engines.status / engines.check / engines.models / engines.setup).
 */
import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react';
import { ArrowUpRight, Check, ChevronDown, Copy, RefreshCw } from 'lucide-react';
import { cn } from '../lib/cn';
import { EngineMark } from './ProviderIcons';
import {
  engineCheck,
  engineModels,
  engineSetup,
  engineStatuses,
  type EngineModel,
  type EngineSetupInfo,
  type EngineStatus,
} from '../data/worker';
import { messageOf } from '../state/useChat';

const TITLES: Record<string, { title: string; provider: string }> = {
  'claude-code': { title: 'Claude', provider: 'Anthropic' },
  codex: { title: 'ChatGPT', provider: 'OpenAI' },
  opencode: { title: 'OpenCode', provider: 'OpenCode' },
  antigravity: { title: 'Google Gemini (Antigravity)', provider: 'Google' },
};

function titleOf(e: EngineStatus) {
  return TITLES[e.engine_id] ?? { title: e.display_name ?? e.name ?? e.engine_id, provider: e.engine_id };
}

export function engineReady(e: EngineStatus): boolean {
  return e.install_state === 'installed' && e.auth_state !== 'unauthenticated';
}

function statusLabel(e: EngineStatus): string {
  if (engineReady(e)) return 'Ready';
  if (e.install_state === 'installed') return 'Sign-in required';
  return 'Needs setup';
}

function versionOf(e: EngineStatus): string | null {
  return e.version?.match(/\d+\.\d+(?:\.\d+)?(?:[-+][\w.-]+)?/)?.[0] ?? null;
}

function Detail({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="grid grid-cols-[110px_minmax(0,1fr)] gap-3 text-[12.5px]">
      <dt className="text-ink-secondary">{label}</dt>
      <dd className="min-w-0 break-words text-ink">{children}</dd>
    </div>
  );
}

function CopyCommand({ command }: { command: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="flex items-center gap-2 rounded-lg bg-inset px-3 py-2">
      <code className="min-w-0 flex-1 overflow-x-auto whitespace-nowrap text-[12.5px] text-ink">{command}</code>
      <button
        type="button"
        aria-label="Copy command"
        title="Copy command"
        onClick={() => {
          navigator.clipboard
            .writeText(command)
            .then(() => {
              setCopied(true);
              window.setTimeout(() => setCopied(false), 1400);
            })
            .catch(() => undefined);
        }}
        className="flex size-7 shrink-0 items-center justify-center rounded-md text-ink-secondary hover:bg-raised hover:text-ink"
      >
        {copied ? <Check size={14} /> : <Copy size={14} />}
      </button>
    </div>
  );
}

function EngineBody({ engine, onChecked }: { engine: EngineStatus; onChecked: () => void }) {
  const [check, setCheck] = useState<string | null>(null);
  const [models, setModels] = useState<EngineModel[] | null>(null);
  const [setup, setSetup] = useState<EngineSetupInfo | null>(null);
  const [busy, setBusy] = useState(false);
  const ready = engineReady(engine);

  useEffect(() => {
    if (ready) return;
    let alive = true;
    engineSetup(engine.engine_id)
      .then((s) => alive && setSetup(s))
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, [engine.engine_id, ready]);

  const run = async () => {
    setBusy(true);
    setCheck('Checking…');
    try {
      const out = await engineCheck(engine.engine_id);
      setCheck(out.error_code ? `Not ready (${out.error_code})` : 'Connected');
      setModels(await engineModels(engine.engine_id).catch(() => [] as EngineModel[]));
      onChecked();
    } catch (e) {
      setCheck(messageOf(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex flex-col gap-4">
      <dl className="flex flex-col gap-2">
        <Detail label="Provider">{titleOf(engine).provider}</Detail>
        <Detail label="Status">{statusLabel(engine)}</Detail>
        {engine.version && <Detail label="Version">{engine.version}</Detail>}
        {engine.executable && <Detail label="Installed at">{engine.executable}</Detail>}
        {engine.detail && <Detail label="Details">{engine.detail}</Detail>}
      </dl>
      {!ready && setup && (
        <div className="flex flex-col gap-2">
          <p className="text-[12.5px] text-ink-secondary">{setup.instructions}</p>
          {setup.command && <CopyCommand command={setup.command.join(' ')} />}
        </div>
      )}
      <div className="flex flex-wrap items-center gap-3">
        <button type="button" disabled={busy} className="ui-button ui-button-sm" onClick={() => void run()} data-engine-check={engine.engine_id}>
          Check connection
        </button>
        {check && (
          <span role="status" className="text-[12.5px] text-ink-secondary">
            {check}
          </span>
        )}
      </div>
      {models && (
        <ul className="flex max-h-40 flex-col gap-1 overflow-y-auto text-[12.5px] text-ink-secondary">
          {models.slice(0, 60).map((m) => (
            <li key={m.id} className="truncate">
              {m.name}
            </li>
          ))}
          {models.length === 0 && <li>No models reported.</li>}
        </ul>
      )}
    </div>
  );
}

function EngineCard({ engine, onChecked }: { engine: EngineStatus; onChecked: () => void }) {
  const ready = engineReady(engine);
  const { title, provider } = titleOf(engine);
  const version = versionOf(engine);
  return (
    <details
      data-engine-card={engine.engine_id}
      className="group/engine min-w-0 rounded-2xl border border-hairline/40 bg-card transition-colors open:col-span-full open:border-hairline/70 hover:border-hairline/70"
    >
      <summary className="cursor-pointer list-none rounded-2xl p-4 outline-none focus-visible:ring-2 focus-visible:ring-accent [&::-webkit-details-marker]:hidden">
        <div className="flex min-w-0 items-center gap-3">
          <span aria-hidden="true" className="flex size-12 shrink-0 items-center justify-center rounded-xl border border-hairline/30 bg-panel">
            <EngineMark engineId={engine.engine_id} size={28} />
          </span>
          <div className="min-w-0 flex-1">
            <div className="truncate text-[15px] font-semibold tracking-[-0.015em] text-ink" title={title}>
              {title}
            </div>
            <div className="mt-1 truncate text-[12px] text-ink-secondary" title={provider}>
              {provider}
            </div>
          </div>
          <ChevronDown size={16} aria-hidden="true" className="shrink-0 text-ink-secondary transition-transform group-open/engine:rotate-180 motion-reduce:transition-none" />
        </div>
        <div className="mt-4 flex flex-wrap items-center justify-between gap-2">
          <span
            className={cn(
              'inline-flex items-center gap-1.5 rounded-full px-2 py-1 text-[11px] font-medium',
              ready ? 'bg-success/10 text-success' : 'bg-control text-ink-secondary'
            )}
          >
            {ready ? <Check size={12} aria-hidden="true" /> : <span className="size-1.5 rounded-full bg-warning" aria-hidden="true" />}
            {statusLabel(engine)}
          </span>
          {ready ? (
            <span className="text-[11px] tabular-nums text-ink-secondary">{version ? `v${version}` : 'Manage'}</span>
          ) : (
            <span className="inline-flex items-center gap-1 text-[12px] font-semibold text-accent-text">
              Set up
              <ArrowUpRight size={13} aria-hidden="true" />
            </span>
          )}
        </div>
      </summary>
      <div className="min-w-0 border-t border-hairline/40 p-4">
        <EngineBody engine={engine} onChecked={onChecked} />
      </div>
    </details>
  );
}

export function AiProviders() {
  const [engines, setEngines] = useState<EngineStatus[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [checked, setChecked] = useState(false);
  const alive = useRef(true);

  const load = useCallback(async () => {
    try {
      const list = await engineStatuses();
      if (alive.current) {
        setEngines(list);
        setError(null);
      }
    } catch (e) {
      if (alive.current) setError(messageOf(e));
    }
  }, []);

  useEffect(() => {
    alive.current = true;
    void load();
    return () => {
      alive.current = false;
    };
  }, [load]);

  const refresh = async () => {
    setBusy(true);
    setChecked(false);
    await load();
    setBusy(false);
    setChecked(true);
  };

  const groups = [true, false].map((ready) => ({ ready, rows: (engines ?? []).filter((e) => engineReady(e) === ready) }));

  return (
    <section data-settings-block="ai" className="flex flex-col gap-4">
      <div className="px-1">
        <div className="text-[11px] font-semibold uppercase tracking-[0.08em] text-ink-secondary">Model providers</div>
        <div className="mt-3 flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <h2 className="text-[22px] font-semibold tracking-[-0.02em] text-ink">Model providers and accounts</h2>
            <p className="mt-2 max-w-[34rem] text-[13px] leading-relaxed text-ink-secondary">
              Connect the AI tools that power your agents. Accounts, setup and updates, all in one place.
            </p>
          </div>
          <div>
            <button
              type="button"
              disabled={busy}
              onClick={() => void refresh()}
              className="inline-flex shrink-0 items-center gap-1.5 rounded-lg px-2.5 py-2 text-[12px] font-medium text-ink-secondary hover:bg-control hover:text-ink focus-visible:outline-2 focus-visible:outline-accent disabled:opacity-50"
            >
              <RefreshCw size={13} aria-hidden="true" className={cn(busy && 'animate-spin')} />
              {busy ? 'Checking…' : 'Check again'}
            </button>
            {checked && (
              <p role="status" className="mt-1 text-right text-[11px] text-ink-secondary">
                Checked just now.
              </p>
            )}
          </div>
        </div>
      </div>
      {error && (
        <p role="alert" className="px-4 text-[13px] text-danger">
          {error}
        </p>
      )}
      {!engines && !error && <p className="px-4 text-[13px] text-ink-secondary">Looking for engines…</p>}
      {engines && (
        <div className="grid grid-cols-[repeat(auto-fit,minmax(min(100%,280px),1fr))] items-start gap-3">
          {groups.flatMap(({ ready, rows }) =>
            rows.length === 0
              ? []
              : [
                  <div
                    key={`heading-${ready}`}
                    className={cn('col-span-full flex items-center justify-between gap-3 px-4', !ready && groups[0]!.rows.length > 0 && 'mt-4')}
                  >
                    <h3 className="text-[12px] font-semibold text-ink-secondary">{ready ? 'Ready' : 'Needs setup'}</h3>
                    <span className="text-[11px] tabular-nums text-ink-secondary">
                      {rows.length} model {rows.length === 1 ? 'provider' : 'providers'}
                    </span>
                  </div>,
                  ...rows.map((e) => <EngineCard key={e.engine_id} engine={e} onChecked={() => void load()} />),
                ]
          )}
          {engines.length === 0 && <p className="col-span-full py-4 text-[13px] text-ink-secondary">No engines were found on this computer.</p>}
        </div>
      )}
    </section>
  );
}
