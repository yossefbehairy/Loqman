import type { ProvidersSnapshot } from './useProviders';

export interface EffectiveModel {
  label: string;
  /** True when the saved model is not what runs: the default chosen in Settings is used instead. */
  inherited: boolean;
  saved: string;
}

/**
 * The model an agent really runs on. The worker falls back to the default model whenever the agent's saved model
 * is not offered by the engine in use (see routing.usable_model), so the UI must not show a stale saved id.
 */
export function effectiveModel(snap: ProvidersSnapshot, engine: string | null | undefined, saved: string | null | undefined): EffectiveModel {
  const eng = engine || snap.choice?.engine || '';
  const entry = eng ? snap.models[eng] : undefined;
  const model = (saved ?? '').trim();
  if (model && (!entry || entry.status !== 'ready' || entry.list.some((m) => m.id === model))) {
    return { label: model, inherited: false, saved: model };
  }
  const fallback = snap.choice && snap.choice.engine === eng ? snap.choice.model : (entry?.list[0]?.id ?? '');
  return { label: fallback ? `Default · ${fallback}` : 'Default model', inherited: true, saved: model };
}
