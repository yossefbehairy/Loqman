/*
 * "AI" settings page (Providers v2). Header with Refresh, the default provider and model
 * for new chats, then one row per provider ordered ready -> unverified -> needs sign-in ->
 * problem -> not installed. State comes from the shared store (state/useProviders.ts), so a
 * Refresh here updates the Composer and the New agent dialog too.
 * Card and header styling follows the RuseBot 0.1.127 EngineLibrary / EnginesSettings
 * (Apache-2.0, Copyright 2026 Milind Soni and OpenMausBot contributors).
 */
import { useEffect, useMemo, useState } from 'react';
import { RefreshCw } from 'lucide-react';
import { cn } from '../lib/cn';
import { usageSummary, type UsageByEngine } from '../data/worker';
import { parseWorkerTime } from '../data/time';
import { refreshProviders, saveDefaultProvider, useProviders } from '../state/useProviders';
import { STATE_ORDER } from '../state/providerText';
import { ProviderPicker } from './ai/ProviderPicker';
import { ProviderRow } from './ai/ProviderRow';

function formatChecked(iso: string | null): string | null {
  if (!iso) return null;
  const d = parseWorkerTime(iso);
  return Number.isNaN(d.getTime()) ? null : d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
}

export function AiProviders() {
  const s = useProviders();
  const [usage, setUsage] = useState<Record<string, UsageByEngine>>({});

  useEffect(() => {
    let alive = true;
    usageSummary()
      .then((u) => {
        if (alive) setUsage(Object.fromEntries((u.by_engine ?? []).map((row) => [row.key, row])));
      })
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, [s.checkedAt]);

  const rows = useMemo(
    () => [...s.statuses].sort((a, b) => STATE_ORDER.indexOf(a.state) - STATE_ORDER.indexOf(b.state)),
    [s.statuses]
  );
  const readyCount = rows.filter((r) => r.state === 'ready').length;
  const checked = formatChecked(s.checkedAt);

  return (
    <section data-settings-block="ai" className="flex min-w-0 flex-col gap-5">
      <div className="flex flex-wrap items-start justify-between gap-3 px-1">
        <div className="min-w-0">
          <h2 className="text-[22px] font-semibold tracking-[-0.02em] text-ink">Model providers and accounts</h2>
          <p className="mt-1.5 max-w-[34rem] text-[13px] leading-relaxed text-ink-secondary">
            The AI tools that run your agents. Each one uses your own account.
          </p>
        </div>
        <div className="flex shrink-0 flex-col items-start gap-1 sm:items-end">
          <button
            type="button"
            disabled={s.refreshing}
            onClick={() => void refreshProviders(true)}
            className="ui-button ui-button-md"
            data-providers-refresh
          >
            <RefreshCw size={13} aria-hidden="true" className={cn(s.refreshing && 'animate-spin motion-reduce:animate-none')} />
            {s.refreshing ? 'Checking…' : 'Refresh'}
          </button>
          <p role="status" className="min-h-4 text-[11.5px] text-ink-secondary">
            {s.refreshing ? '' : checked ? `Checked at ${checked}` : ''}
          </p>
        </div>
      </div>

      <div className="rounded-xl bg-card p-4" data-providers-default>
        <div className="text-[14px] font-medium text-ink">Default for new chats</div>
        <p className="mb-3 mt-0.5 text-[12px] leading-relaxed text-ink-secondary">
          Used by the chat box, by Loqman, for missions and as the starting point for new agents. You can still change it per chat.
        </p>
        <ProviderPicker idPrefix="ai-default" value={s.choice} onChange={(next) => void saveDefaultProvider(next)} />
        {s.defaultError && (
          <p role="alert" className="mt-2 text-[12px] text-danger">
            Could not save the default. {s.defaultError}
          </p>
        )}
      </div>

      <div className="flex min-w-0 flex-col gap-3">
        <div className="flex items-baseline justify-between gap-3 px-1">
          <h3 className="text-[12px] font-semibold text-ink-secondary">Providers</h3>
          {rows.length > 0 && (
            <span className="text-[11.5px] tabular-nums text-ink-secondary">
              {readyCount} of {rows.length} ready
            </span>
          )}
        </div>
        {s.loading && rows.length === 0 && (
          <p role="status" className="px-1 text-[13px] text-ink-secondary">
            Looking for providers…
          </p>
        )}
        {s.error && (
          <div role="alert" className="flex flex-wrap items-center gap-3 px-1 text-[13px] text-danger">
            <span>Could not check the providers. {s.error}</span>
            <button type="button" className="ui-button ui-button-sm text-ink" onClick={() => void refreshProviders(true)}>
              Try again
            </button>
          </div>
        )}
        {!s.loading && !s.error && rows.length === 0 && (
          <p className="px-1 text-[13px] text-ink-secondary">No providers were found on this computer.</p>
        )}
        {rows.length > 0 && (
          <ul className="flex min-w-0 flex-col gap-2">
            {rows.map((entry) => (
              <ProviderRow key={entry.id} entry={entry} usage={usage[entry.id]} />
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}
