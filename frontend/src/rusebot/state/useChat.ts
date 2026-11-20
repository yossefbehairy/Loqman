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
import { ApiError, type StreamHandle } from '../../api/transport';
import { emptyLive, freezeLive, hasLiveContent, reduceLive, type LiveTurn } from './liveTurn';
import { readLegacyThreads, type LegacyThread } from '../data/legacy';
import type { Agent } from '../types';
import {
  currentExecutionChoice,
  isExecutableChoice,
  loadProviderModels,
  selectProviderEngine,
  setProviderChoice,
  useProviders,
  type EngineChoice,
} from './useProviders';

const KEY_DRAFTS = 'agentos.rusebot.drafts.v1';
const CEO_SUBMISSION_PREFIX = 'ceo-submission:';
const SUBMISSION_PREFIX = 'agentos.rusebot.submission.v1.';
const KEY_RUNS = 'agentos.rusebot.runs.v1';
const KEY_SELECTED = 'agentos.rusebot.selected.v1';
const KEY_AGENTS = 'agentos.rusebot.convAgents.v1';

/** ceo.ask refuses longer questions (backend/pyworker/api/ceo.py), so refuse them before anything is saved. */
export const CEO_MAX_CHARS = 2000;

/** The conversation the legacy import creates for old CEO exchanges; hidden while it has nothing in it. */
const CEO_HISTORY_ID = 'legacy-ceo-history';
const CEO_PROGRESS_MS = 1500;
const RUN_STREAM_IDLE_MS = 15_000;
const RUN_STATUS_RETRY_MAX_MS = 15_000;
const RUN_STATUS_SLOW_MS = 8_000;
const runConnectionWarning = 'Could not verify this run’s status with the worker. Still checking for updates.';

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
  catalogStatus: 'loading' | 'ready' | 'error' | undefined;
  executionReady: boolean;
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
    catalogStatus: s.choice ? s.models[s.choice.engine]?.status : undefined,
    executionReady: isExecutableChoice(s.choice),
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
  hideSubmittedDraft?: boolean;
  connectionWarning?: boolean;
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
  /** Runs the failed step again (resend the message, reload the chat or the list); null when there is nothing to retry. */
  retry: (() => void) | null;
  /** The selected chat's messages could not be read: it is not empty, it just did not load. */
  threadError: string | null;
  retryThread: () => void;
  /** Stop was pressed and the worker has not confirmed yet. */
  stopping: boolean;
  sending: boolean;
  /** The selected run can be cancelled (a Loqman answer cannot once it has started). */
  canStop: boolean;
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
  selectedAgentId: string | null;
  agentFor: (conversationId: string | null) => Agent | null;
  openAgentChat: (agent: Agent) => Promise<string>;
  reloadConversations: () => Promise<void>;
  loadAllDrafts: () => Promise<void>;
}

export function messageOf(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

interface ExecutionSettings {
  /** Absent on recovery records written before execution settings were saved. */
  execution?: EngineChoice;
  /** The first legacy turn may include an agent contract in its worker payload. */
  wirePrompt?: string;
}

interface SendAttempt extends ExecutionSettings {
  cid: string | null;
  agentId: string;
  prompt: string;
  draftRevision: symbol | undefined;
  createdCeoChat?: boolean;
  requestId?: string;
  deferredSource?: string;
  /** Source draft kept when the created chat already has independent work. */
  retainedSourceKey?: string;
}

interface SubmissionRecord extends ExecutionSettings {
  cid: string | null;
  agentId: string;
  prompt: string;
  requestId: string;
  ownsDraft: boolean;
  rejected?: boolean;
  createdCeoChat?: boolean;
  deferredSource?: string;
}

function readSubmissions(): Map<string, SubmissionRecord> {
  const records = new Map<string, SubmissionRecord>();
  try {
    for (const key of Object.keys(localStorage)) {
      if (!key.startsWith(SUBMISSION_PREFIX)) continue;
      const record = readJson<Partial<SubmissionRecord> | null>(key, null);
      if (!record || (record.cid !== null && (typeof record.cid !== 'string' || !record.cid)) || typeof record.agentId !== 'string' ||
          !record.agentId || typeof record.prompt !== 'string' || !record.prompt.trim() ||
          typeof record.ownsDraft !== 'boolean' || typeof record.requestId !== 'string' ||
          key !== SUBMISSION_PREFIX + record.requestId ||
          !/^(ui|ceo)-[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/.test(record.requestId) ||
          record.requestId.startsWith('ceo-') !== (record.agentId === CEO_PROFILE_ID) ||
          (record.deferredSource !== undefined && record.deferredSource !== chatOwnerKey(null, record.agentId) &&
            !(typeof record.deferredSource === 'string' && record.deferredSource.startsWith('legacy:')))) continue;
      if (typeof record.execution?.engine !== 'string' || !record.execution.engine ||
          typeof record.execution.model !== 'string' || !record.execution.model ||
          (record.wirePrompt !== undefined && typeof record.wirePrompt !== 'string')) delete record.execution;
      records.set(record.requestId, record as SubmissionRecord);
    }
  } catch { /* Storage unavailable. Live sends still use in-memory state. */ }
  return records;
}

const deliveryWarning = 'Delivery was not confirmed. Check the conversation before retrying';
const creationWarning = 'Conversation creation was not confirmed. Check your chat list before retrying';
const rejectionNotice = 'The worker rejected this request';
const missingSettings = 'The original execution settings were not saved. Check the conversation before dismissing this notice and sending again';
const missingLegacyProfilesKey = 'agentos.rusebot.missingLegacyProfiles.v1';
const missingLegacyProfile = 'This chat’s specialist is no longer available. Your draft stays here. Retry to check the binding again, or open a chat with an available specialist';

function needsConversation(cid: string | null): cid is null | `legacy:${string}` {
  return cid === null || cid.startsWith('legacy:');
}

function chatOwnerKey(cid: string | null, agentId: string): string {
  // The reserved CEO profile ID owns the unsaved Loqman slot.
  return cid ?? `new:${agentId}`;
}

/** An error shown in the chat, with what "Retry" does about it. */
interface ChatErrorEntry {
  message: string;
  retry: SendAttempt | 'load' | null;
}

const NO_MESSAGES: ConvMessage[] = [];
const NO_RUNS: Record<number, RunSnapshot> = {};

function cacheRuns(conversationId: string, ids: string[]): void {
  const all = readJson<Record<string, string[]>>(KEY_RUNS, {});
  all[conversationId] = ids.slice(-30);
  writeJson(KEY_RUNS, all);
}

export function useChat(engine: EngineChoice | null, agents: Agent[] = [], onAgentsChanged?: () => void): ChatState {
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [legacy] = useState<LegacyThread[]>(() => readLegacyThreads());
  const [selectedId, setSelectedId] = useState<string | null>(() => readJson<string | null>(KEY_SELECTED, null));
  // The messages and runs of ONE chat, tagged with it: after a switch the old chat's text is never shown in the new one.
  const [thread, setThread] = useState<{ cid: string | null; list: ConvMessage[] }>({ cid: null, list: NO_MESSAGES });
  const [runsThread, setRunsThread] = useState<{ cid: string | null; map: Record<number, RunSnapshot> }>({ cid: null, map: NO_RUNS });
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
  // Stream callbacks and RPC replies can arrive before React commits the previous run update.
  const currentRuns = useRef(runs);
  const updateRuns = useCallback((update: (current: Record<string, RunState>) => Record<string, RunState>) => {
    const next = update(currentRuns.current);
    if (next === currentRuns.current) return;
    currentRuns.current = next;
    setRuns(next);
  }, []);
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
          if (turn && currentRuns.current[cid]?.runId === rid) next[cid] = turn;
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
      if (prev[conversationId]?.runId !== runId) return prev;
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
  const [approvals, setApprovals] = useState<Approval[]>([]);
  const [drafts, setDrafts] = useState<Record<string, Draft[]>>({});
  // Keep the legacy '' entry in storage for recovery. Its agent is unknown.
  const [draftMap, setDraftMap] = useState<Record<string, string>>(() => readJson(KEY_DRAFTS, {}));
  const [submissions] = useState(readSubmissions);
  // Keep a definitive rejection across reloads, even when its submission is dismissed.
  const [missingLegacyProfiles] = useState(() => new Set(readJson<string[]>(missingLegacyProfilesKey, [])));
  const createdCeoSubmissions = useRef(new Map<string, SendAttempt>());
  const forgetSubmission = useCallback((id: string) => {
    const record = submissions.get(id);
    submissions.delete(id);
    try { localStorage.removeItem(SUBMISSION_PREFIX + id); } catch { /* storage unavailable */ }
    if (!record?.createdCeoChat || !record.cid) return;
    createdCeoSubmissions.current.delete(record.cid);
    const compatibilityKey = CEO_SUBMISSION_PREFIX + record.cid;
    setDraftMap((prev) => {
      if (prev[compatibilityKey] !== record.prompt) return prev;
      const next = { ...prev };
      delete next[compatibilityKey];
      writeJson(KEY_DRAFTS, next);
      return next;
    });
  }, [submissions]);
  const [errors, setErrors] = useState<Record<string, ChatErrorEntry>>(() => {
    const recovered: Record<string, ChatErrorEntry> = {};
    for (const [key, prompt] of Object.entries(draftMap)) {
      if (!key.startsWith(CEO_SUBMISSION_PREFIX) || typeof prompt !== 'string' || !prompt.trim()) continue;
      const cid = key.slice(CEO_SUBMISSION_PREFIX.length);
      if ([...submissions.values()].some(record => record.cid === cid && record.createdCeoChat && record.prompt === prompt)) continue;
      recovered[cid] = {
        message: `${missingSettings}: ${prompt}`,
        // The ordinary draft belongs to a separate edit, even if its text is identical.
        retry: { cid, agentId: CEO_PROFILE_ID, prompt, draftRevision: Symbol(), createdCeoChat: true },
      };
    }
    for (const record of submissions.values()) {
      const owner = chatOwnerKey(record.cid, record.agentId);
      const draft = draftMap[owner];
      const notice = !record.execution ? missingSettings : missingLegacyProfiles.has(record.agentId) ? missingLegacyProfile :
        record.rejected === true ? rejectionNotice : needsConversation(record.cid) ? creationWarning : deliveryWarning;
      recovered[SUBMISSION_PREFIX + record.requestId] = {
        message: `${notice}: ${record.prompt}`,
        retry: { ...record,
          draftRevision: record.ownsDraft && typeof draft === 'string' && draft.trim() === record.prompt ? undefined : Symbol() },
      };
    }
    return recovered;
  });
  const [threadErrors, setThreadErrors] = useState<Record<string, string>>({});
  const [stoppingRuns, setStoppingRuns] = useState<Record<string, true>>({});
  const [sendLocks, setSendLocks] = useState<ReadonlyMap<string, symbol>>(() => new Map());
  const sending = useRef(sendLocks);
  const publishLocks = useCallback((locks: Map<string, symbol>) => {
    sending.current = locks;
    setSendLocks(locks);
  }, []);
  const draftRevisions = useRef(new Map<string, symbol | undefined>());
  const selectionRevision = useRef(0);
  const raise = useCallback((cid: string | null, message: string, retry: ChatErrorEntry['retry'] = null) => {
    setErrors((prev) => ({ ...prev, [cid ?? '']: { message, retry } }));
  }, []);
  const clearError = useCallback((cid: string | null) => {
    setErrors((prev) => {
      if (!((cid ?? '') in prev)) return prev;
      const next = { ...prev };
      delete next[cid ?? ''];
      return next;
    });
  }, []);
  const [loaded, setLoaded] = useState(false);
  const [draftAgentId, setDraftAgentId] = useState<string | null>(null);
  const [agentMap, setAgentMap] = useState<Record<string, string>>(() => readJson(KEY_AGENTS, {}));
  const agentMapRef = useRef(agentMap);
  agentMapRef.current = agentMap;
  const bindAgent = useCallback((cid: string, agentId: string) => {
    const next = { ...agentMapRef.current, [cid]: agentId };
    agentMapRef.current = next;
    setAgentMap(next);
    writeJson(KEY_AGENTS, next);
  }, []);
  const newChatAgentId = draftAgentId ?? CEO_PROFILE_ID;
  const recipientIdFor = useCallback((cid: string | null) => {
    if (cid === null) return newChatAgentId;
    if (cid.startsWith('legacy:')) return legacy.some((t) => `legacy:${t.id}` === cid) ? CEO_PROFILE_ID : null;
    const row = conversations.find((c) => c.id === cid);
    // Only a known, unbound row can use the browser's legacy recipient hint.
    return row ? (row.profile_id ?? agentMap[cid] ?? CEO_PROFILE_ID) : null;
  }, [newChatAgentId, legacy, conversations, agentMap]);
  const agentFor = useCallback((cid: string | null) => {
    const id = recipientIdFor(cid);
    return agents.find((a) => a.id === id) ?? null;
  }, [agents, recipientIdFor]);
  const selectedOwnerKey = chatOwnerKey(selectedId, newChatAgentId);
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

  // Every read of a chat takes a number; an older answer that arrives after a newer one is dropped,
  // and an answer for a chat that is no longer open is never shown.
  const readSeq = useRef(new Map<string, number>());
  const nextRead = (cid: string) => {
    const n = (readSeq.current.get(cid) ?? 0) + 1;
    readSeq.current.set(cid, n);
    return n;
  };
  const fetchMessages = useCallback(async (conversationId: string): Promise<ConvMessage[] | null> => {
    const ticket = nextRead(conversationId);
    const list = await listMessages(conversationId);
    if (readSeq.current.get(conversationId) !== ticket) return null;
    if (selectedRef.current === conversationId) setThread({ cid: conversationId, list });
    return list;
  }, []);

  const loadThread = useCallback(async (conversationId: string) => {
    const ticket = nextRead(conversationId);
    setThreadErrors((prev) => {
      if (!(conversationId in prev)) return prev;
      const next = { ...prev };
      delete next[conversationId];
      return next;
    });
    const list = await listMessages(conversationId);
    if (readSeq.current.get(conversationId) === ticket && selectedRef.current === conversationId) {
      setThread({ cid: conversationId, list });
      const run = currentRuns.current[conversationId];
      if (run?.status === 'succeeded' && liveRuns.current.has(run.runId)) dropLive(conversationId, run.runId);
      // CEO replies carry the ask ID. A matching prompt alone cannot identify a submission.
      for (const record of submissions.values()) {
        if (record.cid !== conversationId || record.agentId !== CEO_PROFILE_ID || sending.current.has(conversationId) ||
            !list.some((m) => m.role === 'assistant' && m.client_message_id === `ask:${record.requestId}:assistant`)) continue;
        forgetSubmission(record.requestId);
        clearError(SUBMISSION_PREFIX + record.requestId);
        if (record.ownsDraft) setDraftMap((prev) => {
          if (prev[conversationId]?.trim() !== record.prompt) return prev;
          const next = { ...prev };
          delete next[conversationId];
          writeJson(KEY_DRAFTS, next);
          return next;
        });
      }
    }
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
    if (selectedRef.current === conversationId && readSeq.current.get(conversationId) === ticket) {
      setRunsThread({ cid: conversationId, map: bySeq });
      setDrafts((prev) => ({ ...prev, ...nextDrafts }));
    }
    return snaps.filter((s): s is RunSnapshot => s !== null);
  }, [submissions, forgetSubmission, clearError, dropLive]);

  const remember = (conversationId: string, runId: string) => {
    const all = readJson<Record<string, string[]>>(KEY_RUNS, {});
    const list = all[conversationId] ?? [];
    if (!list.includes(runId)) all[conversationId] = [...list, runId];
    writeJson(KEY_RUNS, all);
  };

  const closeFollower = useCallback((runId: string) => {
    const follower = streams.current.get(runId);
    streams.current.delete(runId);
    follower?.close();
  }, []);

  const clearStopping = useCallback((runId: string) => {
    setStoppingRuns((prev) => {
      if (!(runId in prev)) return prev;
      const next = { ...prev };
      delete next[runId];
      return next;
    });
  }, []);

  const finishRun = useCallback(async (conversationId: string, runId: string, status: string, error: string | null) => {
    const ownsRun = () => mounted.current && currentRuns.current[conversationId]?.runId === runId;
    const current = currentRuns.current[conversationId];
    if (!mounted.current || (current && (current.runId !== runId || isTerminal(current.status)))) return;
    closeFollower(runId);
    updateRuns((prev) => ({ ...prev, [conversationId]: { runId, status, error } }));
    clearStopping(runId);
    if (selectedRef.current === conversationId) setPendingUser(null);
    if (error && status !== 'cancelled') raise(conversationId, error);

    const partial = liveRuns.current.get(runId);
    if (status === 'cancelled' && hasLiveContent(partial)) {
      const kept = freezeLive(partial);
      liveRuns.current.set(runId, kept);
      setLive((prev) => ({ ...prev, [conversationId]: kept }));
    } else if (status === 'succeeded') {
      // The terminal event and snapshot are published after the reply is committed.
      const history = await fetchMessages(conversationId).catch(() => null);
      if (history !== null) dropLive(conversationId, runId);
    } else {
      dropLive(conversationId, runId);
    }
    if (!ownsRun()) return;
    await refreshConversations().catch(() => undefined);
    if (ownsRun() && selectedRef.current === conversationId) {
      await loadThread(conversationId).catch((error: unknown) => {
        if (ownsRun() && selectedRef.current === conversationId) {
          setThreadErrors((prev) => ({ ...prev, [conversationId]: messageOf(error) }));
        }
      });
    }
    if (!ownsRun()) return;
    const pending = await listApprovals().catch(() => []);
    if (ownsRun()) setApprovals(pending);
  }, [clearStopping, closeFollower, dropLive, fetchMessages, loadThread, raise, refreshConversations, updateRuns]);

  const follow = useCallback((conversationId: string, runId: string) => {
    if (!mounted.current || streams.current.has(runId) || currentRuns.current[conversationId]?.runId !== runId) return;
    let handle: StreamHandle | undefined;
    let subscription: symbol | undefined;
    let timer: number | undefined;
    let checking = false;
    let lastEventId = 0;
    let lastActivity = performance.now();
    let retries = 0;
    let statusFailures = 0;

    const closeSubscription = () => {
      subscription = undefined;
      handle?.close();
      handle = undefined;
    };
    const follower = {
      close() {
        window.clearTimeout(timer);
        closeSubscription();
      },
    };
    const isCurrent = () => mounted.current && streams.current.get(runId) === follower &&
      currentRuns.current[conversationId]?.runId === runId;
    const warning = (visible: boolean) => {
      if (!isCurrent()) return;
      updateRuns((prev) => {
        const run = prev[conversationId];
        return Boolean(run.connectionWarning) === visible ? prev :
          { ...prev, [conversationId]: { ...run, connectionWarning: visible } };
      });
    };
    const schedule = (callback: () => void, delay: number) => {
      window.clearTimeout(timer);
      timer = window.setTimeout(callback, delay);
    };
    const retryDelay = () => {
      const attempt = retries++;
      return attempt === 0 ? 0 : Math.min(500 * 2 ** Math.min(attempt - 1, 5), RUN_STATUS_RETRY_MAX_MS);
    };
    const markRunning = () => {
      updateRuns((prev) => {
        const run = prev[conversationId];
        return run.status === 'queued' ? { ...prev, [conversationId]: { ...run, status: 'running' } } : prev;
      });
    };

    function checkWhenQuiet() {
      if (!isCurrent()) return;
      const remaining = RUN_STREAM_IDLE_MS - (performance.now() - lastActivity);
      if (remaining > 0) schedule(checkWhenQuiet, remaining);
      else void checkStatus();
    }

    async function checkStatus() {
      if (!isCurrent() || checking) return;
      checking = true;
      schedule(() => warning(true), RUN_STATUS_SLOW_MS);
      const snap = await getRun(runId).catch(() => null);
      checking = false;
      if (!isCurrent()) return;
      window.clearTimeout(timer);
      if (!snap || snap.id !== runId || snap.conversation_id !== conversationId) {
        if (++statusFailures >= 3) warning(true);
        schedule(() => void checkStatus(), retryDelay());
        return;
      }
      statusFailures = 0;
      warning(false);
      if (isTerminal(snap.status)) {
        void finishRun(conversationId, runId, snap.status, snap.error);
        return;
      }
      if (snap.status === 'running') markRunning();
      if (!subscription) connect();
      else {
        // EventSource can reconnect on its own. Check status after silence without opening another stream.
        schedule(checkWhenQuiet, RUN_STREAM_IDLE_MS);
      }
    }

    function connect() {
      const attempt = Symbol();
      subscription = attempt;
      const isSubscribed = () => isCurrent() && subscription === attempt;
      const disconnected = () => {
        if (!isSubscribed()) return;
        closeSubscription();
        if (!checking) schedule(() => void checkStatus(), retryDelay());
      };
      lastActivity = performance.now();
      schedule(checkWhenQuiet, RUN_STREAM_IDLE_MS);
      const after = lastEventId;
      let replayThrough = after;
      void followRun(runId, (type, data, id) => {
        if (!isSubscribed()) return;
        if (typeof id === 'number') {
          // A browser reconnect can replay from the original URL cursor.
          if (id < lastEventId) replayThrough = lastEventId;
          if (id <= replayThrough) return;
          lastEventId = id;
        }
        lastActivity = performance.now();
        retries = 0;
        statusFailures = 0;
        warning(false);
        if (isTerminal(type)) void finishRun(conversationId, runId, type, typeof data.error === 'string' ? data.error : null);
        else if (type === 'running') markRunning();
        else if (type !== 'queued') applyLive(conversationId, runId, type, data);
      }, disconnected, after, 'normalised-v1').then((opened) => {
        if (isSubscribed()) handle = opened;
        else opened.close();
      }).catch(disconnected);
    }

    streams.current.set(runId, follower);
    connect();
  }, [applyLive, finishRun, updateRuns]);

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
        for (const [cid, last] of Object.entries(lastRun)) {
          if (!last) continue;
          const snap = await getRun(last).catch(() => null);
          if (!alive) return;
          if (snap && !isTerminal(snap.status)) {
            updateRuns((prev) => prev[cid] ? prev : {
              ...prev, [cid]: { runId: snap.id, status: snap.status, error: null },
            });
            const current = currentRuns.current[cid];
            if (current.runId === snap.id && !isTerminal(current.status)) follow(cid, snap.id);
          }
        }
        if (!alive) return;
        setApprovals(await listApprovals().catch(() => []));
      } catch (e) {
        if (alive) raise(null, `Could not load your chats. ${messageOf(e)}`, 'load');
      } finally {
        if (alive) setLoaded(true);
      }
    })();
    const open = streams.current;
    return () => {
      alive = false;
      for (const id of open.keys()) closeFollower(id);
    };
  }, [closeFollower, follow, withoutEmptyHistory, raise, updateRuns]);

  useEffect(() => {
    if (!selectedId || selectedId.startsWith('legacy:')) {
      setThread({ cid: null, list: NO_MESSAGES });
      setRunsThread({ cid: null, map: NO_RUNS });
      return;
    }
    let alive = true;
    loadThread(selectedId).catch((e) => {
      if (alive) setThreadErrors((prev) => ({ ...prev, [selectedId]: messageOf(e) }));
    });
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
    selectionRevision.current++;
    selectedRef.current = id;
    setDraftAgentId(null);
    setSelectedId(id);
    writeJson(KEY_SELECTED, id);
    setPendingUser(null);
  }, []);

  const setDraft = useCallback(
    (text: string) => {
      const key = selectedOwnerKey;
      for (const record of submissions.values()) {
        if (chatOwnerKey(record.cid, record.agentId) === key && record.ownsDraft) {
          record.ownsDraft = false;
          writeJson(SUBMISSION_PREFIX + record.requestId, record);
        }
      }
      const submitted = createdCeoSubmissions.current.get(key);
      draftRevisions.current.set(key, Symbol());
      updateRuns((prev) => prev[key]?.hideSubmittedDraft
        ? { ...prev, [key]: { ...prev[key], hideSubmittedDraft: false } }
        : prev);
      setDraftMap((prev) => {
        const next = { ...prev };
        if (submitted) next[CEO_SUBMISSION_PREFIX + key] = submitted.prompt;
        if (text) next[key] = text;
        else delete next[key];
        writeJson(KEY_DRAFTS, next);
        return next;
      });
    },
    [selectedOwnerKey, submissions, updateRuns]
  );

  const sendAttempt = useCallback(
    async (attempt: SendAttempt, retryOwner?: string) => {
      const { prompt, agentId, draftRevision } = attempt;
      let { cid } = attempt;
      let key = chatOwnerKey(cid, agentId);
      const errorOwner = retryOwner ?? key;
      let retainedSourceKey = attempt.retainedSourceKey;
      const deferredSource = attempt.deferredSource;
      let recoverySource = deferredSource;
      const previous = createdCeoSubmissions.current.get(key);
      // Pressing Send on the unchanged restored draft acknowledges the same submission as Retry.
      const createdCeoChat = agentId === CEO_PROFILE_ID &&
        (attempt.createdCeoChat || needsConversation(cid) ||
          (previous?.prompt === prompt && previous.draftRevision === draftRevision));
      if (sending.current.has(key) || (deferredSource && sending.current.has(deferredSource))) return;
      const execution = attempt.execution;
      if (!execution) {
        raise(errorOwner, `${missingSettings}: ${prompt}`, attempt);
        return;
      }
      if (agentId === CEO_PROFILE_ID && prompt.length > CEO_MAX_CHARS) {
        raise(
          errorOwner,
          `Loqman reads messages of up to ${CEO_MAX_CHARS.toLocaleString('en-US')} characters; this one has ${prompt.length.toLocaleString('en-US')}. Shorten it and send again.`
        );
        return;
      }
      clearError(errorOwner);
      const lock = Symbol();
      const locks = new Map(sending.current).set(key, lock);
      if (deferredSource) locks.set(deferredSource, lock);
      publishLocks(locks);
      const releaseLock = () => {
        const current = sending.current;
        if (current.get(key) !== lock) return;
        const next = new Map(current);
        next.delete(key);
        if (deferredSource && next.get(deferredSource) === lock) next.delete(deferredSource);
        publishLocks(next);
      };
      const selectionAtStart = selectionRevision.current;
      let boundProfile = conversations.find((c) => c.id === cid)?.profile_id;
      const ownsDraft = (owner: string) => draftRevisions.current.get(owner) === draftRevision;
      const requestId = attempt.requestId ?? (agentId === CEO_PROFILE_ID ? 'ceo-' : 'ui-') + crypto.randomUUID();
      const submissionKey = SUBMISSION_PREFIX + requestId;
      let previewedSpecialistPrompt = false;
      let wirePrompt = attempt.wirePrompt;
      const recoveryAttempt = (): SendAttempt => ({ ...attempt, cid, wirePrompt, retainedSourceKey, createdCeoChat, requestId,
        deferredSource: recoverySource });
      const persist = (owned: boolean, recoveryFallback?: string) => {
        const record: SubmissionRecord = { cid, agentId, prompt, execution, wirePrompt, requestId, ownsDraft: owned,
          createdCeoChat, deferredSource: recoverySource };
        try {
          localStorage.setItem(submissionKey, JSON.stringify(record));
          submissions.set(requestId, record);
          return true;
        } catch {
          raise(submissionKey, `Could not save this submission for recovery. Sending is paused until browser storage is available: ${prompt}`,
            { ...recoveryAttempt(), deferredSource: recoveryFallback ?? recoverySource });
          return false;
        }
      };
      const fail = (message: string, cause?: unknown, noticeOverride?: string) => {
        const rejected = cause instanceof ApiError && cause.status === 422;
        const record = submissions.get(requestId);
        if (record && rejected) {
          record.rejected = true;
          writeJson(submissionKey, record);
        }
        const notice = noticeOverride ?? (rejected ? rejectionNotice : needsConversation(cid) ? creationWarning : deliveryWarning);
        raise(submissionKey, `${message}. ${notice}: ${prompt}`, recoveryAttempt());
        const draftKey = key;
        if (!ownsDraft(draftKey)) return;
        setDraftMap((prev) => {
          if (prev[draftKey]) return prev;
          const next = { ...prev, [draftKey]: prompt };
          writeJson(KEY_DRAFTS, next);
          return next;
        });
      };
      try {
        if (!persist(ownsDraft(key))) return;
        // A restored attempt may target an engine whose catalog has not loaded in this session.
        if (attempt.requestId && !isExecutableChoice(execution)) await loadProviderModels(execution.engine);
        const requireOriginalModel = () => {
          if (!isExecutableChoice(execution)) throw new Error('The original engine and model are currently unavailable');
        };
        if (needsConversation(cid)) {
          requireOriginalModel();
          const created = await createConversation(
            prompt.replace(/\s+/g, ' ').slice(0, 60),
            agentId !== CEO_PROFILE_ID ? agentId : undefined
          );
          const sourceKey = key;
          const stillHere = selectedRef.current === cid && selectionRevision.current === selectionAtStart;
          const sourceRevision = draftRevisions.current.get(sourceKey);
          const sourceEdited = sourceRevision !== draftRevision;
          cid = created.id;
          boundProfile = created.profile_id;
          setConversations((prev) => (prev.some((chat) => chat.id === created.id) ? prev : [created, ...prev]));
          bindAgent(cid, agentId);
          if (sending.current.has(cid)) {
            // The destination has its own send. Offer an explicit Retry at the source, targeting the created ID.
            retainedSourceKey = sourceKey;
            recoverySource = sourceKey;
            if (persist(false)) {
              raise(submissionKey, `Another message is being sent in the created chat. Retry here when it finishes: ${prompt}`,
                recoveryAttempt());
            }
            return;
          }
          const nextLocks = new Map(sending.current);
          if (nextLocks.get(sourceKey) === lock) nextLocks.delete(sourceKey);
          key = cid;
          publishLocks(nextLocks.set(key, lock));
          // Preserve edits if a concurrent list refresh exposed the new chat before creation returned.
          const destinationEdited = draftRevisions.current.has(cid);
          if (!destinationEdited) {
            draftRevisions.current.set(cid, stillHere ? sourceRevision : draftRevision);
          } else {
            retainedSourceKey = sourceKey;
          }
          // Record the actual destination before moving the draft or sending the prompt.
          if (!persist(ownsDraft(cid), sourceKey)) return;
          // Only remove the source here if its text actually moves to the destination.
          if (!destinationEdited && (!sourceEdited || stillHere)) {
            setDraftMap((prev) => {
              if (!(sourceKey in prev)) return prev;
              const next = { ...prev };
              next[created.id] = prev[sourceKey];
              delete next[sourceKey];
              writeJson(KEY_DRAFTS, next);
              return next;
            });
          }
          if (stillHere) {
            selectedRef.current = cid;
            setSelectedId(cid);
            writeJson(KEY_SELECTED, cid);
          }
        }
        const chatId = cid;
        const clearDraft = (owner: string) => {
          if (!ownsDraft(owner)) return;
          setDraftMap((prev) => {
            if (!(owner in prev)) return prev;
            const next = { ...prev };
            delete next[owner];
            writeJson(KEY_DRAFTS, next);
            return next;
          });
        };
        if (agentId === CEO_PROFILE_ID) {
          // Loqman (the CEO) answers through the CEO loop, which is the path that can create agents.
          const ticket = requestId;
          if (attempt.requestId && attempt.cid === chatId) {
            const history = await listMessages(chatId);
            if (history.some((m) => m.role === 'assistant' && m.client_message_id === `ask:${ticket}:assistant`)) {
              forgetSubmission(ticket);
              clearDraft(chatId);
              if (retainedSourceKey) clearDraft(retainedSourceKey);
              return;
            }
            const progress = await ceoAskProgress(ticket).catch((e: unknown) => {
              if (e instanceof ApiError && e.status === 404) return null;
              throw e;
            });
            if ((progress && (!progress.done || progress.stage === 'done')) ||
                (!progress && history.some((m) => m.client_message_id === `ask:${ticket}:user`))) {
              fail('The worker has received this question. Check its reply before retrying');
              return;
            }
          }
          if (selectedRef.current === chatId) setPendingUser(prompt);
          const recoveryKey = CEO_SUBMISSION_PREFIX + chatId;
          const hideSubmittedDraft = ownsDraft(chatId);
          // Keep the existing new-CEO draft format compatible. The submission record owns recovery.
          if (createdCeoChat) {
            createdCeoSubmissions.current.set(chatId, attempt);
            // Creation may already have transferred a newer edit into the saved composer.
            setDraftMap((prev) => {
              if (prev[chatId]?.trim() === prompt && hideSubmittedDraft) return prev;
              const next = { ...prev, [recoveryKey]: prompt };
              writeJson(KEY_DRAFTS, next);
              return next;
            });
          }
          // Hide the submitted revision until the ask settles. Reload reads the persisted draft.
          updateRuns((prev) => ({ ...prev, [chatId]: { runId: ticket, status: 'running', error: null, hideSubmittedDraft } }));
          // Ask the worker where the answer has got to. One request at a time; it ends with the ask, a chat switch or unmount.
          let polling = true;
          void (async () => {
            while (polling) {
              await new Promise((r) => window.setTimeout(r, CEO_PROGRESS_MS));
              if (!polling || !mounted.current || selectedRef.current !== chatId) return;
              const p = await ceoAskProgress(ticket).catch(() => null);
              if (!polling || !mounted.current || selectedRef.current !== chatId) return;
              if (p) {
                const step = progressText(p);
                if (step) setCeoStep({ cid: chatId, text: step });
                if (p.done) return;
              }
            }
          })();
          let failure: string | null = null;
          try {
            // The worker stores both sides of the exchange in this chat (the question before generating, the
            // reply after), so a reload mid-generation keeps the message. A failed ask removes its question.
            requireOriginalModel();
            const answer = await askCeo(prompt, execution.engine, execution.model, ticket, chatId);
            forgetSubmission(requestId);
            clearDraft(chatId);
            if (retainedSourceKey) clearDraft(retainedSourceKey);
            if (answer.agents_created.length) onAgentsChanged?.();
          } catch (e) {
            failure = messageOf(e);
            fail(failure, e);
          } finally {
            polling = false;
            setCeoStep((prev) => (prev?.cid === chatId ? null : prev));
            if (selectedRef.current === chatId) setPendingUser(null);
            // A failed answer is a failed run, not a finished one.
            updateRuns((prev) => ({ ...prev, [chatId]: { runId: ticket, status: failure ? 'failed' : 'succeeded', error: failure } }));
            // The answer is settled. A slow history refresh must not swallow the next Retry.
            releaseLock();
            await refreshConversations().catch(() => undefined);
            await fetchMessages(chatId).catch(() => null);
          }
          return;
        }
        // Revalidate an unbound legacy recipient even when Retry already has a prepared payload.
        let outgoing = wirePrompt ?? prompt;
        if (!boundProfile) {
          requireOriginalModel();
          const agent = agents.find((a) => a.id === agentId);
          const bound = await bindConversationProfile(chatId, agentId).catch((e: unknown) => {
            if (e instanceof ApiError) {
              if (e.status === 409 || (e.status === 404 && e.detail === 'Conversation not found')) throw e;
              if (e.status === 404 && e.detail === 'Profile not found' && !missingLegacyProfiles.has(agentId)) {
                missingLegacyProfiles.add(agentId);
                writeJson(missingLegacyProfilesKey, [...missingLegacyProfiles]);
              }
            }
            if (!agent && !missingLegacyProfiles.has(agentId)) throw e;
            return null;
          });
          if (bound) {
            setConversations((prev) => prev.map((c) => (c.id === bound.id ? bound : c)));
            if (bound.profile_id !== agentId) throw new Error('The conversation is bound to a different agent.');
            if (missingLegacyProfiles.delete(agentId)) writeJson(missingLegacyProfilesKey, [...missingLegacyProfiles]);
          } else if (!missingLegacyProfiles.has(agentId) && wirePrompt === undefined && agent?.prompt &&
              (await listMessages(chatId).catch(() => [{}])).length === 0) {
            outgoing = `${CONTRACT_OPEN}
${agent.prompt}
${CONTRACT_CLOSE}

${prompt}`;
          }
          if (!bound && missingLegacyProfiles.has(agentId)) {
            fail(missingLegacyProfile, undefined, 'No new turn was submitted by this attempt');
            return;
          }
          // An earlier start may already be accepted. Preserve its exact payload for the same request key.
          if (wirePrompt === undefined) {
            wirePrompt = outgoing;
            if (!persist(ownsDraft(key))) return;
          }
        }
        requireOriginalModel();
        const before = currentRuns.current[chatId];
        if (selectedRef.current === chatId && (!before || isTerminal(before.status))) {
          setPendingUser(prompt);
          previewedSpecialistPrompt = true;
        }
        const snap = await startTurn({
          conversationId: chatId,
          engine: execution.engine,
          modelId: execution.model,
          prompt: outgoing,
          requestKey: requestId,
        });
        forgetSubmission(requestId);
        clearDraft(chatId);
        if (retainedSourceKey) clearDraft(retainedSourceKey);
        const current = currentRuns.current[chatId];
        if (current && (current.runId === snap.id ? isTerminal(current.status) : isTerminal(snap.status))) {
          // A delayed acknowledgement cannot replace a settled run or take ownership from a newer run.
          if (previewedSpecialistPrompt && selectedRef.current === chatId) setPendingUser(null);
          await loadThread(chatId).catch(() => null);
          return;
        }
        remember(chatId, snap.id);
        if (current && current.runId !== snap.id) {
          closeFollower(current.runId);
          clearStopping(current.runId);
          dropLive(chatId, current.runId);
        }
        if (isTerminal(snap.status)) {
          await finishRun(chatId, snap.id, snap.status, snap.error);
          return;
        }
        updateRuns((prev) => ({ ...prev, [chatId]: { runId: snap.id, status: snap.status, error: null } }));
        await fetchMessages(chatId).catch(() => null);
        follow(chatId, snap.id);
      } catch (e) {
        if (previewedSpecialistPrompt && selectedRef.current === cid) setPendingUser(null);
        fail(messageOf(e), e);
      } finally {
        releaseLock();
      }
    },
    [follow, agents, bindAgent, onAgentsChanged, refreshConversations, conversations, fetchMessages, loadThread, dropLive, updateRuns, raise, clearError, publishLocks, submissions, missingLegacyProfiles, forgetSubmission, clearStopping, closeFollower, finishRun]
  );

  const send = useCallback((text: string) => {
    const prompt = text.trim();
    if (!prompt) return Promise.resolve();
    const cid = selectedRef.current;
    const agentId = recipientIdFor(cid);
    if (agentId === null) return Promise.resolve();
    const revision = draftRevisions.current.get(chatOwnerKey(cid, agentId));
    for (const [errorKey, entry] of Object.entries(errors)) {
      const action = entry.retry;
      if (typeof action === 'object' && action?.requestId && action.cid === cid && action.agentId === agentId &&
          action.prompt === prompt && action.draftRevision === revision) return sendAttempt(action, errorKey);
    }
    const execution = currentExecutionChoice();
    if (!execution || execution.engine !== engine?.engine || execution.model !== engine.model) return Promise.resolve();
    return sendAttempt({
      cid, prompt, agentId, execution,
      draftRevision: revision,
    });
  }, [recipientIdFor, sendAttempt, errors, engine]);

  const openAgentChat = useCallback(
    async (agent: Agent) => {
      const existing = Object.entries(agentMapRef.current).find(
        ([cid, id]) => id === agent.id && conversations.some((c) => c.id === cid && (c.profile_id ?? id) === agent.id)
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
    const run = cid ? currentRuns.current[cid] : undefined;
    // A Loqman answer is one request to the worker that cannot be cancelled midway; the button is disabled for it.
    if (!cid || !run || run.runId.startsWith('ceo-') || isTerminal(run.status) || stoppedRuns.current.has(run.runId)) return;
    // The text stops changing the moment Stop is pressed, before the worker confirms.
    stoppedRuns.current.add(run.runId);
    setStoppingRuns((prev) => ({ ...prev, [run.runId]: true }));
    try {
      const result = await cancelRun(run.runId);
      if (result.id === run.runId && result.conversation_id === cid && isTerminal(result.status)) {
        await finishRun(cid, run.runId, result.status, result.error);
        return;
      }
    } catch (e) {
      const current = currentRuns.current[cid];
      if (current?.runId === run.runId && !isTerminal(current.status)) {
        raise(cid, `Could not stop the reply. ${messageOf(e)}`);
      }
    }
    stoppedRuns.current.delete(run.runId);
    clearStopping(run.runId);
  }, [clearStopping, finishRun, raise]);

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
      raise(selectedRef.current, messageOf(e));
    }
    const list = await listDrafts(runId).catch(() => []);
    setDrafts((prev) => ({ ...prev, [runId]: list }));
  }, [raise]);

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
      raise(selectedRef.current, messageOf(e));
    }
    const list = await listDrafts(draft.run_id).catch(() => []);
    setDrafts((prev) => ({ ...prev, [draft.run_id]: list }));
  }, [raise]);

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
      raise(selectedRef.current, messageOf(e));
    }
  }, [raise]);

  const messages = thread.cid !== null && thread.cid === selectedId ? thread.list : NO_MESSAGES;
  const runsBySeq = runsThread.cid !== null && runsThread.cid === selectedId ? runsThread.map : NO_RUNS;
  const selectedAgentId = recipientIdFor(selectedId);
  const submissionKey = Object.keys(errors).reverse().find((key) => {
    const action = errors[key].retry;
    return key.startsWith(SUBMISSION_PREFIX) && action !== null && typeof action === 'object' && action.agentId === selectedAgentId &&
      (action.cid === selectedId || action.deferredSource === selectedOwnerKey);
  });
  const errorKey = errors[selectedOwnerKey] ? selectedOwnerKey : submissionKey ?? '';
  const shownError = errors[errorKey];
  const threadError = selectedId ? (threadErrors[selectedId] ?? null) : null;

  const retry = useMemo(() => {
    if (!shownError?.retry) return null;
    const action = shownError.retry;
    if (typeof action === 'object' && !action.execution) return null;
    return () => {
      if (action !== 'load') {
        if (action.cid && !action.cid.startsWith('legacy:')) {
          const chat = conversations.find((c) => c.id === action.cid);
          if (!chat || (chat.profile_id && chat.profile_id !== action.agentId)) {
            raise(errorKey, 'The original conversation or its agent is no longer available.', action.requestId ? action : null);
            return;
          }
        }
        void sendAttempt(action, errorKey);
        return;
      }
      clearError(errorKey || null);
      void refreshConversations().catch((e) => raise(null, `Could not load your chats. ${messageOf(e)}`, 'load'));
    };
  }, [shownError, conversations, sendAttempt, clearError, errorKey, refreshConversations, raise]);

  const retryThread = useCallback(() => {
    const cid = selectedRef.current;
    if (!cid || cid.startsWith('legacy:')) return;
    loadThread(cid).catch((e) => setThreadErrors((prev) => ({ ...prev, [cid]: messageOf(e) })));
  }, [loadThread]);

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
    error: shownError?.message ?? (currentRun?.connectionWarning ? runConnectionWarning : null),
    dismissError: () => {
      if (!shownError && selectedId && currentRun?.connectionWarning) {
        updateRuns((prev) => {
          const run = prev[selectedId];
          return run?.runId === currentRun.runId ? { ...prev, [selectedId]: { ...run, connectionWarning: false } } : prev;
        });
        return;
      }
      if (errorKey.startsWith(SUBMISSION_PREFIX)) forgetSubmission(errorKey.slice(SUBMISSION_PREFIX.length));
      else if (shownError?.retry && typeof shownError.retry === 'object' && shownError.retry.createdCeoChat && !shownError.retry.requestId) {
        setDraftMap((prev) => {
          const next = { ...prev };
          delete next[CEO_SUBMISSION_PREFIX + errorKey];
          writeJson(KEY_DRAFTS, next);
          return next;
        });
      }
      clearError(errorKey || null);
    },
    retry,
    threadError,
    retryThread,
    stopping: Boolean(currentRun && stoppingRuns[currentRun.runId]),
    sending: sendLocks.has(selectedOwnerKey),
    canStop: Boolean(currentRun && !currentRun.runId.startsWith('ceo-') && !isTerminal(currentRun.status)),
    draft: currentRun?.hideSubmittedDraft ? '' : (draftMap[selectedOwnerKey] ?? ''),
    setDraft,
    send,
    stop,
    newChat: () => {
      const current = recipientIdFor(selectedRef.current);
      select(null);
      setDraftAgentId(current);
    },
    decide,
    makeDeliverable,
    reviewDraft,
    downloadDraft,
    loaded,
    activeRunIds,
    selectedAgentId,
    agentFor,
    openAgentChat,
    reloadConversations: refreshConversations,
    loadAllDrafts,
  };
}
