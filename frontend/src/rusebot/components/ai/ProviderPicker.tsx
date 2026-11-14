/* Provider + model selects bound to the shared choice. Used by Settings (default for new chats) and the New agent dialog. */
import { useEffect, useRef } from 'react';
import { cn } from '../../lib/cn';
import { defaultModelFor, loadProviderModels, useProviders, type EngineChoice } from '../../state/useProviders';
import { STATE_SHORT, selectable } from '../../state/providerText';

export const PICKER_SELECT =
  'w-full min-w-0 cursor-pointer rounded-lg border border-hairline/40 bg-inset py-2 pl-3 pr-3 text-[13px] text-ink focus:outline-none focus:ring-1 focus:ring-focus disabled:cursor-default disabled:opacity-60';

/** Provider select + model select. `value` / `onChange` default to nothing: callers pass the choice they edit. */
export function ProviderPicker({
  idPrefix,
  value,
  onChange,
  className,
}: {
  idPrefix: string;
  value: EngineChoice | null;
  onChange: (next: EngineChoice) => void;
  className?: string;
}) {
  const s = useProviders();
  const engine = value?.engine ?? '';
  const slot = engine ? s.models[engine] : undefined;
  const models = slot?.list ?? [];

  useEffect(() => {
    if (engine) void loadProviderModels(engine);
  }, [engine]);

  // Only the latest pick counts: a slow model list for an earlier provider must not bring that provider back.
  const latestPick = useRef(0);
  const pickEngine = async (id: string) => {
    const mine = ++latestPick.current;
    const list = await loadProviderModels(id);
    if (mine !== latestPick.current) return;
    onChange({ engine: id, model: defaultModelFor(id, list) });
  };
  const pickModel = (model: string) => {
    if (!value) return;
    latestPick.current += 1;
    onChange({ engine: value.engine, model });
  };

  const modelMissing = value && value.model && !models.some((m) => m.id === value.model);

  return (
    <div className={cn('grid min-w-0 grid-cols-1 gap-3 sm:grid-cols-2', className)}>
      <div className="min-w-0">
        <label htmlFor={`${idPrefix}-engine`} className="mb-1 block text-[12px] font-medium text-ink-secondary">
          Provider
        </label>
        <select
          id={`${idPrefix}-engine`}
          value={engine}
          disabled={s.selectable.length === 0}
          onChange={(e) => void pickEngine(e.target.value)}
          className={PICKER_SELECT}
        >
          {s.selectable.length === 0 && <option value="">{s.loading ? 'Loading…' : 'No providers found'}</option>}
          {s.selectable.length > 0 && !engine && (
            <option value="" disabled>
              {s.loading ? 'Loading…' : 'Choose a provider'}
            </option>
          )}
          {s.selectable.map((e) => {
            const usable = e.id === 'mock' || selectable(e.state);
            return (
              <option key={e.id} value={e.id} disabled={!usable}>
                {e.title}
                {e.state !== 'ready' && e.id !== 'mock' ? ` (${STATE_SHORT[e.state]})` : ''}
              </option>
            );
          })}
        </select>
      </div>
      <div className="min-w-0">
        <label htmlFor={`${idPrefix}-model`} className="mb-1 block text-[12px] font-medium text-ink-secondary">
          Model
        </label>
        <select
          id={`${idPrefix}-model`}
          value={value?.model ?? ''}
          disabled={!value || slot?.status !== 'ready' || models.length === 0}
          onChange={(e) => pickModel(e.target.value)}
          className={PICKER_SELECT}
        >
          {(!slot || slot.status === 'loading') && <option value="">Loading models…</option>}
          {slot?.status === 'error' && <option value="">Could not load models</option>}
          {slot?.status === 'ready' && models.length === 0 && <option value="">No models</option>}
          {modelMissing && value && (
            <option value={value.model} disabled>
              {value.model}
            </option>
          )}
          {models.map((m) => (
            <option key={m.id} value={m.id}>
              {m.name}
            </option>
          ))}
        </select>
      </div>
    </div>
  );
}
