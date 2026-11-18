/*
 * Shared provider store: engine statuses, capabilities, models per engine, the current
 * {engine, model} choice and each provider's preferred model. One module-level store read
 * with useSyncExternalStore, so a Refresh in Settings updates the Composer and the New agent
 * dialog without a reload.
 *
 * Default-pick order: stored choice if still usable -> "mock" if present -> first ready ->
 * first unverified, each only if it has a model to offer. When nothing qualifies the choice is
 * cleared and the Composer refuses to send, pointing to Settings > AI. "mock" only exists when
 * the worker runs in test mode, and stays preferred there so e2e never calls a real model.
 * The worker owns the default for new chats (preferences.get / preferences.set_default). On the first
 * load it replaces a remembered choice when that default is usable and still offers its model; localStorage
 * stays as the cache and for the per-chat choice. The Settings strip saves through saveDefaultProvider.
 * A late answer never overrides a choice made after it was requested (see choiceSeq).
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

const KEY_ENGINE = 'agentos.rusebot.engine.v1';
const KEY_PREFERRED = 'agentos.rusebot.preferredModels.v1';

export interface EngineChoice {
  engine: string;
  model: string;
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
}

export interface ProvidersSnapshot {
  /** Every engine the worker reports, for the Settings list. */
  statuses: ProviderEntry[];
  /** Engines that can run a conversation, for pickers. Includes "mock" in test mode. */
  selectable: ProviderEntry[];
  models: Record<string, ModelsEntry>;
  choice: EngineChoice | null;
  preferred: Record<string, string>;
  /** True until the first load and the default pick have finished. */
  loading: boolean;
  refreshing: boolean;
  error: string | null;
  checkedAt: string | null;
  /** Why the last attempt to save the default to the worker failed, if it did. */
  defaultError: string | null;
}

function readJson<T>(key: string, fallback: T): T {
  try {
    const raw = localStorage.getItem(key);
    return raw ? (JSON.parse(raw) as T) : fallback;
  } catch {
    return fallback;
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
  choice: readJson<EngineChoice | null>(KEY_ENGINE, null),
  preferred: readJson<Record<string, string>>(KEY_PREFERRED, {}),
  loading: true,
  refreshing: false,
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
const getSnapshot = () => snap;

/** Only "mock" may be ready without a discovery result; a real engine nobody has seen is a problem. */
function entryOf(id: string, status: EngineStatus | null, caps: EngineCapabilities): ProviderEntry {
  const { title, vendor } = titleOf(id, status);
  const state: EngineStateId = status ? deriveState(status) : id === 'mock' ? 'ready' : 'error';
  return { id, status, caps, state, title, vendor };
}

const isUsable = (e: ProviderEntry) => e.id === 'mock' || selectable(e.state);

// ---- models -------------------------------------------------------------------------

const modelRequests = new Map<string, Promise<EngineModel[]>>();

/** Load (once) and cache an engine's models. Errors are kept so the UI can show them and retry. `force` reloads. */
export function loadProviderModels(engine: string, force = false): Promise<EngineModel[]> {
  const cached = snap.models[engine];
  if (!force && cached?.status === 'ready') return Promise.resolve(cached.list);
  const inflight = modelRequests.get(engine);
  // A forced reload queues behind a running request so the newer answer is always the one that sticks.
  if (inflight) return force ? inflight.then(() => loadProviderModels(engine, true)) : inflight;
  patch({ models: { ...snap.models, [engine]: { status: 'loading', list: cached?.list ?? [], error: null } } });
  const request = (engine === 'mock' ? freeModels() : engineModels(engine))
    .then((list) => {
      // A remembered model that this provider no longer offers is dropped, not kept for later.
      const kept = snap.preferred[engine];
      const stale = list.length > 0 && kept !== undefined && !list.some((m) => m.id === kept);
      const preferred = stale ? Object.fromEntries(Object.entries(snap.preferred).filter(([k]) => k !== engine)) : snap.preferred;
      patch({ models: { ...snap.models, [engine]: { status: 'ready', list, error: null } }, preferred });
      if (stale) writeJson(KEY_PREFERRED, preferred);
      return list;
    })
    .catch((e: unknown) => {
      patch({
        models: { ...snap.models, [engine]: { status: 'error', list: [], error: e instanceof Error ? e.message : String(e) } },
      });
      return [] as EngineModel[];
    })
    .finally(() => {
      modelRequests.delete(engine);
    });
  modelRequests.set(engine, request);
  return request;
}

/** Current choice if it is still in the list, else the provider's remembered model if it is, else the first. */
function modelFor(engine: string, list: EngineModel[], current?: EngineChoice | null): string {
  if (current && current.engine === engine && list.some((m) => m.id === current.model)) return current.model;
  const pref = snap.preferred[engine];
  if (pref && list.some((m) => m.id === pref)) return pref;
  return list[0]?.id ?? '';
}

/** The model a provider starts with when switched to: its remembered one if still offered, else the first. */
export function defaultModelFor(engine: string, list: EngineModel[]): string {
  return modelFor(engine, list, null);
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

/** Bumped on every change of the choice. Anything that awaited before changing it checks that it still holds. */
let choiceSeq = 0;
/** Bumped only when the user (or a Settings action) chooses; automatic reconciliation does not count. */
let userChoiceSeq = 0;

export function setProviderChoice(next: EngineChoice): void {
  choiceSeq += 1;
  userChoiceSeq += 1;
  const preferred = next.model ? { ...snap.preferred, [next.engine]: next.model } : snap.preferred;
  patch({ choice: next, preferred });
  writeJson(KEY_ENGINE, next);
  if (next.model) writeJson(KEY_PREFERRED, preferred);
}

/**
 * The Settings "Default for new chats": use it now and save it in the worker, which applies it to Loqman's
 * missions, delegation and new agents. "mock" (test mode) is never saved: the worker would refuse it.
 */
export async function saveDefaultProvider(next: EngineChoice): Promise<void> {
  setProviderChoice(next);
  if (next.engine === 'mock' || !next.model) return;
  try {
    await preferencesSetDefault(next.engine, next.model);
    patch({ defaultError: null });
  } catch (e) {
    patch({ defaultError: e instanceof Error ? e.message : String(e) });
  }
}

/** Switch provider; the model becomes that provider's preferred one (or its first). The last request wins. */
export async function selectProviderEngine(engine: string): Promise<void> {
  const ticket = ++choiceSeq;
  userChoiceSeq += 1;
  const list = await loadProviderModels(engine);
  if (ticket !== choiceSeq) return;
  setProviderChoice({ engine, model: modelFor(engine, list) });
}

/** Remember a model as the provider's preferred one; if it is the current provider, use it now. */
export function setPreferredModel(engine: string, model: string): void {
  if (snap.choice?.engine === engine) {
    setProviderChoice({ engine, model });
    return;
  }
  const preferred = { ...snap.preferred, [engine]: model };
  patch({ preferred });
  writeJson(KEY_PREFERRED, preferred);
}

/**
 * Keep the choice valid: the current provider while it is usable and still offers the model, else
 * "mock", else the first ready, else the first unverified one that has a model. With none, the
 * choice is cleared. Not persisted until the user chooses. A choice made while this awaited wins.
 */
async function reconcileChoice(): Promise<void> {
  const ticket = choiceSeq;
  const current = snap.choice;
  const usable = snap.selectable.filter(isUsable);
  const order = [
    ...usable.filter((e) => e.id === current?.engine),
    ...usable.filter((e) => e.id === 'mock'),
    ...usable.filter((e) => e.state === 'ready'),
    ...usable.filter((e) => e.state === 'unverified'),
  ];
  const tried = new Set<string>();
  for (const e of order) {
    if (tried.has(e.id)) continue;
    tried.add(e.id);
    const models = await loadProviderModels(e.id);
    if (ticket !== choiceSeq) return;
    const model = modelFor(e.id, models, current);
    if (!model) continue;
    if (current?.engine !== e.id || current.model !== model) {
      choiceSeq += 1;
      patch({ choice: { engine: e.id, model } });
    }
    return;
  }
  if (ticket === choiceSeq && snap.choice) {
    choiceSeq += 1;
    patch({ choice: null });
  }
}

/**
 * The worker's saved default replaces a remembered choice, once, if it is usable and still offers its model.
 * `userSeq` was captured before the first requests started: anything the user chose since wins.
 */
async function applyWorkerDefault(pref: DefaultPreference | null, userSeq: number): Promise<void> {
  if (!pref?.engine || !pref.model_id) return;
  if (userSeq !== userChoiceSeq) return;
  if (snap.selectable.some((e) => e.id === 'mock')) return; // test mode keeps "mock" preferred
  const entry = snap.selectable.find((e) => e.id === pref.engine);
  if (!entry || !isUsable(entry)) return;
  const ticket = choiceSeq;
  const models = await loadProviderModels(pref.engine);
  if (ticket !== choiceSeq || userSeq !== userChoiceSeq || !models.some((m) => m.id === pref.model_id)) return;
  setProviderChoice({ engine: pref.engine, model: pref.model_id });
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
let workerDefaultRead = false;

/** Load statuses and capabilities. `force` makes the worker re-probe instead of using its ~30 s cache. */
export function refreshProviders(force = false): Promise<void> {
  if (loadRequest) return force ? loadRequest.then(() => refreshProviders(true)) : loadRequest;
  patch({ refreshing: true });
  const userSeq = userChoiceSeq; // before any request starts, so a choice made while they are pending is seen
  loadRequest = (async () => {
    let failure: string | null = null;
    const [caps, statuses, pref] = await Promise.all([
      engineCapabilities().catch(() => null),
      engineStatuses(force).catch((e: unknown) => {
        failure = e instanceof Error ? e.message : String(e);
        return null;
      }),
      workerDefaultRead
        ? Promise.resolve(null)
        : preferencesGet()
            .then((p) => {
              workerDefaultRead = true;
              return p;
            })
            .catch(() => null),
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
    await applyWorkerDefault(pref, userSeq);
    // Account changes and empty first answers must not stick: an explicit Refresh reloads every list that was loaded.
    if (force) await Promise.all(Object.keys(snap.models).map((id) => loadProviderModels(id, true)));
    await reconcileChoice();
  })().finally(() => {
    loadRequest = null;
    patch({ loading: false, refreshing: false });
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
  return useSyncExternalStore(subscribe, getSnapshot);
}
