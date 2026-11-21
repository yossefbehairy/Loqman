/* Provider + model selects bound to the shared choice. Used by Settings (default for new chats) and the New agent dialog. */
import { useEffect, useRef } from 'react';
import { cn } from '../../lib/cn';
import { defaultModelFor, loadProviderModels, useProviders, type EngineChoice } from '../../state/useProviders';
import { SettingsSelect } from './SettingsSelect';
import { STATE_SHORT, selectable } from '../../state/providerText';

/** Provider select + model select. `value` / `onChange` default to nothing: callers pass the choice they edit. */
export function ProviderPicker({
  idPrefix,
  value,
  onChange,
  onEngineIntent,
  className,
}: {
  idPrefix: string;
  value: EngineChoice | null;
  onChange: (next: EngineChoice) => void;
  onEngineIntent?: (engine: string) => void;
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
    onEngineIntent?.(id);
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
        <SettingsSelect
          id={`${idPrefix}-engine`}
          value={engine}
          disabled={s.selectable.length === 0}
          onValueChange={(id) => void pickEngine(id)}
          options={[
            ...(s.selectable.length === 0 ? [{ value: '', label: s.loading ? 'Loading…' : 'No providers found' }] : []),
            ...(s.selectable.length > 0 && !engine ? [{ value: '', label: s.loading ? 'Loading…' : 'Choose a provider', disabled: true }] : []),
            ...s.selectable.map(e => ({
              value: e.id,
              label: e.title + (e.state !== 'ready' && e.id !== 'mock' ? ` (${STATE_SHORT[e.state]})` : ''),
              disabled: !(e.id === 'mock' || selectable(e.state)),
            })),
          ]}
        />
      </div>
      <div className="min-w-0">
        <label htmlFor={`${idPrefix}-model`} className="mb-1 block text-[12px] font-medium text-ink-secondary">
          Model
        </label>
        <SettingsSelect
          id={`${idPrefix}-model`}
          value={value?.model ?? ''}
          disabled={!value || slot?.status !== 'ready' || models.length === 0}
          onValueChange={pickModel}
          options={[
            ...(!slot || slot.status === 'loading' ? [{ value: '', label: 'Loading models…' }] : []),
            ...(slot?.status === 'error' ? [{ value: '', label: 'Could not load models' }] : []),
            ...(slot?.status === 'ready' && models.length === 0 ? [{ value: '', label: 'No models' }] : []),
            ...(modelMissing && value ? [{ value: value.model, label: value.model, disabled: true }] : []),
            ...models.map(m => ({ value: m.id, label: m.name })),
          ]}
        />
      </div>
    </div>
  );
}
