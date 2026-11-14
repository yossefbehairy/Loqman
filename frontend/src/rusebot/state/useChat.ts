import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  CEO_PROFILE_ID,
  bindConversationProfile,
  askCeo,
  cancelRun,
  createConversation,
  decideApproval,
  decideDraft,
  draftContent,
  createDraft,
  followRun,
  getRun,
  importLegacyCeo,
  isTerminal,
  listApprovals,
  listConversations,
  listAllDrafts,
  listDrafts,
  listRuns,
  listMessages,
  startTurn,
  type Approval,
  type Conversation,
  type ConvMessage,
  type Draft,
  type EngineCapabilities,
  type EngineModel,
  type EngineStateId,
  type EngineStatus,
  type RunSnapshot,
} from '../data/worker';
import type { StreamHandle } from '../../api/transport';
import { readLegacyThreads, type LegacyThread } from '../data/legacy';
import type { Agent } from '../../types/agentos';
import {
  loadProviderModels,
  selectProviderEngine,
  setProviderChoice,
  useProviders,
  type EngineChoice,
} from './useProviders';

const KEY_DRAFTS = 'agentos.rusebot.drafts.v1';
const KEY_RUNS = 'agentos.rusebot.runs.v1';
const KEY_SELECTED = 'agentos.rusebot.selected.v1';
const KEY_AGENTS = 'agentos.rusebot.convAgents.v1';

/** ceo.ask refuses longer questions (backend/pyworker/api/ceo.py), so refuse them before anything is saved. */
export const CEO_MAX_CHARS = 2000;

export const CONTRACT_OPEN = '[[contract]]';
export const CONTRACT_CLOSE = '[[/contract]]';
/** Hide the contract preamble that is sent with a conversation's first message. */
export function stripContract(text: string): string {
  if (!text.startsWith(CONTRACT_OPEN)) return text;
  const end = text.indexOf(CONTRACT_CLOSE);
  return end < 0 ? text : text.slice(end + CONTRACT_CLOSE.length).replace(/^\s+/, '');
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

export type { EngineChoice } from './useProviders';

export interface EngineState {
  engines: Array<{ id: string; status: EngineStatus | null; caps: EngineCapabilities; state: EngineStateId; title: string }>;
  /** Models of the current provider. */
  models: EngineModel[];
  choice: EngineChoice | null;
  loading: boolean;
  setChoice: (choice: EngineChoice) => void;
  /** Switch provider and use its preferred model. */
  selectEngine: (engine: string) => Promise<void>;
  loadModels: (engine: string) => Promise<EngineModel[]>;
}

/** Thin adapter over the shared provider store (see useProviders.ts). */
export function useEngines(): EngineState {
  const s = useProviders();
  const engines = useMemo(
    () => s.selectable.map((e) => ({ id: e.id, status: e.status, caps: e.caps, state: e.state, title: e.title })),
    [s.selectable]
  );
  const models = s.choice ? (s.models[s.choice.engine]?.list ?? NO_MODELS) : NO_MODELS;
  return {
    engines,
    models,
    choice: s.choice,
    loading: s.loading,
    setChoice: setProviderChoice,
    selectEngine: selectProviderEngine,
    loadModels: loadProviderModels,
  };
}

const NO_MODELS: EngineModel[] = [];

export interface RunState {
  runId: string;
  status: string;
  error: string | null;
}

export interface ChatState {
  conversations: Conversation[];
  legacy: LegacyThread[];
  selectedId: string | null;
  select: (id: string | null) => void;
  messages: ConvMessage[];
  pendingUser: string | null;
  run: RunState | null;
  runsBySeq: Record<number, RunSnapshot>;
  approvals: Approval[];
  drafts: Record<string, Draft[]>;
  error: string | null;
  dismissError: () => void;
  draft: string;
  setDraft: (text: string) => void;
  send: (text: string) => Promise<void>;
  stop: () => Promise<void>;
  newChat: () => void;
  decide: (approval: Approval, decision: 'approve' | 'deny') => Promise<void>;
  makeDeliverable: (runId: string) => Promise<void>;
  reviewDraft: (draft: Draft, decision: 'approve' | 'reject') => Promise<void>;
  downloadDraft: (draft: Draft) => Promise<void>;
  loaded: boolean;
  activeRunIds: Record<string, string>;
  agentFor: (conversationId: string | null) => Agent | null;
  openAgentChat: (agent: Agent) => Promise<string>;
  reloadConversations: () => Promise<void>;
  loadAllDrafts: () => Promise<void>;
}

export function messageOf(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function cacheRuns(conversationId: string, ids: string[]): void {
  const all = readJson<Record<string, string[]>>(KEY_RUNS, {});
  all[conversationId] = ids.slice(-30);
  writeJson(KEY_RUNS, all);
}

export function useChat(engine: EngineChoice | null, agents: Agent[] = [], onAgentsChanged?: () => void): ChatState {
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [legacy] = useState<LegacyThread[]>(() => readLegacyThreads());
  const [selectedId, setSelectedId] = useState<string | null>(() => readJson<string | null>(KEY_SELECTED, null));
  const [messages, setMessages] = useState<ConvMessage[]>([]);
  const [pendingUser, setPendingUser] = useState<string | null>(null);
  const [runs, setRuns] = useState<Record<string, RunState>>({});
  const [runsBySeq, setRunsBySeq] = useState<Record<number, RunSnapshot>>({});
  const [approvals, setApprovals] = useState<Approval[]>([]);
  const [drafts, setDrafts] = useState<Record<string, Draft[]>>({});
  const [error, setError] = useState<string | null>(null);
  const [draftMap, setDraftMap] = useState<Record<string, string>>(() => readJson(KEY_DRAFTS, {}));
  const [loaded, setLoaded] = useState(false);
  const [draftAgentId, setDraftAgentId] = useState<string | null>(null);
  const draftAgentRef = useRef<string | null>(null);
  draftAgentRef.current = draftAgentId;
  const [agentMap, setAgentMap] = useState<Record<string, string>>(() => readJson(KEY_AGENTS, {}));
  const agentMapRef = useRef(agentMap);
  agentMapRef.current = agentMap;
  const ceo = useMemo(() => agents.find((a) => a.mgr && !a.team) ?? agents[0] ?? null, [agents]);
  const bindAgent = useCallback((cid: string, agentId: string) => {
    const next = { ...agentMapRef.current, [cid]: agentId };
    agentMapRef.current = next;
    setAgentMap(next);
    writeJson(KEY_AGENTS, next);
  }, []);
  const agentFor = useCallback(
    (cid: string | null) =>
      cid ? (agents.find((a) => a.id === agentMap[cid]) ?? ceo) : (agents.find((a) => a.id === draftAgentId) ?? ceo),
    [agents, agentMap, ceo, draftAgentId]
  );
  const streams = useRef(new Map<string, StreamHandle>());
  const selectedRef = useRef(selectedId);
  selectedRef.current = selectedId;

  const refreshConversations = useCallback(async () => {
    setConversations(await listConversations());
  }, []);

  const loadThread = useCallback(async (conversationId: string) => {
    const list = await listMessages(conversationId);
    if (selectedRef.current === conversationId) setMessages(list);
    // The worker knows this conversation's runs; the browser index is only a fallback when it is unreachable.
    let snaps: Array<RunSnapshot | null> | null = await listRuns(conversationId, 30).catch(() => null);
    if (snaps) {
      cacheRuns(conversationId, snaps.map((s) => s!.id));
    } else {
      const ids = readJson<Record<string, string[]>>(KEY_RUNS, {})[conversationId] ?? [];
      snaps = await Promise.all(ids.slice(-30).map((id) => getRun(id).catch(() => null)));
    }
    const bySeq: Record<number, RunSnapshot> = {};
    const nextDrafts: Record<string, Draft[]> = {};
    for (const snap of snaps) {
      if (!snap) continue;
      if (snap.assistant_message_seq != null) bySeq[snap.assistant_message_seq] = snap;
      if (snap.status === 'succeeded') nextDrafts[snap.id] = await listDrafts(snap.id).catch(() => []);
    }
    if (selectedRef.current === conversationId) {
      setRunsBySeq(bySeq);
      setDrafts((prev) => ({ ...prev, ...nextDrafts }));
    }
    return snaps.filter((s): s is RunSnapshot => s !== null);
  }, []);

  const remember = (conversationId: string, runId: string) => {
    const all = readJson<Record<string, string[]>>(KEY_RUNS, {});
    const list = all[conversationId] ?? [];
    if (!list.includes(runId)) all[conversationId] = [...list, runId];
    writeJson(KEY_RUNS, all);
  };

  const follow = useCallback(
    (conversationId: string, runId: string) => {
      if (streams.current.has(runId)) return;
      const finish = async (status: string, err: string | null) => {
        const handle = streams.current.get(runId);
        handle?.close();
        streams.current.delete(runId);
        setRuns((prev) => ({ ...prev, [conversationId]: { runId, status, error: err } }));
        setPendingUser(null);
        if (err && status !== 'cancelled' && selectedRef.current === conversationId) setError(err);
        await refreshConversations().catch(() => undefined);
        if (selectedRef.current === conversationId) await loadThread(conversationId).catch(() => undefined);
        setApprovals(await listApprovals().catch(() => []));
      };
      const placeholder = { close: () => undefined } as StreamHandle;
      streams.current.set(runId, placeholder);
      followRun(
        runId,
        (type, data) => {
          if (isTerminal(type)) void finish(type, typeof data.error === 'string' ? data.error : null);
          else setRuns((prev) => ({ ...prev, [conversationId]: { runId, status: type, error: null } }));
        },
        () => {
          if (streams.current.has(runId)) {
            void getRun(runId).then((snap) => {
              if (isTerminal(snap.status)) void finish(snap.status, snap.error);
              else streams.current.delete(runId);
            });
          }
        }
      )
        .then((handle) => {
          if (streams.current.has(runId)) streams.current.set(runId, handle);
          else handle.close();
        })
        .catch((e) => {
          streams.current.delete(runId);
          setError(messageOf(e));
        });
    },
    [loadThread, refreshConversations]
  );

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        await importLegacyCeo().catch(() => undefined);
        const list = await listConversations();
        if (!alive) return;
        setConversations(list);
        const all = readJson<Record<string, string[]>>(KEY_RUNS, {});
        // Recover from the cache, and ask the worker about recent chats the cache does not know (cleared storage).
        const lastRun: Record<string, string | undefined> = {};
        for (const [cid, ids] of Object.entries(all)) lastRun[cid] = ids[ids.length - 1];
        const unknown = list.filter((c) => !(c.id in lastRun)).slice(0, 10);
        await Promise.all(
          unknown.map(async (c) => {
            const found = await listRuns(c.id, 1).catch(() => []);
            if (found[0]) lastRun[c.id] = found[0].id;
          })
        );
        if (!alive) return;
        const active: Record<string, RunState> = {};
        for (const [cid, last] of Object.entries(lastRun)) {
          if (!last) continue;
          const snap = await getRun(last).catch(() => null);
          if (snap && !isTerminal(snap.status)) {
            active[cid] = { runId: snap.id, status: snap.status, error: null };
            follow(cid, snap.id);
          }
        }
        if (!alive) return;
        setRuns(active);
        setApprovals(await listApprovals().catch(() => []));
      } catch (e) {
        if (alive) setError(messageOf(e));
      } finally {
        if (alive) setLoaded(true);
      }
    })();
    const open = streams.current;
    return () => {
      alive = false;
      for (const handle of open.values()) handle.close();
      open.clear();
    };
  }, [follow]);

  useEffect(() => {
    if (!selectedId || selectedId.startsWith('legacy:')) {
      setMessages([]);
      setRunsBySeq({});
      return;
    }
    let alive = true;
    loadThread(selectedId).catch((e) => alive && setError(messageOf(e)));
    return () => {
      alive = false;
    };
  }, [selectedId, loadThread]);

  const anyActive = Object.values(runs).some((r) => !isTerminal(r.status));
  useEffect(() => {
    if (!anyActive) return;
    const timer = window.setInterval(() => {
      listApprovals()
        .then(setApprovals)
        .catch(() => undefined);
    }, 1500);
    return () => window.clearInterval(timer);
  }, [anyActive]);

  const select = useCallback((id: string | null) => {
    setDraftAgentId(null);
    setSelectedId(id);
    writeJson(KEY_SELECTED, id);
    setError(null);
    setPendingUser(null);
  }, []);

  const setDraft = useCallback(
    (text: string) => {
      const key = selectedRef.current ?? '';
      setDraftMap((prev) => {
        const next = { ...prev };
        if (text) next[key] = text;
        else delete next[key];
        writeJson(KEY_DRAFTS, next);
        return next;
      });
    },
    []
  );

  const send = useCallback(
    async (text: string) => {
      const prompt = text.trim();
      if (!prompt) return;
      if (!engine?.model) {
        setError('No AI provider is ready. Open Settings > AI to sign in or pick a model.');
        return;
      }
      setError(null);
      let cid = selectedRef.current;
      const draftKey = cid ?? '';
      const known = cid && !cid.startsWith('legacy:') ? cid : null;
      const target = known
        ? (agents.find((a) => a.id === agentMapRef.current[known]) ?? ceo)
        : (agents.find((a) => a.id === draftAgentRef.current) ?? ceo);
      if ((!target || target.id === CEO_PROFILE_ID) && prompt.length > CEO_MAX_CHARS) {
        setError(
          `Loqman reads messages of up to ${CEO_MAX_CHARS.toLocaleString('en-US')} characters; this one has ${prompt.length.toLocaleString('en-US')}. Shorten it and send again.`
        );
        return;
      }
      let boundProfile: string | null | undefined = cid ? conversations.find((c) => c.id === cid)?.profile_id : null;
      try {
        if (!cid || cid.startsWith('legacy:')) {
          const created = await createConversation(
            prompt.replace(/\s+/g, ' ').slice(0, 60),
            target && target.id !== CEO_PROFILE_ID ? target.id : undefined
          );
          cid = created.id;
          boundProfile = created.profile_id;
          setConversations((prev) => [created, ...prev]);
          selectedRef.current = cid;
          setSelectedId(cid);
          writeJson(KEY_SELECTED, cid);
          const starter = agents.find((a) => a.id === draftAgentRef.current) ?? ceo;
          if (starter) bindAgent(cid, starter.id);
        }
        const agent = agents.find((a) => a.id === agentMapRef.current[cid!]) ?? ceo;
        const clearDraft = () =>
          setDraftMap((prev) => {
            const next = { ...prev };
            delete next[draftKey];
            writeJson(KEY_DRAFTS, next);
            return next;
          });
        if (!agent || agent.id === CEO_PROFILE_ID) {
          // Loqman (the CEO) answers through the CEO loop, which is the path that can create agents.
          const conversationId = cid;
          const ticket = `ceo-${crypto.randomUUID()}`;
          setPendingUser(prompt);
          clearDraft();
          setRuns((prev) => ({ ...prev, [conversationId]: { runId: ticket, status: 'running', error: null } }));
          try {
            // The worker stores both sides of the exchange in this chat (the question before generating, the
            // reply after), so a reload mid-generation keeps the message. A failed ask removes its question.
            const answer = await askCeo(prompt, engine.engine, engine.model, ticket, conversationId);
            if (answer.agents_created.length) onAgentsChanged?.();
          } catch (e) {
            setError(messageOf(e));
            setDraftMap((prev) => {
              const next = { ...prev, [conversationId]: prev[conversationId] || prompt };
              writeJson(KEY_DRAFTS, next);
              return next;
            });
          } finally {
            setPendingUser(null);
            setRuns((prev) => ({ ...prev, [conversationId]: { runId: ticket, status: 'succeeded', error: null } }));
            await refreshConversations().catch(() => undefined);
            const list = await listMessages(conversationId).catch(() => null);
            if (list && selectedRef.current === conversationId) setMessages(list);
          }
          return;
        }
        // A chat made before conversations were bound to their agent has no profile: bind it now so the
        // worker applies the agent's instructions; only if that fails does the first message carry the contract.
        let outgoing = prompt;
        if (agent && !boundProfile) {
          const bound = await bindConversationProfile(cid, agent.id).catch(() => null);
          if (bound) {
            setConversations((prev) => prev.map((c) => (c.id === bound.id ? bound : c)));
          } else if (agent.prompt && (await listMessages(cid).catch(() => [{}])).length === 0) {
            outgoing = `${CONTRACT_OPEN}
${agent.prompt}
${CONTRACT_CLOSE}

${prompt}`;
          }
        }
        setPendingUser(prompt);
        const snap = await startTurn({
          conversationId: cid,
          engine: engine.engine,
          modelId: engine.model,
          prompt: outgoing,
          requestKey: `ui-${crypto.randomUUID()}`,
        });
        remember(cid, snap.id);
        setDraftMap((prev) => {
          const next = { ...prev };
          delete next[draftKey];
          writeJson(KEY_DRAFTS, next);
          return next;
        });
        setRuns((prev) => ({ ...prev, [cid!]: { runId: snap.id, status: snap.status, error: null } }));
        const list = await listMessages(cid).catch(() => null);
        if (list && selectedRef.current === cid) setMessages(list);
        follow(cid, snap.id);
      } catch (e) {
        setPendingUser(null);
        setError(messageOf(e));
        throw e;
      }
    },
    [engine, follow, agents, ceo, bindAgent, onAgentsChanged, refreshConversations, conversations]
  );

  const openAgentChat = useCallback(
    async (agent: Agent) => {
      const existing = Object.entries(agentMapRef.current).find(
        ([cid, id]) => id === agent.id && conversations.some((c) => c.id === cid)
      )?.[0];
      if (existing) {
        select(existing);
        return existing;
      }
      const created = await createConversation(
        `${agent.name} — ${agent.role}`.slice(0, 120),
        agent.id !== CEO_PROFILE_ID ? agent.id : undefined
      );
      bindAgent(created.id, agent.id);
      setConversations((prev) => [created, ...prev]);
      select(created.id);
      return created.id;
    },
    [conversations, bindAgent, select]
  );

  const stop = useCallback(async () => {
    const cid = selectedRef.current;
    const run = cid ? runs[cid] : undefined;
    if (!run || run.runId.startsWith('ceo-')) return; // a CEO answer cannot be cancelled midway
    try {
      await cancelRun(run.runId);
    } catch (e) {
      setError(messageOf(e));
    }
  }, [runs]);

  const decide = useCallback(async (approval: Approval, decision: 'approve' | 'deny') => {
    try {
      await decideApproval(approval.run_id, approval.id, decision);
    } finally {
      setApprovals(await listApprovals().catch(() => []));
    }
  }, []);

  const makeDeliverable = useCallback(async (runId: string) => {
    try {
      await createDraft(runId);
    } catch (e) {
      setError(messageOf(e));
    }
    const list = await listDrafts(runId).catch(() => []);
    setDrafts((prev) => ({ ...prev, [runId]: list }));
  }, []);

  const loadAllDrafts = useCallback(async () => {
    const next: Record<string, Draft[]> = {};
    const fromWorker = await listAllDrafts().catch(() => null);
    if (fromWorker) {
      for (const d of fromWorker) (next[d.run_id] ??= []).push(d);
      for (const list of Object.values(next)) list.reverse(); // the worker lists newest first; cards read oldest first
    } else {
      const all = readJson<Record<string, string[]>>(KEY_RUNS, {});
      for (const id of Object.values(all).flat()) {
        const list = await listDrafts(id).catch(() => []);
        if (list.length) next[id] = list;
      }
    }
    setDrafts((prev) => ({ ...prev, ...next }));
  }, []);

  const reviewDraft = useCallback(async (draft: Draft, decision: 'approve' | 'reject') => {
    try {
      await decideDraft(draft.run_id, draft.id, decision, draft.content_sha256);
    } catch (e) {
      setError(messageOf(e));
    }
    const list = await listDrafts(draft.run_id).catch(() => []);
    setDrafts((prev) => ({ ...prev, [draft.run_id]: list }));
  }, []);

  const downloadDraft = useCallback(async (draft: Draft) => {
    try {
      const out = await draftContent(draft.run_id, draft.id);
      const url = URL.createObjectURL(new Blob([out.content], { type: out.media_type }));
      const link = document.createElement('a');
      link.href = url;
      link.download = out.filename;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
    } catch (e) {
      setError(messageOf(e));
    }
  }, []);

  const currentRun = selectedId ? (runs[selectedId] ?? null) : null;
  const activeRunIds = useMemo(() => {
    const out: Record<string, string> = {};
    for (const [cid, r] of Object.entries(runs)) if (!isTerminal(r.status)) out[cid] = r.runId;
    return out;
  }, [runs]);

  return {
    conversations,
    legacy,
    selectedId,
    select,
    messages,
    pendingUser,
    run: currentRun,
    runsBySeq,
    approvals,
    drafts,
    error,
    dismissError: () => setError(null),
    draft: draftMap[selectedId ?? ''] ?? '',
    setDraft,
    send,
    stop,
    newChat: () => {
      const current = agentFor(selectedRef.current)?.id ?? null;
      select(null);
      setDraftAgentId(current);
    },
    decide,
    makeDeliverable,
    reviewDraft,
    downloadDraft,
    loaded,
    activeRunIds,
    agentFor,
    openAgentChat,
    reloadConversations: refreshConversations,
    loadAllDrafts,
  };
}
