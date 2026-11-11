import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type {
  CeoAction,
  CeoActionStatus,
  CeoCreatedProfile,
  CeoProposedAction,
  CeoThread,
  CeoTurn,
  ModelCatalogEntry,
} from '../../types';
import {
  askCeo,
  approveCeoAction,
  denyCeoAction,
  getCeoAskProgress,
  listCeoActions,
} from '../../api/client';
import {
  AlertTriangle,
  ArrowUp,
  BarChart3,
  Bot,
  Check,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  ClipboardList,
  Clock3,
  FileText,
  Inbox,
  Loader2,
  Plus,
  RefreshCw,
  Search,
  ShieldCheck,
  Sparkles,
  UserRoundPlus,
  Users,
  X,
  XCircle,
} from 'lucide-react';

interface Props {
  /** Live free catalog (GET /api/models/free) for the inline model picker. */
  models: ModelCatalogEntry[];
  /** Default model chosen by the user (Settings) — sent with every question. */
  selectedModelId: string;
  /** Reasoning variant for that model, when supported. */
  selectedVariant: string;
  /** Persist a new model/variant choice into the shared app settings. */
  onModelChange?: (modelId: string, variant: string) => void;
  /** Called after an approval that really created employee profiles. */
  onProfilesCreated?: (profiles: CeoCreatedProfile[], teamName: string | null) => void;
  /** Local ask history (newest first) rendered in the sidebar. */
  threads: CeoThread[];
  activeThreadId: string | null;
  onSelectThread: (id: string) => void;
  /** Back to the welcome state — a fresh question. */
  onNewThread: () => void;
  setThreads: React.Dispatch<React.SetStateAction<CeoThread[]>>;
  onToast: (message: string) => void;
  /** The full approvals inbox dialog is controlled by the app shell. */
  approvalsOpen: boolean;
  onApprovalsOpenChange: (open: boolean) => void;
}

const STATUSES: CeoActionStatus[] = ['pending', 'approved', 'denied', 'failed'];

const TAB_LABELS: Record<CeoActionStatus, string> = {
  pending: 'معلّقة',
  approved: 'معتمدة',
  denied: 'مرفوضة',
  failed: 'فاشلة',
};

const STATUS_TAGS: Record<CeoActionStatus, { label: string; cls: string }> = {
  pending: { label: 'معلّق', cls: 'os-tag-amber' },
  approved: { label: 'معتمد', cls: 'os-tag-green' },
  denied: { label: 'مرفوض', cls: 'os-tag-red' },
  failed: { label: 'فشل', cls: 'os-tag-red' },
};

type KindMeta = { label: string; Icon: React.ComponentType<{ size?: number; className?: string }> };

const KIND_META: Record<string, KindMeta> = {
  update_contract: { label: 'تعديل عقد', Icon: FileText },
  new_profile: { label: 'موظف جديد', Icon: UserRoundPlus },
  new_team: { label: 'فريق جديد', Icon: Users },
};

const kindMeta = (kind: string): KindMeta => KIND_META[kind] ?? { label: kind, Icon: FileText };

const SUGGESTIONS: { Icon: React.ComponentType<{ size?: number }>; title: string; text: string; prompt: string }[] = [
  {
    Icon: ShieldCheck,
    title: 'فريق مراجعة أمنية',
    text: 'اطلب فريق جديد — واعتمده بنفسك',
    prompt: 'اعمل فريق مراجعة أمنية',
  },
  {
    Icon: BarChart3,
    title: 'ملخص المهام',
    text: 'كام مهمة خلصت وإيه اللي ناقص؟',
    prompt: 'كام مهمة خلصت وإيه اللي ناقص؟',
  },
  {
    Icon: Users,
    title: 'فِرَق الشركة',
    text: 'مين الفرق والموظفين الموجودين دلوقتي؟',
    prompt: 'إيه الفرق والموظفين الموجودين دلوقتي؟',
  },
  {
    Icon: ClipboardList,
    title: 'محتاجة قرارك',
    text: 'اقتراحات معلّقة بانتظار الاعتماد',
    prompt: 'إيه اللي محتاج قرار مني دلوقتي؟',
  },
];

const fetchAllActions = () => Promise.all(STATUSES.map((status) => listCeoActions(status)));

const toStatusMap = (results: CeoAction[][]): Record<CeoActionStatus, CeoAction[]> => {
  const next = { pending: [], approved: [], denied: [], failed: [] } as Record<
    CeoActionStatus,
    CeoAction[]
  >;
  STATUSES.forEach((status, index) => {
    next[status] = results[index];
  });
  return next;
};

const uid = () =>
  typeof crypto !== 'undefined' && 'randomUUID' in crypto
    ? crypto.randomUUID()
    : `ask-${Date.now()}-${Math.random().toString(16).slice(2)}`;

const fmtClock = (ts: number) =>
  new Date(ts).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });

const fmtDateTime = (iso: string) => {
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? iso
    : d.toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
};

/** Maps backend error details to clear Arabic feedback (request() throws the detail). */
function humanizeError(error: unknown): string {
  const raw = error instanceof Error ? error.message : String(error);
  if (/failed to fetch|networkerror|load failed/i.test(raw)) {
    return 'تعذّر الاتصال بالخادم المحلي — تأكد من تشغيل محرك OpenCode ثم أعد المحاولة.';
  }
  if (/x-agentos-confirm|confirmation/i.test(raw)) {
    return 'رُفض القرار من الخادم: بوابة التأكيد المحلية مطلوبة (X-AgentOS-Confirm).';
  }
  if (/already decided|status=/i.test(raw)) {
    return 'هذا الإجراء محسوم مسبقاً — حدّثنا القائمة لتصلك حالته الأخيرة.';
  }
  if (/runtime unreachable|ceo ask failed/i.test(raw)) {
    return 'تعذّر وصول لقمان إلى محرك OpenCode — حاول مرة أخرى بعد قليل.';
  }
  return raw;
}

/** Maps backend ask-progress stages to short Arabic status text. */
function progressLabel(stage: string, detail: string): string {
  switch (stage) {
    case 'starting':
      return 'بيهيّئ جلسة المحادثة…';
    case 'thinking':
      return 'لقمان بيفكر…';
    case 'tool':
      return `بيجمع بيانات: ${detail || 'أداة داخلية'}`;
    case 'final':
      return 'بيكتب الرد النهائي…';
    case 'done':
      return 'الرد جاهز';
    case 'error':
      return 'حصل خطأ — بنقفل المحاولة…';
    default:
      return 'لقمان بيراجع بيانات الشركة…';
  }
}

export const CeoChatView: React.FC<Props> = ({
  models,
  selectedModelId,
  selectedVariant,
  onModelChange,
  onProfilesCreated,
  threads,
  activeThreadId,
  onSelectThread,
  onNewThread,
  setThreads,
  onToast,
  approvalsOpen,
  onApprovalsOpenChange,
}) => {
  const activeThread = threads.find((t) => t.id === activeThreadId) ?? null;

  // ---------------------------------------------------------------------------
  // Ask surface (question + live progress while the sync POST is in flight)
  // ---------------------------------------------------------------------------
  const [question, setQuestion] = useState('');
  const [sending, setSending] = useState(false);
  const [sendingThreadId, setSendingThreadId] = useState<string | null>(null);
  const [progressStage, setProgressStage] = useState('');
  const [progressDetail, setProgressDetail] = useState('');
  const [elapsedSec, setElapsedSec] = useState(0);
  const tickRef = useRef<number | null>(null);
  const pollRef = useRef<number | null>(null);
  const composerRef = useRef<HTMLTextAreaElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  const stopAskWatchers = useCallback(() => {
    if (tickRef.current !== null) {
      window.clearInterval(tickRef.current);
      tickRef.current = null;
    }
    if (pollRef.current !== null) {
      window.clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  useEffect(() => stopAskWatchers, [stopAskWatchers]);

  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
    // turns length: follow-up questions scroll the conversation into view.
  }, [activeThread?.reply, activeThread?.error, activeThread?.turns.length, activeThreadId, sending]);

  // ---------------------------------------------------------------------------
  // Model / effort menus (fed by the live free catalog)
  // ---------------------------------------------------------------------------
  const [modelMenuOpen, setModelMenuOpen] = useState(false);
  const [effortMenuOpen, setEffortMenuOpen] = useState(false);
  const [modelQuery, setModelQuery] = useState('');

  const currentModel = models.find((m) => m.id === selectedModelId);
  const variantOptions = currentModel?.variants || [];

  const filteredModels = useMemo(() => {
    const q = modelQuery.trim().toLowerCase();
    if (!q) return models;
    return models.filter(
      (m) =>
        m.name.toLowerCase().includes(q) ||
        m.id.toLowerCase().includes(q) ||
        m.provider_id.toLowerCase().includes(q)
    );
  }, [models, modelQuery]);

  const handleModelSelect = (nextId: string) => {
    const picked = models.find((m) => m.id === nextId);
    const nextVariant = picked?.variants.includes(selectedVariant)
      ? selectedVariant
      : picked?.variants[0] || selectedVariant;
    onModelChange?.(nextId, nextVariant);
  };

  // ---------------------------------------------------------------------------
  // Approvals — decided right in the chat (inline proposal cards); the full
  // inbox (all statuses, older sessions) lives in a dialog controlled by the
  // app shell and opened from the topbar bell or the pending strip.
  // ---------------------------------------------------------------------------
  const [tab, setTab] = useState<CeoActionStatus>('pending');
  const [byStatus, setByStatus] = useState<Record<CeoActionStatus, CeoAction[]>>({
    pending: [],
    approved: [],
    denied: [],
    failed: [],
  });
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState('');
  const [confirming, setConfirming] = useState<{ id: string; mode: 'approve' | 'deny' } | null>(null);
  const [decidingId, setDecidingId] = useState<string | null>(null);
  const [decisionError, setDecisionError] = useState('');
  const [lastDecision, setLastDecision] = useState<CeoAction | null>(null);
  // Proposals recorded in this session carry a local creation time (the API
  // exposes decided_at but not created_at).
  const [localCreatedAt, setLocalCreatedAt] = useState<Record<string, number>>({});

  /** Re-syncs every status tab without flashing the loading skeleton. */
  const reloadQuiet = useCallback(async () => {
    try {
      const results = await fetchAllActions();
      setByStatus(toStatusMap(results));
      setLoadError('');
    } catch (error) {
      setLoadError(humanizeError(error));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    let ignore = false;
    void fetchAllActions().then(
      (results) => {
        if (ignore) return;
        setByStatus(toStatusMap(results));
        setLoadError('');
        setLoading(false);
      },
      (error) => {
        if (ignore) return;
        setLoadError(humanizeError(error));
        setLoading(false);
      }
    );
    return () => {
      ignore = true;
    };
  }, []);

  const refresh = () => {
    setLoading(true);
    void reloadQuiet();
  };

  useEffect(() => {
    if (!approvalsOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onApprovalsOpenChange(false);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [approvalsOpen, onApprovalsOpenChange]);

  // ---------------------------------------------------------------------------
  // Ask the CEO
  // ---------------------------------------------------------------------------
  const handleAsk = async () => {
    const trimmed = question.trim();
    if (!trimmed || sending) return;

    const startedAt = Date.now();
    const turn: CeoTurn = { question: trimmed, reply: '', proposed: [], ts: startedAt };
    // One ongoing conversation: asking with a thread open EXTENDS it; only the
    // fresh welcome state starts a new one. The backend keeps the real
    // conversation memory, so continuity survives restarts regardless.
    const threadId = activeThreadId ?? uid();
    if (threads.some((t) => t.id === threadId)) {
      setThreads((prev) =>
        prev.map((t) =>
          t.id === threadId ? { ...t, reply: '', error: undefined, turns: [...t.turns, turn] } : t
        )
      );
    } else {
      const thread: CeoThread = {
        id: threadId,
        question: trimmed,
        reply: '',
        proposed: [],
        ts: startedAt,
        turns: [turn],
      };
      setThreads((prev) => [thread, ...prev]);
    }
    onSelectThread(threadId);

    setQuestion('');
    setSending(true);
    setSendingThreadId(threadId);
    setProgressStage('starting');
    setProgressDetail('');
    setElapsedSec(0);

    // Client-generated id: the backend records live stages for it while the
    // sync POST runs, so the UI can show what the CEO is doing right now.
    const requestId = uid();
    tickRef.current = window.setInterval(() => {
      setElapsedSec(Math.floor((Date.now() - startedAt) / 1000));
    }, 1000);
    pollRef.current = window.setInterval(() => {
      void getCeoAskProgress(requestId)
        .then((progress) => {
          setProgressStage(progress.stage);
          setProgressDetail(progress.detail || '');
        })
        .catch(() => undefined); // 404 until the backend registers the id
    }, 1200);

    try {
      const response = await askCeo(
        trimmed,
        selectedModelId || undefined,
        selectedVariant || undefined,
        requestId
      );
      setThreads((prev) =>
        prev.map((t) => {
          if (t.id !== threadId) return t;
          const turns = t.turns.map((tr, i) =>
            i === t.turns.length - 1
              ? { ...tr, reply: response.reply, proposed: response.actions_proposed, error: undefined }
              : tr
          );
          return {
            ...t,
            turns,
            reply: response.reply,
            proposed: response.actions_proposed,
            error: undefined,
          };
        })
      );
      if (response.actions_proposed.length > 0) {
        const now = Date.now();
        setLocalCreatedAt((prev) => {
          const next = { ...prev };
          response.actions_proposed.forEach((action) => {
            next[action.id] = now;
          });
          return next;
        });
        void reloadQuiet(); // Surface the fresh proposals under the reply immediately.
      }
    } catch (error) {
      const message = humanizeError(error);
      setThreads((prev) =>
        prev.map((t) => {
          if (t.id !== threadId) return t;
          const turns = t.turns.map((tr, i) =>
            i === t.turns.length - 1 ? { ...tr, error: message } : tr
          );
          return { ...t, turns, error: message };
        })
      );
    } finally {
      stopAskWatchers();
      setProgressStage('');
      setProgressDetail('');
      setSending(false);
      setSendingThreadId(null);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      void handleAsk();
    }
  };

  const decide = async (actionId: string, mode: 'approve' | 'deny') => {
    setDecidingId(actionId);
    setDecisionError('');
    try {
      const updated =
        mode === 'approve' ? await approveCeoAction(actionId) : await denyCeoAction(actionId);
      setLastDecision(updated);
      if (mode === 'approve' && updated.created_profiles && updated.created_profiles.length > 0) {
        onProfilesCreated?.(updated.created_profiles, updated.team_name ?? null);
      }
      await reloadQuiet();
    } catch (error) {
      setDecisionError(humanizeError(error));
      // Re-sync after a conflict so the stale card leaves the pending list.
      if (/already decided|status=/i.test(error instanceof Error ? error.message : '')) {
        await reloadQuiet();
      }
    } finally {
      setConfirming(null);
      setDecidingId(null);
    }
  };

  const copyReply = (text: string) => {
    void navigator.clipboard
      ?.writeText(text)
      .then(() => onToast('تم نسخ الرد'))
      .catch(() => onToast('تعذّر النسخ — انسخ يدوياً'));
  };

  // Live records for every proposed action, used to mirror real statuses on
  // the inline proposal cards inside the conversation.
  const liveActions = useMemo(
    () => [...byStatus.pending, ...byStatus.approved, ...byStatus.denied, ...byStatus.failed],
    [byStatus]
  );

  const activeItems = byStatus[tab];
  const showProgressHere = sending && activeThread?.id === sendingThreadId;
  const shortTitle = activeThread
    ? activeThread.question.length > 64
      ? `${activeThread.question.slice(0, 64).trim()}…`
      : activeThread.question
    : '';

  return (
    <div className="chat-layout">
      {/* ------------------------------------------------------------------ */}
      {/* Conversation column                                                  */}
      {/* ------------------------------------------------------------------ */}
      <div className="chat-column">
        {activeThread ? (
          <div className="conversation-heading">
            <div style={{ minWidth: 0 }}>
              <span className="eyebrow">غرفة القيادة · CEO</span>
              <h1 dir="auto" title={activeThread.question}>
                {shortTitle}
              </h1>
            </div>
            <button
              className="icon-button"
              title="سؤال جديد"
              aria-label="سؤال جديد"
              onClick={() => {
                onNewThread();
                window.setTimeout(() => composerRef.current?.focus(), 40);
              }}
            >
              <Plus size={16} />
            </button>
          </div>
        ) : null}

        <div ref={scrollRef} className={`message-scroll ${activeThread ? '' : 'empty-scroll'}`}>
          {!activeThread ? (
            <div className="welcome-wrap">
              <div className="welcome-emblem">
                <div className="emblem-orbit orbit-one" />
                <div className="emblem-orbit orbit-two" />
                <div className="emblem-core">
                  <Sparkles size={23} strokeWidth={1.55} />
                </div>
              </div>
              <div className="welcome-kicker">
                <span className="kicker-line" /> غرفة القيادة التنفيذية <span className="kicker-line" />
              </div>
              <h1>
                هنعمل إيه <span>النهارده</span>؟
              </h1>
              <p className="welcome-subtitle">
                لقمان شايف بيانات شركتك الحية — الفِرق، المهام والعقود.
                <br />
                اسأل، وهو يحلّل ويقترح. مفيش حاجة بتتنفذ غير باعتمادك.
              </p>
              <div className="suggestion-grid" dir="rtl">
                {SUGGESTIONS.map(({ Icon, title, text, prompt }) => (
                  <button
                    className="suggestion-card"
                    key={title}
                    onClick={() => {
                      setQuestion(prompt);
                      window.setTimeout(() => composerRef.current?.focus(), 40);
                    }}
                  >
                    <span className="suggestion-icon">
                      <Icon size={17} />
                    </span>
                    <span className="suggestion-copy">
                      <strong>{title}</strong>
                      <small>{text}</small>
                    </span>
                    <ArrowUp size={14} className="suggestion-arrow" />
                  </button>
                ))}
              </div>
            </div>
          ) : (
            <div className="messages-inner">
              {activeThread.turns.map((turn, turnIdx) => {
                const isLastTurn = turnIdx === activeThread.turns.length - 1;
                // Only the LATEST reply carries the accessible region name, so
                // the landmark stays unique when a thread collects follow-ups.
                const isLatestReply = isLastTurn && !!turn.reply && !turn.error;
                return (
                  <React.Fragment key={`${turnIdx}-${turn.ts}`}>
                {/* User question */}
                <article className="message-row user">
                  <div className="message-user-avatar">YB</div>
                  <div className="message-content-wrap">
                    <div className="message-meta">
                      <strong>أنت</strong>
                      <time>{fmtClock(turn.ts)}</time>
                    </div>
                    <div className="message-body" dir="auto">
                      {turn.question}
                    </div>
                  </div>
                </article>

                {/* Live progress while this ask is in flight */}
                {isLastTurn && showProgressHere && !turn.reply && !turn.error ? (
                  <div className="progress-row" role="status">
                    <Loader2 size={14} className="animate-spin" />
                    <span>{progressLabel(progressStage, progressDetail)}</span>
                    <span className="mono secs">· {elapsedSec}s</span>
                  </div>
                ) : null}

                {/* Failure card */}
                {turn.error ? (
                  <article className="message-row assistant">
                    <div className="assistant-avatar">
                      <span className="brand-mark">
                        <span />
                        <span />
                        <span />
                      </span>
                    </div>
                    <div className="message-content-wrap">
                      <div className="message-meta">
                        <strong>لقمان</strong>
                        <time>{fmtClock(turn.ts)}</time>
                      </div>
                      <div className="ask-error" role="alert">
                        {turn.error}
                      </div>
                    </div>
                  </article>
                ) : null}

                {/* The reply */}
                {turn.reply ? (
                  <article className="message-row assistant">
                    <div className="assistant-avatar">
                      <span className="brand-mark">
                        <span />
                        <span />
                        <span />
                      </span>
                    </div>
                    <div className="message-content-wrap">
                      <div className="message-meta">
                        <strong>لقمان</strong>
                        <time>{fmtClock(turn.ts)}</time>
                      </div>
                      <div
                        className="message-body"
                        role={isLatestReply ? 'region' : undefined}
                        aria-label={isLatestReply ? 'رد لقمان' : undefined}
                        dir="auto"
                      >
                        {turn.reply}
                      </div>
                      <div className="message-tools">
                        <button
                          title="نسخ الرد"
                          aria-label="نسخ الرد"
                          onClick={() => copyReply(turn.reply)}
                        >
                          <FileText size={14} />
                        </button>
                      </div>

                      {turn.proposed.length > 0 ? (
                        <div className="thread-proposals">
                          <div className="proposals-note">
                            <Clock3 size={12} />
                            مقترحات من لقمان — القرار من هنا، مفيش حاجة بتتنفذ غير باعتمادك
                          </div>
                          {turn.proposed.map((action: CeoProposedAction) => {
                            const meta = kindMeta(action.kind);
                            const live = liveActions.find((a) => a.id === action.id);
                            const status: CeoActionStatus = live?.status ?? 'pending';
                            const tag = STATUS_TAGS[status];
                            const isConfirming = confirming?.id === action.id;
                            return (
                              <div key={action.id} className={`proposal-card ${status}`}>
                                <div className="proposal-card-head">
                                  <span className="os-tag">
                                    <meta.Icon size={11} />
                                    {meta.label}
                                  </span>
                                  <span className={`os-tag ${tag.cls}`}>{tag.label}</span>
                                  {status === 'pending' ? (
                                    <span className="waiting">بانتظار اعتمادك</span>
                                  ) : null}
                                </div>

                                <p className="proposal-card-summary" dir="auto" title={action.summary}>
                                  {action.summary}
                                </p>

                                {status === 'pending' && !isConfirming ? (
                                  <div className="proposal-card-actions">
                                    <button
                                      onClick={() => setConfirming({ id: action.id, mode: 'approve' })}
                                      className="os-btn os-btn-sm"
                                    >
                                      <Check size={13} />
                                      اعتماد
                                    </button>
                                    <button
                                      onClick={() => setConfirming({ id: action.id, mode: 'deny' })}
                                      className="os-btn os-btn-ghost os-btn-sm text-[var(--red)]"
                                    >
                                      <X size={13} />
                                      رفض
                                    </button>
                                  </div>
                                ) : null}

                                {isConfirming ? (
                                  <div
                                    role="group"
                                    aria-label="تأكيد القرار"
                                    className={`rail-gate ${confirming?.mode === 'approve' ? 'ok' : 'err'}`}
                                  >
                                    <p>
                                      {confirming?.mode === 'approve'
                                        ? 'تأكيد اعتماد هذا الإجراء؟ سيُنفَّذ فوراً.'
                                        : 'تأكيد رفض هذا الإجراء؟ لن يُنفَّذ شيء.'}
                                    </p>
                                    <div className="btns">
                                      <button
                                        onClick={() => void decide(action.id, confirming?.mode ?? 'approve')}
                                        disabled={decidingId === action.id}
                                        className={
                                          confirming?.mode === 'approve'
                                            ? 'os-btn os-btn-sm'
                                            : 'os-btn os-btn-danger os-btn-sm'
                                        }
                                      >
                                        {decidingId === action.id ? (
                                          <Loader2 size={13} className="animate-spin" />
                                        ) : confirming?.mode === 'approve' ? (
                                          <Check size={13} />
                                        ) : (
                                          <X size={13} />
                                        )}
                                        {confirming?.mode === 'approve' ? 'تأكيد الاعتماد' : 'تأكيد الرفض'}
                                      </button>
                                      <button
                                        onClick={() => setConfirming(null)}
                                        disabled={decidingId === action.id}
                                        className="os-btn os-btn-ghost os-btn-sm"
                                      >
                                        إلغاء
                                      </button>
                                    </div>
                                  </div>
                                ) : null}
                              </div>
                            );
                          })}

                          {lastDecision && turn.proposed.some((p) => p.id === lastDecision.id) ? (
                            <div
                              className={`decision-line ${
                                lastDecision.status === 'approved' ? 'ok' : 'err'
                              }`}
                              role={lastDecision.status === 'failed' ? 'alert' : 'status'}
                            >
                              {lastDecision.status === 'approved' ? (
                                <CheckCircle2 size={13} />
                              ) : lastDecision.status === 'denied' ? (
                                <XCircle size={13} />
                              ) : (
                                <AlertTriangle size={13} />
                              )}
                              <span>
                                {lastDecision.status === 'approved'
                                  ? 'تم اعتماد الإجراء وتنفيذه.'
                                  : lastDecision.status === 'denied'
                                    ? 'تم رفض الإجراء — لم يُنفَّذ شيء.'
                                    : 'فشل التنفيذ بعد الاعتماد.'}
                              </span>
                            </div>
                          ) : null}
                        </div>
                      ) : null}
                    </div>
                  </article>
                ) : null}
                  </React.Fragment>
                );
              })}
            </div>
          )}
        </div>

        {/* Composer */}
        <div className="composer-zone">
          {byStatus.pending.length > 0 ? (
            <button className="approvals-strip" onClick={() => onApprovalsOpenChange(true)}>
              <Clock3 size={13} />
              <span>
                {byStatus.pending.length > 1
                  ? `${byStatus.pending.length} إجراءات بانتظار قرارك`
                  : 'إجراء بانتظار قرارك'}
              </span>
              <span className="go">
                اعرض الكل
                <ChevronRight size={12} />
              </span>
            </button>
          ) : null}
          <div className="composer-shell">
            <textarea
              ref={composerRef}
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="اكتب سؤالاً عن الشركة… مثال: كام مهمة خلصت؟"
              aria-label="سؤال للمدير العام"
              rows={1}
            />
            <div className="composer-footer">
              <div className="composer-controls">
                <div className="model-picker-wrap">
                  <button
                    className={`model-picker ${modelMenuOpen ? 'is-open' : ''}`}
                    aria-label="موديل لقمان"
                    aria-expanded={modelMenuOpen}
                    title={currentModel?.name || selectedModelId || 'النموذج الافتراضي'}
                    onClick={() => {
                      setModelMenuOpen((v) => !v);
                      setEffortMenuOpen(false);
                      setModelQuery('');
                    }}
                  >
                    <span className="model-spark">
                      <Sparkles size={13} />
                    </span>
                    <span className="model-picker-label">
                      {currentModel?.name || selectedModelId || 'النموذج الافتراضي'}
                    </span>
                    <ChevronDown size={13} />
                  </button>
                  {modelMenuOpen ? (
                    <div className="model-menu">
                      <div className="model-menu-label">
                        SELECT MODEL · {models.length} FREE
                      </div>
                      <label className="model-search">
                        <Search size={12} />
                        <input
                          value={modelQuery}
                          onChange={(e) => setModelQuery(e.target.value)}
                          placeholder="ابحث في الموديلات…"
                          aria-label="بحث في الموديلات"
                        />
                      </label>
                      <div className="model-menu-list">
                        {filteredModels.map((m) => {
                          const isSelected = m.id === selectedModelId;
                          return (
                            <button
                              key={m.id}
                              role="menuitemradio"
                              aria-checked={isSelected}
                              onClick={() => {
                                handleModelSelect(m.id);
                                setModelMenuOpen(false);
                              }}
                            >
                              <span className="model-option-icon">
                                <Bot size={14} />
                              </span>
                              <span className="model-option-copy">
                                <strong>{m.name}</strong>
                                <small>
                                  {m.provider_id}
                                  {m.variants.length > 0 ? ` · ${m.variants.join(' / ')}` : ''}
                                </small>
                              </span>
                              {isSelected ? <Check size={14} className="model-check" /> : null}
                            </button>
                          );
                        })}
                        {filteredModels.length === 0 ? (
                          <div className="no-search-results">لا موديلات مطابقة</div>
                        ) : null}
                      </div>
                    </div>
                  ) : null}
                </div>

                {variantOptions.length > 0 ? (
                  <div className="model-picker-wrap">
                    <button
                      className={`model-picker ${effortMenuOpen ? 'is-open' : ''}`}
                      aria-label="مستوى التفكير"
                      aria-expanded={effortMenuOpen}
                      onClick={() => {
                        setEffortMenuOpen((v) => !v);
                        setModelMenuOpen(false);
                      }}
                    >
                      <span className="model-picker-label">
                        تفكير: <b className="effort-value">{selectedVariant}</b>
                      </span>
                      <ChevronDown size={13} />
                    </button>
                    {effortMenuOpen ? (
                      <div className="model-menu effort-menu">
                        <div className="model-menu-label">EFFORT</div>
                        <div className="model-menu-list">
                          {variantOptions.map((v) => {
                            const isSelected = v === selectedVariant;
                            return (
                              <button
                                key={v}
                                role="menuitemradio"
                                aria-checked={isSelected}
                                onClick={() => {
                                  onModelChange?.(selectedModelId, v);
                                  setEffortMenuOpen(false);
                                }}
                              >
                                <span className="effort-value">{v}</span>
                                {isSelected ? <Check size={14} className="model-check" /> : null}
                              </button>
                            );
                          })}
                        </div>
                      </div>
                    ) : null}
                  </div>
                ) : null}
              </div>

              <div className="composer-right">
                <span className="enter-hint">Enter للإرسال · Shift+Enter لسطر جديد</span>
                <button
                  className={`send-button ${question.trim() && !sending ? 'ready' : ''}`}
                  onClick={() => void handleAsk()}
                  disabled={!question.trim() || sending}
                  aria-label="إرسال"
                  title="إرسال"
                >
                  {sending ? <Loader2 size={16} className="animate-spin" /> : <ArrowUp size={17} />}
                </button>
              </div>
            </div>
          </div>
          <div className="composer-disclaimer">
            لقمان ممكن يغلط — راجع أي تغيير مهم قبل ما تعتمده.
          </div>
        </div>
      </div>

      {/* ------------------------------------------------------------------ */}
      {/* Approvals inbox — dialog opened from the topbar bell / pending strip */}
      {/* ------------------------------------------------------------------ */}
      {approvalsOpen ? (
        <div
          className="approvals-overlay"
          onClick={(e) => {
            if (e.target === e.currentTarget) onApprovalsOpenChange(false);
          }}
        >
          <div className="approvals-modal" role="dialog" aria-modal="true" aria-label="صندوق الاعتماد">
            <div className="rail-header">
              <h2 className="rail-title">صندوق الاعتماد</h2>
              <div className="rail-header-actions">
                <button
                  className="icon-button"
                  onClick={refresh}
                  disabled={loading}
                  aria-label="تحديث المقترحات"
                  title="تحديث المقترحات"
                >
                  <RefreshCw size={15} className={loading ? 'animate-spin' : ''} />
                </button>
                <button
                  className="icon-button"
                  onClick={() => onApprovalsOpenChange(false)}
                  aria-label="إغلاق"
                  title="إغلاق"
                >
                  <X size={15} />
                </button>
              </div>
            </div>
            <p className="rail-note">قراءة بلا حدود · الكتابة بتأكيدك أنت.</p>

            {byStatus.pending.length > 0 ? (
              <span className="rail-pending-pill">
                {byStatus.pending.length > 1
                  ? `${byStatus.pending.length} إجراءات بانتظار قرارك`
                  : 'إجراء بانتظار قرارك'}
              </span>
            ) : null}

            {lastDecision ? (
              <div
                className={`rail-banner ${lastDecision.status === 'approved' ? 'ok' : 'err'}`}
                role={lastDecision.status === 'failed' ? 'alert' : 'status'}
              >
                {lastDecision.status === 'approved' ? (
                  <CheckCircle2 size={15} className="text-[var(--green)] flex-none mt-0.5" />
                ) : lastDecision.status === 'denied' ? (
                  <XCircle size={15} className="text-[var(--red)] flex-none mt-0.5" />
                ) : (
                  <AlertTriangle size={15} className="text-[var(--red)] flex-none mt-0.5" />
                )}
                <div className="copy">
                  <b>
                    {lastDecision.status === 'approved'
                      ? 'تم اعتماد الإجراء وتنفيذه.'
                      : lastDecision.status === 'denied'
                        ? 'تم رفض الإجراء — لم يُنفَّذ شيء.'
                        : 'فشل التنفيذ بعد الاعتماد.'}
                  </b>
                  <span className="mono" dir="auto">
                    {lastDecision.summary}
                  </span>
                  {lastDecision.status === 'failed' && lastDecision.decision_note ? (
                    <span dir="auto">{lastDecision.decision_note}</span>
                  ) : null}
                </div>
                <button className="icon-button" aria-label="إغلاق التنبيه" onClick={() => setLastDecision(null)}>
                  <X size={13} />
                </button>
              </div>
            ) : null}

            {decisionError ? (
              <div className="rail-banner err" role="alert">
                <AlertTriangle size={15} className="text-[var(--red)] flex-none mt-0.5" />
                <div className="copy">
                  <span>{decisionError}</span>
                </div>
              </div>
            ) : null}

            {loadError ? (
              <div className="rail-banner err" role="alert">
                <AlertTriangle size={15} className="text-[var(--red)] flex-none mt-0.5" />
                <div className="copy">
                  <span>{loadError}</span>
                  <div>
                    <button onClick={refresh} className="os-btn os-btn-ghost os-btn-sm" style={{ marginTop: 6 }}>
                      <RefreshCw size={12} />
                      إعادة المحاولة
                    </button>
                  </div>
                </div>
              </div>
            ) : null}

            <div className="rail-tabs">
              {STATUSES.map((status) => (
                <button
                  key={status}
                  onClick={() => setTab(status)}
                  aria-label={TAB_LABELS[status]}
                  className={`rail-tab ${tab === status ? 'active' : ''}`}
                >
                  <span>{TAB_LABELS[status]}</span>
                  <span className="rail-tab-count" aria-hidden="true">
                    {byStatus[status].length}
                  </span>
                </button>
              ))}
            </div>

            <div className="rail-list">
              {loading && activeItems.length === 0 ? (
                <div className="rail-loading">
                  <Loader2 size={14} className="animate-spin" />
                  جارٍ تحميل المقترحات…
                </div>
              ) : activeItems.length === 0 ? (
                <div className="rail-empty">
                  {tab === 'pending' ? (
                    <>
                      <ShieldCheck size={26} />
                      <div>لا مقترحات بانتظار قرارك</div>
                      <div className="hint">
                        عندما يقترح لقمان إجراءً (فريق، موظف، تعديل عقد) يظهر هنا أولاً
                        وينتظر اعتمادك.
                      </div>
                    </>
                  ) : (
                    <>
                      <Inbox size={26} />
                      <div>لا توجد إجراءات بهذه الحالة.</div>
                    </>
                  )}
                </div>
              ) : (
                <ul role="list">
                  {activeItems.map((action) => {
                    const meta = kindMeta(action.kind);
                    const tag = STATUS_TAGS[action.status];
                    const isConfirming = confirming?.id === action.id;
                    const created = localCreatedAt[action.id];
                    return (
                      <li key={action.id} className="rail-card">
                        <div className="rail-card-head">
                          <span className="os-tag">
                            <meta.Icon size={11} />
                            {meta.label}
                          </span>
                          <span className={`os-tag ${tag.cls}`}>{tag.label}</span>
                          <span className="mono rail-card-id">#{action.id.slice(0, 8)}</span>
                        </div>

                        <p className="rail-card-summary" dir="auto">
                          {action.summary}
                        </p>

                        <div className="rail-card-meta">
                          {action.status === 'pending' ? (
                            created ? (
                              <>اقترحه لقمان · {fmtClock(created)}</>
                            ) : (
                              <>بواسطة {action.created_by || 'لقمان'}</>
                            )
                          ) : action.decided_at ? (
                            <>القرار: {fmtDateTime(action.decided_at)}</>
                          ) : null}
                        </div>

                        {action.status === 'failed' && action.decision_note ? (
                          <div dir="auto" className="text-[11px] text-[var(--red)] leading-relaxed">
                            {action.decision_note}
                          </div>
                        ) : null}

                        {action.status === 'pending' && !isConfirming ? (
                          <div className="rail-card-actions">
                            <button
                              onClick={() => setConfirming({ id: action.id, mode: 'approve' })}
                              className="os-btn os-btn-sm"
                            >
                              <Check size={13} />
                              اعتماد
                            </button>
                            <button
                              onClick={() => setConfirming({ id: action.id, mode: 'deny' })}
                              className="os-btn os-btn-ghost os-btn-sm text-[var(--red)] border-[var(--red)]/30 hover:bg-[var(--red)]/10"
                            >
                              <X size={13} />
                              رفض
                            </button>
                          </div>
                        ) : null}

                        {isConfirming ? (
                          <div
                            role="group"
                            aria-label="تأكيد القرار"
                            className={`rail-gate ${confirming?.mode === 'approve' ? 'ok' : 'err'}`}
                          >
                            <p>
                              {confirming?.mode === 'approve'
                                ? 'تأكيد اعتماد هذا الإجراء؟ سيُنفَّذ فوراً.'
                                : 'تأكيد رفض هذا الإجراء؟ لن يُنفَّذ شيء.'}
                            </p>
                            <div className="btns">
                              <button
                                onClick={() => void decide(action.id, confirming?.mode ?? 'approve')}
                                disabled={decidingId === action.id}
                                className={
                                  confirming?.mode === 'approve'
                                    ? 'os-btn os-btn-sm'
                                    : 'os-btn os-btn-danger os-btn-sm'
                                }
                              >
                                {decidingId === action.id ? (
                                  <Loader2 size={13} className="animate-spin" />
                                ) : confirming?.mode === 'approve' ? (
                                  <Check size={13} />
                                ) : (
                                  <X size={13} />
                                )}
                                {confirming?.mode === 'approve' ? 'تأكيد الاعتماد' : 'تأكيد الرفض'}
                              </button>
                              <button
                                onClick={() => setConfirming(null)}
                                disabled={decidingId === action.id}
                                className="os-btn os-btn-ghost os-btn-sm"
                              >
                                إلغاء
                              </button>
                            </div>
                          </div>
                        ) : null}
                      </li>
                    );
                  })}
                </ul>
              )}
            </div>

            <div className="rail-footer">
              <ShieldCheck size={13} />
              لا يُنفَّذ أي إجراء قبل اعتمادك.
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
};
