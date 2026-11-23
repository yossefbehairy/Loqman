import { useEffect, useSyncExternalStore } from 'react';
import type { EngineModel, Profile } from '../data/worker';
import { selectable } from './providerText';
import {
  getProviderSnapshot,
  hasCatalogChoice,
  isExecutableChoice,
  loadProviderModels,
  type EngineChoice,
  type ProvidersSnapshot,
} from './useProviders';

const prefix = 'agentos.rusebot.chatExecution.v1.';
const noModels: EngineModel[] = [];

interface SavedSelection extends EngineChoice {
  preferred?: Record<string, string>;
}

interface ProviderIntent {
  owner: string;
  engine: string;
  profile: Profile | null | undefined;
}

interface OwnerSelection {
  saved?: SavedSelection;
  intent?: ProviderIntent;
  storageFailed?: boolean;
}

function validId(value: unknown, max: number): value is string {
  return typeof value === 'string' && value.length > 0 && value.length <= max && value === value.trim();
}

function readSelections(): Map<string, OwnerSelection> {
  const result = new Map<string, OwnerSelection>();
  try {
    // The old global keys have no conversation owner. Leave them untouched.
    for (const key of Object.keys(localStorage)) {
      const owner = key.slice(prefix.length);
      if (!key.startsWith(prefix) || !validId(owner, 160)) continue;
      try {
        const raw = JSON.parse(localStorage.getItem(key) ?? 'null') as Partial<SavedSelection> | null;
        if (!raw || !validId(raw.engine, 64) || !validId(raw.model, 128)) continue;
        const preferredModels = raw.preferred && typeof raw.preferred === 'object' && !Array.isArray(raw.preferred)
          ? raw.preferred
          : {};
        const preferred = Object.fromEntries(Object.entries(preferredModels)
          .filter(([engine, model]) => validId(engine, 64) && validId(model, 128)).slice(0, 16));
        result.set(owner, { saved: { engine: raw.engine, model: raw.model, preferred } });
      } catch {
        // An invalid record has no effect on any other owner.
      }
    }
  } catch {
    // Storage can be unavailable. Selections still work in memory.
  }
  return result;
}

let selections = readSelections();
const listeners = new Set<() => void>();
const subscribe = (listener: () => void) => {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
};
const getSnapshot = () => selections;

function publish(next: Map<string, OwnerSelection>) {
  selections = next;
  listeners.forEach(listener => listener());
}

function persist(owner: string, saved?: SavedSelection): boolean {
  try {
    if (saved) localStorage.setItem(prefix + owner, JSON.stringify(saved));
    else localStorage.removeItem(prefix + owner);
    return true;
  } catch {
    return false;
  }
}

function commit(owner: string, choice: EngineChoice) {
  const current = selections.get(owner);
  const previous = current?.saved;
  if (!current?.intent && !current?.storageFailed && previous?.engine === choice.engine && previous.model === choice.model) return;
  const preferred = Object.fromEntries([
    [choice.engine, choice.model],
    ...Object.entries(previous?.preferred ?? {}).filter(([engine]) => engine !== choice.engine).slice(0, 15),
  ]);
  const saved = { ...choice, preferred };
  publish(new Map(selections).set(owner, { saved, storageFailed: !persist(owner, saved) }));
}

function completeIntent(intent: ProviderIntent) {
  const { owner, engine, profile } = intent;
  const current = selections.get(owner);
  const providers = getProviderSnapshot();
  const catalog = providers.models[engine];
  if (current?.intent !== intent || catalog?.status !== 'ready') return;
  const preferred = current.saved?.preferred?.[engine]
    ?? (current.saved?.engine === engine ? current.saved.model : undefined);
  const inherited = profile === null ? null : inheritedChoice(providers, profile);
  const inheritedModel = inherited?.engine === engine ? inherited.model : undefined;
  const model = catalog.list.find(entry => entry.id === preferred)?.id
    ?? catalog.list.find(entry => entry.id === inheritedModel)?.id
    ?? catalog.list.find(entry => entry.id)?.id;
  if (!model) return;
  const choice = { engine, model };
  if (hasCatalogChoice(choice, providers)) commit(owner, choice);
}

export function discardDraftChoice(owner: string) {
  if (!selections.has(owner)) return;
  const next = new Map(selections);
  if (persist(owner)) next.delete(owner);
  else next.set(owner, { storageFailed: true });
  publish(next);
}

export function getDraftOverride(owner: string): EngineChoice | undefined {
  const saved = selections.get(owner)?.saved;
  return saved && { engine: saved.engine, model: saved.model };
}

/** The acknowledged conversation receives the originating draft's choice. An edited destination wins. */
export function transferDraftChoice(source: string, destination: string, move: boolean, submitted?: EngineChoice) {
  const from = selections.get(source);
  const saved = move ? from?.saved ?? submitted : submitted;
  const intent = move && from?.intent ? { ...from.intent, owner: destination } : undefined;
  if (!from && !saved) return;
  const existing = selections.get(destination);
  const next = new Map(selections);
  let canRemoveSource = true;
  const transfer = !existing?.saved && !existing?.intent && (saved || intent);
  if (transfer) {
    canRemoveSource = !saved || persist(destination, saved);
    next.set(destination, { saved, intent, storageFailed: !canRemoveSource });
  }
  if (move) {
    if (canRemoveSource && persist(source)) next.delete(source);
    else next.set(source, { storageFailed: true });
  }
  publish(next);
  if (transfer && intent) void loadProviderModels(intent.engine).then(() => completeIntent(intent));
}

function inheritedChoice(providers: ProvidersSnapshot, profile?: Profile): EngineChoice | null {
  const workspace = providers.choice;
  const engine = profile?.engine?.trim();
  const model = profile?.default_model_id?.trim();
  if (engine) {
    const available = providers.models[engine]?.list ?? noModels;
    return { engine, model: model || (workspace?.engine === engine ? workspace.model : available[0]?.id ?? '') };
  }
  if (workspace && model) {
    const catalog = providers.models[workspace.engine];
    if (catalog?.list.some(entry => entry.id === model)) return { engine: workspace.engine, model };
  }
  return workspace;
}

// Null means a specialist's profile is unresolved. Undefined permits workspace inheritance for Loqman.
function ownedChoice(selection: OwnerSelection | undefined, providers: ProvidersSnapshot, profile: Profile | null | undefined): EngineChoice | null {
  if (selection?.intent) return { engine: selection.intent.engine, model: '' };
  return selection?.saved ?? (profile === null ? null : inheritedChoice(providers, profile));
}

export function getChatExecution(owner: string, profile: Profile | null | undefined): EngineChoice | null {
  const providers = getProviderSnapshot();
  const choice = ownedChoice(selections.get(owner), providers, profile);
  return choice && isExecutableChoice(choice, providers) ? { engine: choice.engine, model: choice.model } : null;
}

export function useChatEngines(owner: string, providers: ProvidersSnapshot, profile: Profile | null | undefined) {
  const selection = useSyncExternalStore(subscribe, getSnapshot).get(owner);
  const choice = ownedChoice(selection, providers, profile);
  const engine = choice?.engine;
  const catalog = engine ? providers.models[engine] : undefined;
  const provider = providers.selectable.find(entry => entry.id === engine);
  const available = provider && (provider.id === 'mock' || selectable(provider.state));
  const intent = selection?.intent;
  useEffect(() => {
    if (engine && available && !catalog) void loadProviderModels(engine);
    if (intent && catalog?.status === 'ready') completeIntent(intent);
  }, [engine, available, catalog, intent]);
  return {
    engines: providers.selectable,
    models: catalog?.list ?? noModels,
    choice,
    explicit: Boolean(selection?.saved || intent),
    needsAgentDefaults: profile === null && !choice,
    storageFailed: selection?.storageFailed ?? false,
    providerUnavailable: Boolean(engine && !available && !providers.loading),
    catalogStatus: catalog?.status,
    executionReady: isExecutableChoice(choice, providers),
    loading: providers.loading,
    setChoice(next: EngineChoice) {
      if (ownedChoice(selections.get(owner), getProviderSnapshot(), profile)?.engine === next.engine && hasCatalogChoice(next)) {
        commit(owner, next);
      }
    },
    async selectEngine(engine: string) {
      const intent = { owner, engine, profile };
      publish(new Map(selections).set(owner, { ...selections.get(owner), intent }));
      await loadProviderModels(engine);
      completeIntent(intent);
    },
  };
}

export type EngineState = ReturnType<typeof useChatEngines>;
