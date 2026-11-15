import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  CEO_PROFILE_ID,
  bindConversationProfile,
  askCeo,
  ceoAskProgress,
  type CeoProgress,
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
import { emptyLive, freezeLive, hasLiveContent, reduceLive, type LiveTurn } from './liveTurn';
import { readLegacyThreads, type LegacyThread } from '../data/legacy';
import type { Agent } from '../types';
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

/** The conversation the legacy import creates for old CEO exchanges; hidden while it has nothing in it. */
const CEO_HISTORY_ID = 'legacy-ceo-history';
const CEO_PROGRESS_MS = 1500;

/** One short line for the pending reply area, from what ceo.ask_progress reports. */
export function progressText(p: CeoProgress): string | null {
  const step = p.iteration > 1 ? ` (step ${p.iteration})` : '';
  switch (p.stage) {
    case 'starting':
      return 'Starting';
    case 'fast':
      return 'Answering';
    case 'thinking':
      return `Thinking${step}`;
    case 'tool':
      return p.detail ? `Using ${p.detail.slice(0, 40)}${step}` : `Working${step}`;
    case 'final':
      return 'Writing the answer';
    default:
      return null;
  }
}

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
  /** What Loqman is doing right now in the selected chat, while he answers; null otherwise. */
  progress: string | null;
  run: RunState | null;
  /** The reply as it streams in (normalised driver events) in the selected chat; null when nothing is streaming. */
  live: LiveTurn | null;
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
  const [ceoStep, setCeoStep] = useState<{ cid: string; text: string } | null>(null);
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  const historyUsed = useRef(false);
  const [runs, setRuns] = useState<Record<string, RunState>>({});
  const [live, setLive] = useState<Record<string, LiveTurn>>({});
  const liveRuns = useRef(new Map<string, LiveTurn>());
  const stoppedRuns = useRef(new Set<string>());
  const liveDirty = useRef(new Map<string, string>());
  const liveFrame = useRef<number | null>(null);
  const liveLastFlush = useRef(0);
  /** Publish the streamed text to React at most about every 60 ms and once per frame, however many deltas arrived. */
  const scheduleLive = useCallback((conversationId: string, runId: string) => {
    liveDirty.current.set(conversationId, runId);
    if (liveFrame.current !== null) return;
    const tick = () => {
      if (performance.now() - liveLastFlush.current < 60) {
        liveFrame.current = requestAnimationFrame(tick);
        return;
      }
      liveFrame.current = null;
      liveLastFlush.current = performance.now();
      const entries = [...liveDirty.current];
      liveDirty.current.clear();
      setLive((prev) => {
        const next = { ...prev };
        for (const [cid, rid] of entries) {
          const turn = liveRuns.current.get(rid);
          if (turn) next[cid] = turn;
        }
        return next;
      });
    };
    liveFrame.current = requestAnimationFrame(tick);
  }, []);
  const applyLive = useCallback(
    (conversationId: string, runId: string, type: string, data: Record<string, unknown>) => {
      if (stoppedRuns.current.has(runId)) return;
      const cur = liveRuns.current.get(runId) ?? emptyLive(runId);
      const next = reduceLive(cur, type, data);
      if (next === cur) return;
      liveRuns.current.set(runId, next);
      scheduleLive(conversationId, runId);
    },
    [scheduleLive]
  );
  const dropLive = useCallback((conversationId: string, runId: string) => {
    liveRuns.current.delete(runId);
    stoppedRuns.current.delete(runId);
    if (liveDirty.current.get(conversationId) === runId) liveDirty.current.delete(conversationId);
    setLive((prev) => {
      if (!(conversationId in prev)) return prev;
      const next = { ...prev };
      delete next[conversationId];
      return next;
    });
  }, []);
  useEffect(
    () => () => {
      if (liveFrame.current !== null) cancelAnimationFrame(liveFrame.current);
    },
    []
  );
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

  /** The CEO history conversation is hidden while it is empty (it is created for every user by the import). Nothing is deleted. */
  const withoutEmptyHistory = useCallback(async (list: Conversation[]) => {
    const history = list.find((c) => c.id === CEO_HISTORY_ID);
    if (!history || historyUsed.current) return list;
    const msgs = await listMessages(history.id).catch(() => null);
    if (msgs === null) return list;
    if (msgs.length > 0) {
      historyUsed.current = true;
      return list;
    }
    return list.filter((c) => c.id !== history.id);
  }, []);

  const refreshConversations = useCallback(async () => {
    setConversations(await withoutEmptyHistory(await listConversations()));
  }, [withoutEmptyHistory]);

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
        const partial = liveRuns.current.get(runId);
        if (status === 'cancelled' && hasLiveContent(partial)) {
          // Keep what was written, marked as stopped; a reload shows only what the worker stored.
          const kept = freezeLive(partial);
          liveRuns.current.set(runId, kept);
          setLive((prev) => ({ ...prev, [conversationId]: kept }));
        } else {
          // The stored reply replaces the live one in the same render, so the text never shows twice or disappears.
          const stored = status === 'succeeded' ? await listMessages(conversationId).catch(() => null) : null;
          if (stored && selectedRef.current === conversationId) setMessages(stored);
          dropLive(conversationId, runId);
        }
        await refreshConversations().catch(() => undefined);
        if (selectedRef.current === conversationId) await loadThread(conversationId).catch(() => undefined);
        setApprovals(await listApprovals().catch(() => []));
      };
      const placeholder = { close: () => undefined } as StreamHandle;
      streams.current.set(runId, placeholder);
      followRun(
        runId,
        (type, data) => {
          // Only a lifecycle terminal ends the run; driver events feed the live reply; anything else is ignored.
          if (isTerminal(type)) void finish(type, typeof data.error === 'string' ? data.error : null);
          else if (type === 'queued' || type === 'running') {
            setRuns((prev) => ({ ...prev, [conversationId]: { runId, status: type, error: null } }));
          } else applyLive(conversationId, runId, type, data);
        },
        () => {
          if (streams.current.has(runId)) {
            void getRun(runId).then((snap) => {
              if (isTerminal(snap.status)) void finish(snap.status, snap.error);
              else streams.current.delete(runId);
            });
          }
        },
        0,
        'normalised-v1'
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
    [loadThread, refreshConversations, applyLive, dropLive]
  );

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        await importLegacyCeo().catch(() => undefined);
        const all0 = await listConversations();
        const list = await withoutEmptyHistory(all0);
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
  }, [follow, withoutEmptyHistory]);

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
          // Ask the worker where the answer has got to. One request at a time; it ends with the ask, a chat switch or unmount.
          let polling = true;
          void (async () => {
            while (polling) {
              await new Promise((r) => window.setTimeout(r, CEO_PROGRESS_MS));
              if (!polling || !mounted.current || selectedRef.current !== conversationId) return;
              const p = await ceoAskProgress(ticket).catch(() => null);
              if (!polling || !mounted.current || selectedRef.current !== conversationId) return;
              if (p) {
                const text = progressText(p);
                if (text) setCeoStep({ cid: conversationId, text });
                if (p.done) return;
              }
            }
          })();
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
            polling = false;
            setCeoStep(null);
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
        setLive((prev) => {
          if (!(cid! in prev)) return prev;
          const next = { ...prev };
          delete next[cid!];
          return next;
        });
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
    // The text stops changing the moment Stop is pressed, before the worker confirms.
    stoppedRuns.current.add(run.runId);
    const turn = liveRuns.current.get(run.runId);
    if (turn && cid) {
      const frozen = freezeLive(turn);
      liveRuns.current.set(run.runId, frozen);
      setLive((prev) => ({ ...prev, [cid]: frozen }));
    }
    try {
      await cancelRun(run.runId);
    } catch (e) {
      stoppedRuns.current.delete(run.runId);
      if (turn && cid) {
        liveRuns.current.set(run.runId, turn);
        setLive((prev) => ({ ...prev, [cid]: turn }));
      }
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
    progress: ceoStep && ceoStep.cid === selectedId && currentRun && !isTerminal(currentRun.status) ? ceoStep.text : null,
    run: currentRun,
    live: selectedId ? (live[selectedId] ?? null) : null,
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
