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

/** Describe uncertainty without substituting a different model for an explicit choice. */
export function contractModelLabel(snap: ProvidersSnapshot, engine: string | null | undefined, saved: string | null | undefined): string {
  const model = effectiveModel(snap, engine, saved);
  const workspaceDefault = snap.defaultPreference?.effective;
  if (model.inherited) return workspaceDefault ? model.label : 'Default model (unresolved)';

  const catalog = snap.models[engine ?? workspaceDefault?.engine ?? ''];
  if (engine) {
    const state = snap.statuses.find(entry => entry.id === engine)?.state;
    const missing = catalog?.status === 'ready' && !catalog.list.some(entry => entry.id === model.saved);
    if (missing || state === 'not_installed' || state === 'needs_login') return `${model.label} (unavailable)`;
    if (state === 'error') return `${model.label} (unverified)`;
  }
  return catalog?.status === 'ready' ? model.label : `${model.label} (unverified)`;
}
