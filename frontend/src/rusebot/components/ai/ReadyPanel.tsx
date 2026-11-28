import { useEffect, useMemo, useState } from 'react';
import { Check } from 'lucide-react';
import { cn } from '../../lib/cn';
import type { EngineModel, UsageByEngine } from '../../data/worker';
import {
  loadProviderModels,
  setPreferredModel,
  useProviders,
  validModelFor,
  type ProviderEntry,
} from '../../state/useProviders';
import { explainError, formatDuration } from '../../state/providerText';
import type { CheckState, TestState } from './useProviderActions';

const FILTER_FROM = 9;
const NO_MODELS: EngineModel[] = [];

function Models({ entry }: { entry: ProviderEntry }) {
  const s = useProviders();
  const [filter, setFilter] = useState('');
  const slot = s.models[entry.id];

  useEffect(() => {
    void loadProviderModels(entry.id);
  }, [entry.id]);

  const list = slot?.list ?? NO_MODELS;
  const current = validModelFor(entry.id, list);
  const shown = useMemo(() => {
    const q = filter.trim().toLowerCase();
    return q ? list.filter((m) => `${m.name} ${m.id}`.toLowerCase().includes(q)) : list;
  }, [list, filter]);

  return (
    <section aria-label="Models" className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h4 className="text-[12px] font-semibold text-ink-secondary">
          Models{slot?.status === 'ready' && list.length > 0 ? <span className="ml-1.5 font-normal tabular-nums">{list.length}</span> : null}
        </h4>
        {slot?.status === 'ready' && list.length >= FILTER_FROM && (
          <input
            type="search"
            dir="auto"
            aria-label={`Filter ${entry.title} models`}
            placeholder="Filter"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            className="w-full min-w-0 rounded-lg border border-hairline/40 bg-inset px-2.5 py-1 text-[12.5px] text-ink placeholder:text-ink-secondary sm:w-44"
          />
        )}
      </div>
      {(!slot || slot.status === 'loading') && <p className="text-[12.5px] text-ink-secondary">Loading models…</p>}
      {slot?.status === 'error' && (
        <div role="alert" className="flex flex-wrap items-center gap-3 text-[12.5px] text-danger">
          <span>Could not load the models. {slot.error}</span>
          <button type="button" className="ui-button ui-button-sm text-ink" onClick={() => void loadProviderModels(entry.id, true)}>
            Try again
          </button>
        </div>
      )}
      {slot?.status === 'ready' && list.length === 0 && <p className="text-[12.5px] text-ink-secondary">This provider reported no models.</p>}
      {slot?.status === 'ready' && list.length > 0 && shown.length === 0 && (
        <p className="text-[12.5px] text-ink-secondary">No model matches &ldquo;{filter}&rdquo;.</p>
      )}
      {shown.length > 0 && (
        <ul className="flex max-h-56 flex-col gap-0.5 overflow-y-auto rounded-lg bg-inset p-1">
          {shown.map((m) => (
            <ModelRow key={m.id} model={m} selected={m.id === current} onPick={() => setPreferredModel(entry.id, m.id)} />
          ))}
        </ul>
      )}
    </section>
  );
}

function ModelRow({ model, selected, onPick }: { model: EngineModel; selected: boolean; onPick: () => void }) {
  const variants = model.variants ?? [];
  return (
    <li>
      <button
        type="button"
        aria-pressed={selected}
        data-engine-model={model.id}
        onClick={onPick}
        className={cn(
          'flex w-full min-w-0 items-center gap-2 rounded-md px-2.5 py-1.5 text-left text-[12.5px] hover:bg-control/70',
          selected ? 'bg-control text-ink' : 'text-ink-secondary hover:text-ink'
        )}
      >
        <span dir="auto" className="min-w-0 flex-1 truncate" title={model.id}>
          {model.name}
        </span>
        {variants.length > 0 && (
          <span className="hidden shrink-0 truncate text-[11.5px] text-ink-secondary sm:inline sm:max-w-[10rem]" title={variants.join(', ')}>
            {variants.slice(0, 3).join(' · ')}
            {variants.length > 3 ? ` +${variants.length - 3}` : ''}
          </span>
        )}
        {model.is_free && <span className="shrink-0 rounded bg-success/10 px-1.5 py-0.5 text-[11px] font-medium text-success">free</span>}
        {selected && (
          <span className="inline-flex shrink-0 items-center gap-1 text-[11.5px] font-medium text-ink">
            <Check size={12} aria-hidden="true" />
            Default
          </span>
        )}
      </button>
    </li>
  );
}

function TestResult({ state }: { state: TestState }) {
  if (state.phase === 'idle') return null;
  if (state.phase === 'running')
    return (
      <p role="status" className="text-[12.5px] text-ink-secondary">
        Waiting for a reply…
      </p>
    );
  if (state.phase === 'failed')
    return (
      <p role="alert" className="text-[12.5px] text-danger">
        {explainError(null, state.message)}
      </p>
    );
  const r = state.result;
  if (!r.ok)
    return (
      <p role="alert" className="text-[12.5px] text-danger" data-engine-test-result="failed">
        {explainError(r.error_code, r.error_message)}
      </p>
    );
  return (
    <div role="status" className="flex min-w-0 flex-col gap-1.5" data-engine-test-result="ok">
      <p className="text-[12.5px] text-success">
        It works. Replied in {formatDuration(r.duration_ms)}
        {r.model_id ? ` with ${r.model_id}` : ''}.
      </p>
      {r.reply_preview && (
        <blockquote dir="auto" className="max-h-24 overflow-y-auto whitespace-pre-wrap break-words rounded-lg bg-inset px-3 py-2 text-[12.5px] text-ink-secondary">
          {r.reply_preview}
        </blockquote>
      )}
    </div>
  );
}

function Usage({ usage }: { usage: UsageByEngine | undefined }) {
  if (!usage || usage.events <= 0) return null;
  const rate = Math.round((usage.succeeded / usage.events) * 100);
  const tokensKnown = usage.token_usage_unknown_events < usage.events;
  const nf = new Intl.NumberFormat();
  const items: Array<[string, string]> = [
    ['Runs', nf.format(usage.events)],
    ['Success', `${rate}%`],
    ['Average time', formatDuration(usage.avg_duration_ms)],
    ['Tokens in / out', tokensKnown ? `${nf.format(usage.tokens_in)} / ${nf.format(usage.tokens_out)}` : 'Not reported'],
  ];
  return (
    <section aria-label="Usage" className="flex flex-col gap-2" data-engine-usage>
      <h4 className="text-[12px] font-semibold text-ink-secondary">Usage</h4>
      <dl className="grid grid-cols-2 gap-x-4 gap-y-2 sm:grid-cols-4">
        {items.map(([label, value]) => (
          <div key={label} className="min-w-0">
            <dt className="text-[11.5px] text-ink-secondary">{label}</dt>
            <dd className="truncate text-[13px] tabular-nums text-ink">{value}</dd>
          </div>
        ))}
      </dl>
      {usage.failed > 0 && <p className="text-[11.5px] text-ink-secondary">{usage.failed} failed.</p>}
    </section>
  );
}

function TechnicalDetails({ entry }: { entry: ProviderEntry }) {
  const st = entry.status;
  const rows: Array<[string, string | null | undefined]> = [
    ['Engine id', entry.id],
    ['Installed at', st?.executable],
    ['Version', st?.version],
    ['Install state', st?.install_state],
    ['Sign-in state', st?.auth_state],
    ['Details', st?.detail],
  ];
  return (
    <details className="group/tech">
      <summary className="cursor-pointer text-[12px] font-medium text-ink-secondary hover:text-ink">Technical details</summary>
      <dl className="mt-2 flex flex-col gap-1.5">
        {rows
          .filter(([, v]) => v)
          .map(([label, value]) => (
            <div key={label} className="grid grid-cols-[7rem_minmax(0,1fr)] gap-3 text-[12px]">
              <dt className="text-ink-secondary">{label}</dt>
              <dd className="min-w-0 break-words text-ink" dir="auto">
                {value}
              </dd>
            </div>
          ))}
      </dl>
    </details>
  );
}

export function ReadyPanel({
  entry,
  test,
  onTest,
  check,
  onCheck,
  usage,
}: {
  entry: ProviderEntry;
  test: TestState;
  onTest: () => void;
  check: CheckState;
  onCheck: () => void;
  usage: UsageByEngine | undefined;
}) {
  return (
    <div className="flex flex-col gap-5">
      {entry.state === 'unverified' && (
        <p className="text-[12.5px] leading-relaxed text-ink-secondary">
          {entry.title} is installed, but we could not confirm you are signed in. It may still work. Send a test message to find out.
        </p>
      )}
      <Models entry={entry} />
      <section aria-label="Test" className="flex flex-col gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            disabled={check.busy}
            onClick={onCheck}
            className="ui-button ui-button-sm"
            data-engine-check={entry.id}
          >
            {check.busy ? 'Checking…' : 'Check connection'}
          </button>
          <button
            type="button"
            disabled={test.phase === 'running'}
            onClick={onTest}
            className="ui-button ui-button-sm"
            data-engine-test={entry.id}
          >
            {test.phase === 'running' ? 'Sending…' : 'Send test message'}
          </button>
        </div>
        <p className="text-[11.5px] text-ink-secondary">Checking is free. A test message uses a little of your quota.</p>
        {check.text && (
          <p role="status" className="text-[12.5px] text-ink-secondary">
            {check.text}
          </p>
        )}
        <TestResult state={test} />
      </section>
      <Usage usage={usage} />
      <TechnicalDetails entry={entry} />
    </div>
  );
}
