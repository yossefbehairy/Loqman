import type { ProvidersSnapshot } from './useProviders';

export interface EffectiveModel {
  label: string;
  /** True when the saved model is not what runs: the default chosen in Settings is used instead. */
  inherited: boolean;
  saved: string;
}

/**
 * An explicit engine keeps the profile's configuration. For an inherited engine the worker falls back
 * to its own default when its catalog excludes the candidate (see routing.usable_model).
 */
export function effectiveModel(snap: ProvidersSnapshot, engine: string | null | undefined, saved: string | null | undefined): EffectiveModel {
  const model = (saved ?? '').trim();
  const worker = snap.defaultPreference?.effective;
  const entry = worker ? snap.models[worker.engine] : undefined;
  if (model && (engine || entry?.status !== 'ready' || entry.list.some((m) => m.id === model))) {
    return { label: model, inherited: false, saved: model };
  }
  const fallback = worker?.model;
  return { label: fallback ? `Default · ${fallback}` : 'Default model', inherited: true, saved: model };
}
