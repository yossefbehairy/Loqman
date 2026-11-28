/*
 * Shared provider discovery, catalogs, workspace defaults and provider test preferences.
 * Chat overrides have their own owners and never update this store's default.
 */
import { useEffect, useSyncExternalStore } from 'react';
import {
  engineCapabilities,
  engineModels,
  engineStatuses,
  freeModels,
  preferencesGet,
  preferencesSetDefault,
  type DefaultPreference,
  type EngineCapabilities,
  type EngineCheckResult,
  type EngineModel,
  type EngineStateId,
  type EngineStatus,
} from '../data/worker';
import { deriveState, selectable, titleOf } from './providerText';

const KEY_PREFERRED = 'agentos.rusebot.preferredModels.v1';

export interface EngineChoice {
  engine: string;
  model: string;
}

interface WorkerDefault {
  saved: EngineChoice | null;
  effective: EngineChoice | null;
}

function workerDefaultOf(pref: DefaultPreference & { effective_engine?: string; effective_model_id?: string }): WorkerDefault {
  const saved = pref.engine && pref.model_id ? { engine: pref.engine, model: pref.model_id } : null;
  const effective = pref.effective_engine && pref.effective_model_id
    ? { engine: pref.effective_engine, model: pref.effective_model_id }
    : saved;
  return { saved, effective };
}

export interface ProviderEntry {
  id: string;
  status: EngineStatus | null;
  caps: EngineCapabilities;
  state: EngineStateId;
  title: string;
  vendor: string;
}

export interface ModelsEntry {
  status: 'loading' | 'ready' | 'error';
  list: EngineModel[];
  error: string | null;
  recheck?: symbol;
}

export interface ProvidersSnapshot {
  /** Every engine the worker reports, for the Settings list. */
  statuses: ProviderEntry[];
  /** Engines that can run a conversation, for pickers. Includes "mock" in test mode. */
  selectable: ProviderEntry[];
  models: Record<string, ModelsEntry>;
  /** Usable workspace choice, falling back through discovered providers when necessary. */
  choice: EngineChoice | null;
  defaultPreference: WorkerDefault | null;
  defaultSaving: boolean;
  preferred: Record<string, string>;
  /** True until the first load and the default pick have finished. */
  loading: boolean;
  refreshing: boolean;
  recheck: symbol | null;
  error: string | null;
  checkedAt: string | null;
  /** Why the last attempt to save the default to the worker failed, if it did. */
  defaultError: string | null;
}

function readPreferred(): Record<string, string> {
  try {
    const raw = JSON.parse(localStorage.getItem(KEY_PREFERRED) ?? 'null');
    return raw && typeof raw === 'object' && !Array.isArray(raw)
      ? Object.fromEntries(Object.entries(raw).filter((entry): entry is [string, string] =>
        typeof entry[1] === 'string' && entry[1].length > 0))
      : {};
  } catch {
    return {};
  }
}

function writeJson(key: string, value: unknown): void {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* storage unavailable: state stays in memory */
  }
}

let snap: ProvidersSnapshot = {
  statuses: [],
  selectable: [],
  models: {},
  choice: null,
  defaultPreference: null,
  defaultSaving: false,
  preferred: readPreferred(),
  loading: true,
  refreshing: false,
  recheck: null,
  error: null,
  checkedAt: null,
  defaultError: null,
};
const listeners = new Set<() => void>();

function patch(next: Partial<ProvidersSnapshot>): void {
  snap = { ...snap, ...next };
  listeners.forEach((l) => l());
}

const subscribe = (l: () => void) => {
  listeners.add(l);
  return () => {
    listeners.delete(l);
  };
};
export const getProviderSnapshot = () => snap;

/** Only "mock" may be ready without a discovery result; a real engine nobody has seen is a problem. */
function entryOf(id: string, status: EngineStatus | null, caps: EngineCapabilities): ProviderEntry {
  const { title, vendor } = titleOf(id, status);
  const state: EngineStateId = status ? deriveState(status) : id === 'mock' ? 'ready' : 'error';
  return { id, status, caps, state, title, vendor };
}

const isUsable = (e: ProviderEntry) => e.id === 'mock' || selectable(e.state);

export function hasCatalogChoice(choice: EngineChoice, providers = snap): boolean {
  const catalog = providers.models[choice.engine];
  return catalog?.status === 'ready' && catalog.list.some((model) => model.id === choice.model) &&
    providers.selectable.some((entry) => entry.id === choice.engine && isUsable(entry));
}

/** Consult the live store, including catalog updates received since the last React render. */
export function isExecutableChoice(choice: EngineChoice | null | undefined, providers = snap): boolean {
  return !!choice && !providers.loading && hasCatalogChoice(choice, providers);
}

/** Check a saved Retry choice independently of the current composer choice. */
export function retryProviderReadiness(choice: EngineChoice, providers = snap) {
  const catalog = providers.models[choice.engine];
  const catalogIsCurrent = !providers.recheck || catalog?.recheck === providers.recheck;
  // The saved pair's catalog can fail while startup is still finding a composer fallback.
  if (catalogIsCurrent && catalog?.status === 'error') return 'failed';
  // After startup, a recheck can settle this pair while other catalogs are still refreshing.
  const checkingProviders = providers.loading || (!providers.recheck && providers.refreshing);
  if (checkingProviders || !catalogIsCurrent || catalog?.status === 'loading') return 'checking';
  const engine = providers.selectable.find(entry => entry.id === choice.engine);
  if (providers.error || engine?.state === 'error') return 'failed';
  if (!engine || !isUsable(engine)) return 'unavailable';
  if (!catalog) return 'needs-catalog';
  return catalog.list.some(model => model.id === choice.model) ? 'ready' : 'unavailable';
}

// ---- models -------------------------------------------------------------------------

const modelRequests = new Map<string, Promise<EngineModel[]>>();

/** Load (once) and cache an engine's models. Errors are kept so the UI can show them and retry. `force` reloads. */
export function loadProviderModels(engine: string, force = false, recheck?: symbol): Promise<EngineModel[]> {
  const cached = snap.models[engine];
  if (!force && cached?.status === 'ready') return Promise.resolve(cached.list);
  const inflight = modelRequests.get(engine);
  // A forced reload queues behind a running request so the newer answer is always the one that sticks.
  if (inflight) return force ? inflight.then(() => loadProviderModels(engine, true, recheck)) : inflight;
  patch({ models: { ...snap.models, [engine]: { status: 'loading', list: cached?.list ?? [], error: null } } });
  const request = (engine === 'mock' ? freeModels() : engineModels(engine))
    .then((list) => {
      // A remembered model that this provider no longer offers is dropped, not kept for later.
      const kept = snap.preferred[engine];
      const stale = list.length > 0 && kept !== undefined && !list.some((m) => m.id === kept);
      const preferred = stale ? Object.fromEntries(Object.entries(snap.preferred).filter(([k]) => k !== engine)) : snap.preferred;
      patch({ models: { ...snap.models, [engine]: { status: 'ready', list, error: null, recheck } }, preferred });
      if (stale) writeJson(KEY_PREFERRED, preferred);
      return list;
    })
    .catch((e: unknown) => {
      patch({
        models: { ...snap.models, [engine]: { status: 'error', list: [], error: e instanceof Error ? e.message : String(e), recheck } },
      });
      return [] as EngineModel[];
    })
    .finally(() => {
      modelRequests.delete(engine);
    });
  modelRequests.set(engine, request);
  return request;
}

/** The model a provider starts with when switched to: its remembered one if still offered, else the first. */
export function defaultModelFor(engine: string, list: EngineModel[]): string {
  const pref = snap.preferred[engine];
  if (pref && list.some((m) => m.id === pref)) return pref;
  return list[0]?.id ?? '';
}

/** The model to show or test for a provider: the remembered or chosen one if `list` still offers it, else the first. */
export function validModelFor(engine: string, list: EngineModel[]): string | undefined {
  const pref = snap.preferred[engine];
  if (pref && list.some((m) => m.id === pref)) return pref;
  const chosen = snap.choice?.engine === engine ? snap.choice.model : undefined;
  if (chosen && list.some((m) => m.id === chosen)) return chosen;
  return list[0]?.id;
}

// ---- choice -------------------------------------------------------------------------

/** Orders workspace fallback calculations. */
let choiceSeq = 0;

let defaultSaveQueue = Promise.resolve();
let latestDefaultSave = 0;
let latestDefaultSelection = 0;

/** Register an explicit Settings pick before catalog discovery, even across Settings remounts. */
export function beginDefaultProviderSelection(): () => boolean {
  const ticket = ++latestDefaultSelection;
  return () => ticket === latestDefaultSelection;
}

export function saveDefaultProvider(next: EngineChoice): Promise<void> {
  if (next.engine === 'mock' || !next.model) {
    if (!snap.defaultSaving) patch({ defaultError: 'Choose a provider with an available model.' });
    return Promise.resolve();
  }
  const ticket = ++latestDefaultSave;
  patch({ defaultSaving: true, defaultError: null });
  // Serialize writes as well as acknowledgements so save order also holds in the worker.
  defaultSaveQueue = defaultSaveQueue.then(async () => {
    try {
      const pref = workerDefaultOf(await preferencesSetDefault(next.engine, next.model));
      patch({ defaultPreference: pref });
      void reconcileChoice();
    } catch (e) {
      if (ticket === latestDefaultSave) patch({ defaultError: e instanceof Error ? e.message : String(e) });
    } finally {
      if (ticket === latestDefaultSave) patch({ defaultSaving: false });
    }
  });
  return defaultSaveQueue;
}

/** Remember a model for provider tools and deliberate provider picks. */
export function setPreferredModel(engine: string, model: string): void {
  const preferred = { ...snap.preferred, [engine]: model };
  patch({ preferred });
  writeJson(KEY_PREFERRED, preferred);
}

/** Prefer the worker default, then mock or the first usable catalog. */
async function reconcileChoice(): Promise<void> {
  const ticket = ++choiceSeq;
  const wanted = snap.defaultPreference?.effective;
  const usable = snap.selectable.filter(isUsable);
  const order = [
    ...usable.filter((e) => e.id === wanted?.engine),
    ...usable.filter((e) => e.id === 'mock'),
    ...usable.filter((e) => e.state === 'ready'),
    ...usable.filter((e) => e.state === 'unverified'),
  ];
  const tried = new Set<string>();
  for (const e of order) {
    if (tried.has(e.id)) continue;
    tried.add(e.id);
    const catalog = snap.models[e.id];
    const models = catalog && catalog.status !== 'loading' ? catalog.list : await loadProviderModels(e.id);
    if (ticket !== choiceSeq) return;
    const model = wanted?.engine === e.id && models.some((m) => m.id === wanted.model) ? wanted.model : models[0]?.id;
    if (!model) continue;
    if (snap.choice?.engine !== e.id || snap.choice.model !== model) patch({ choice: { engine: e.id, model } });
    return;
  }
  if (snap.choice) patch({ choice: null });
}

/** Fold a fresh engines.check discovery into the shared status, so Settings, the Composer and the dialogs agree with it. */
export function applyEngineCheck(engineId: string, discovery: EngineCheckResult['discovery']): void {
  if (!discovery) return;
  const fold = (e: ProviderEntry): ProviderEntry => {
    if (e.id !== engineId) return e;
    const status: EngineStatus = { ...(e.status ?? { engine_id: engineId }), ...discovery, engine_id: engineId, state: undefined };
    return entryOf(engineId, status, e.caps);
  };
  patch({ statuses: snap.statuses.map(fold), selectable: snap.selectable.map(fold) });
  void reconcileChoice();
}

// ---- loading ------------------------------------------------------------------------

let loadRequest: Promise<void> | null = null;
let recheckRequest: Promise<void> | null = null;
let workerDefaultRead = false;

/** Load statuses and capabilities. `force` makes the worker re-probe instead of using its ~30 s cache. */
export function refreshProviders(force = false): Promise<void> {
  if (recheckRequest) return recheckRequest;
  if (!force) return loadRequest ?? loadProviders();
  const recheck = Symbol();
  // Queue at most one explicit recheck behind startup, without overlapping discovery writes.
  recheckRequest = (loadRequest ?? Promise.resolve()).then(() => loadProviders(recheck)).finally(() => {
    recheckRequest = null;
    patch({ recheck: null });
  });
  patch({ recheck });
  return recheckRequest;
}

function loadProviders(recheck?: symbol): Promise<void> {
  patch({ refreshing: true });
  const defaultAtLoad = snap.defaultPreference;
  loadRequest = (async () => {
    let failure: string | null = null;
    const preferenceRead = workerDefaultRead ? Promise.resolve(null) : preferencesGet()
      .then((pref) => {
        workerDefaultRead = true;
        return pref;
      })
      .catch(() => null);
    const [caps, statuses] = await Promise.all([
      engineCapabilities().catch((e: unknown) => {
        failure ??= e instanceof Error ? e.message : String(e);
        return null;
      }),
      engineStatuses(Boolean(recheck)).catch((e: unknown) => {
        failure ??= e instanceof Error ? e.message : String(e);
        return null;
      }),
    ]);
    const known = statuses ?? snap.statuses.flatMap((e) => (e.status ? [e.status] : []));
    const capMap = caps ?? Object.fromEntries(snap.selectable.map((e) => [e.id, e.caps]));
    // A real engine with capabilities but no discovery result is listed as a problem, not assumed ready.
    const statusEntries = [
      ...known.map((s) => entryOf(s.engine_id, s, capMap[s.engine_id] ?? {})),
      ...Object.entries(capMap)
        .filter(([id]) => id !== 'mock' && !known.some((s) => s.engine_id === id))
        .map(([id, c]) => entryOf(id, null, c)),
    ];
    const selectableEntries = Object.entries(capMap)
      .filter(([, c]) => c.conversation_runs)
      .map(([id, c]) => entryOf(id, known.find((s) => s.engine_id === id) ?? null, c));
    const stamps = known.map((s) => s.checked_at).filter((t): t is string => Boolean(t));
    patch({
      statuses: statusEntries,
      selectable: selectableEntries,
      error: failure,
      checkedAt: statuses ? (stamps.sort().at(-1) ?? new Date().toISOString()) : snap.checkedAt,
    });
    const pref = await preferenceRead;
    if (pref && snap.defaultPreference === defaultAtLoad) patch({ defaultPreference: workerDefaultOf(pref) });
    // Account changes and empty first answers must not stick: an explicit Refresh reloads every list that was loaded.
    if (recheck) await Promise.all(Object.keys(snap.models).map((id) => loadProviderModels(id, true, recheck)));
    await reconcileChoice();
  })().finally(() => {
    loadRequest = null;
    patch({ loading: false, refreshing: false });
    // Label lookup should never delay composer readiness.
    const inheritedEngine = snap.defaultPreference?.effective?.engine;
    if (inheritedEngine && snap.selectable.some((entry) => entry.id === inheritedEngine && isUsable(entry))) {
      void loadProviderModels(inheritedEngine);
    }
  });
  return loadRequest;
}

let started = false;
function ensureStarted(): void {
  if (started) return;
  started = true;
  void refreshProviders(false);
}

export function useProviders(): ProvidersSnapshot {
  useEffect(ensureStarted, []);
  return useSyncExternalStore(subscribe, getProviderSnapshot);
}
