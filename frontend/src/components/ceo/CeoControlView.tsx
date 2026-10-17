import React, { useCallback, useEffect, useRef, useState } from 'react';
import type { CeoAction, CeoActionStatus, CeoCreatedProfile, CeoProposedAction, ModelCatalogEntry } from '../../types';
import { askCeo, approveCeoAction, denyCeoAction, getCeoAskProgress, listCeoActions } from '../../api/client';
import {
  AlertTriangle,
  Check,
  CheckCircle2,
  Clock3,
  Crown,
  FileText,
  Inbox,
  Loader2,
  RefreshCw,
  Send,
  ShieldCheck,
  UserRoundPlus,
  Users,
  X,
  XCircle,
} from 'lucide-react';

interface Props {
  /** Default model chosen by the user (Settings) — sent with every question. */
  selectedModelId: string;
  /** Reasoning variant for that model, when supported. */
  selectedVariant: string;
  /** Live free catalog (GET /api/models/free) for the inline model picker. */
  models?: ModelCatalogEntry[];
  /** Persist a new model/variant choice into the shared app settings. */
  onModelChange?: (modelId: string, variant: string) => void;
  /** Called after an approval that really created employee profiles. */
  onProfilesCreated?: (profiles: CeoCreatedProfile[], teamName: string | null) => void;
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
    return 'تعذّر وصول المدير العام إلى محرك OpenCode — حاول مرة أخرى بعد قليل.';
  }
  return raw;
}

/** Maps backend ask-progress stages to short Arabic status text. */
function progressLabel(stage: string, detail: string): string {
  switch (stage) {
    case 'starting':
      return 'بيهيّئ جلسة المحادثة…';
    case 'thinking':
      return 'المدير العام بيفكر…';
    case 'tool':
      return `بيجمع بيانات: ${detail || 'أداة داخلية'}`;
    case 'final':
      return 'بيكتب الرد النهائي…';
    case 'done':
      return 'الرد جاهز';
    case 'error':
      return 'حصل خطأ — بنقفل المحاولة…';
    default:
      return 'المدير العام يراجع بيانات الشركة…';
  }
}

export const CeoControlView: React.FC<Props> = ({
  selectedModelId,
  selectedVariant,
  models,
  onModelChange,
  onProfilesCreated,
}) => {
  // Ask surface
  const [question, setQuestion] = useState('');
  const [sending, setSending] = useState(false);
  const [askError, setAskError] = useState('');
  const [reply, setReply] = useState('');
  const [proposed, setProposed] = useState<CeoProposedAction[]>([]);
  // Proposals recorded in this session carry a local creation time (the API
  // exposes decided_at but not created_at).
  const [localCreatedAt, setLocalCreatedAt] = useState<Record<string, number>>({});

  // Live ask progress (polled while the synchronous ask is in flight)
  const [progressStage, setProgressStage] = useState('');
  const [progressDetail, setProgressDetail] = useState('');
  const [elapsedSec, setElapsedSec] = useState(0);
  const tickRef = useRef<number | null>(null);
  const pollRef = useRef<number | null>(null);

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

  const variantOptions = (models || []).find((m) => m.id === selectedModelId)?.variants || [];

  const handleModelSelect = (nextId: string) => {
    const picked = (models || []).find((m) => m.id === nextId);
    const nextVariant = picked?.variants.includes(selectedVariant)
      ? selectedVariant
      : picked?.variants[0] || selectedVariant;
    onModelChange?.(nextId, nextVariant);
  };

  // Approvals inbox
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

  const handleAsk = async () => {
    const trimmed = question.trim();
    if (!trimmed || sending) return;
    setSending(true);
    setAskError('');
    setProgressStage('starting');
    setProgressDetail('');
    setElapsedSec(0);

    // Client-generated id: the backend records live stages for it while the
    // sync POST runs, so the UI can show what the CEO is doing right now.
    const requestId =
      typeof crypto !== 'undefined' && 'randomUUID' in crypto
        ? crypto.randomUUID()
        : `ask-${Date.now()}-${Math.random().toString(16).slice(2)}`;
    const startedAt = Date.now();
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
      setReply(response.reply);
      setProposed(response.actions_proposed);
      if (response.actions_proposed.length > 0) {
        const now = Date.now();
        setLocalCreatedAt((prev) => {
          const next = { ...prev };
          response.actions_proposed.forEach((action) => {
            next[action.id] = now;
          });
          return next;
        });
        void reloadQuiet(); // Surface the fresh proposals in the inbox immediately.
      }
      setQuestion('');
    } catch (error) {
      setAskError(humanizeError(error));
    } finally {
      stopAskWatchers();
      setProgressStage('');
      setProgressDetail('');
      setSending(false);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      void handleAsk();
    }
  };

  const decide = async (action: CeoAction, mode: 'approve' | 'deny') => {
    setDecidingId(action.id);
    setDecisionError('');
    try {
      const updated =
        mode === 'approve' ? await approveCeoAction(action.id) : await denyCeoAction(action.id);
      setLastDecision(updated);
      if (
        mode === 'approve' &&
        updated.created_profiles &&
        updated.created_profiles.length > 0
      ) {
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

  const activeItems = byStatus[tab];

  return (
    <div className="flex flex-col gap-4 max-w-6xl min-w-0">
      {/* Page Header */}
      <div>
        <h1 className="text-2xl font-bold tracking-tight text-[var(--text)] m-0">
          CEO Control Plane (غرفة القيادة)
        </h1>
        <p className="text-xs text-[var(--muted)] m-0 mt-1">
          اسأل المدير العام من البيانات الحية، واعتمد مقترحاته — لا يُنفَّذ أي إجراء قبل قرارك.
        </p>
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-2 gap-3 items-start min-w-0">
        {/* ----------------------------------------------------------------- */}
        {/* Ask the CEO                                                       */}
        {/* ----------------------------------------------------------------- */}
        <div className="os-card flex flex-col gap-3.5 min-w-0">
          <div className="flex items-center gap-2.5 min-w-0">
            <span className="w-8 h-8 rounded-xl bg-[var(--acc)]/10 border border-[var(--line2)] flex items-center justify-center flex-none">
              <Crown size={15} className="text-[var(--acc)]" />
            </span>
            <div className="min-w-0 flex-1">
              <h2 className="text-sm font-semibold text-[var(--text)] m-0">اسأل المدير العام</h2>
              {models && models.length > 0 ? (
                <div className="flex items-center gap-1.5 flex-wrap">
                  <select
                    value={models.some((m) => m.id === selectedModelId) ? selectedModelId : ''}
                    onChange={(e) => handleModelSelect(e.target.value)}
                    aria-label="موديل المدير العام"
                    className="mono text-[10px] py-0.5 px-1.5 rounded-lg bg-[var(--card2)] border border-[var(--line)] text-[var(--text)] max-w-[240px]"
                  >
                    {!models.some((m) => m.id === selectedModelId) && (
                      <option value="">{selectedModelId || 'default model'} (غير متحقق)</option>
                    )}
                    {models.map((m) => (
                      <option key={m.id} value={m.id}>
                        {m.name}
                      </option>
                    ))}
                  </select>
                  {variantOptions.length > 0 && (
                    <select
                      value={selectedVariant}
                      onChange={(e) => onModelChange?.(selectedModelId, e.target.value)}
                      aria-label="مستوى التفكير"
                      className="mono text-[10px] py-0.5 px-1.5 rounded-lg bg-[var(--card2)] border border-[var(--line)] text-[var(--text)]"
                    >
                      {variantOptions.map((v) => (
                        <option key={v} value={v}>
                          {v}
                        </option>
                      ))}
                    </select>
                  )}
                </div>
              ) : (
                <div className="mono text-[10px] text-[var(--dim)] truncate">
                  {selectedModelId || 'default model'}
                  {selectedVariant ? ` · ${selectedVariant}` : ''}
                </div>
              )}
            </div>
          </div>

          <textarea
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="اكتب سؤالاً عن الشركة… مثال: كام مهمة خلصت؟"
            aria-label="سؤال للمدير العام"
            rows={3}
            className="resize-none text-xs leading-relaxed"
          />

          <div className="flex items-center gap-2.5 flex-wrap">
            <span className="text-[10px] text-[var(--dim)]">
              Enter للإرسال · Shift+Enter لسطر جديد
            </span>
            <button
              onClick={() => void handleAsk()}
              disabled={!question.trim() || sending}
              className="os-btn ml-auto flex-none"
            >
              {sending ? (
                <Loader2 size={14} className="animate-spin" />
              ) : (
                <Send size={14} />
              )}
              <span>{sending ? 'جارٍ التحليل…' : 'إرسال'}</span>
            </button>
          </div>

          {sending && (
            <div className="flex items-center gap-2 text-xs text-[var(--muted)] flex-wrap" role="status">
              <span className="w-1.5 h-1.5 rounded-full bg-[var(--acc)] animate-pulse inline-block" />
              <span>{progressLabel(progressStage, progressDetail)}</span>
              <span className="mono text-[10px] text-[var(--dim)]">· {elapsedSec}s</span>
            </div>
          )}

          {askError ? (
            <div
              role="alert"
              className="rounded-xl border border-[var(--red)]/30 bg-[var(--red)]/8 px-3.5 py-3 text-xs text-[var(--red)] leading-relaxed"
            >
              {askError}
            </div>
          ) : null}

          {reply && !sending ? (
            <div
              role="region"
              aria-label="رد المدير العام"
              className="rounded-xl border border-[var(--line)] bg-[var(--card2)]/50 p-3.5 flex flex-col gap-2"
            >
              <div className="mono text-[10px] text-[var(--dim)]">CEO · المدير العام</div>
              <div
                dir="auto"
                className="text-xs leading-loose text-[var(--text)] whitespace-pre-wrap max-w-[72ch]"
              >
                {reply}
              </div>
            </div>
          ) : null}

          {proposed.length > 0 && !sending ? (
            <div className="flex flex-col gap-2">
              <div className="text-[11px] text-[var(--amber)] flex items-center gap-1.5">
                <Clock3 size={12} />
                مقترحات جديدة بانتظار اعتمادك — لا يُنفَّذ شيء قبل قرارك
              </div>
              {proposed.map((action) => {
                const meta = kindMeta(action.kind);
                return (
                  <div
                    key={action.id}
                    className="rounded-xl border border-[var(--amber)]/30 bg-[var(--amber)]/5 px-2.5 py-2 flex items-center gap-2.5 flex-wrap"
                  >
                    <span className="os-tag os-tag-amber">
                      <meta.Icon size={11} />
                      {meta.label}
                    </span>
                    <span
                      dir="auto"
                      className="text-xs text-[var(--text)] min-w-0 flex-1 truncate"
                      title={action.summary}
                    >
                      {action.summary}
                    </span>
                    <span className="text-[10px] font-semibold text-[var(--amber)] flex-none">
                      بانتظار اعتمادك
                    </span>
                  </div>
                );
              })}
            </div>
          ) : null}
        </div>

        {/* ----------------------------------------------------------------- */}
        {/* Approvals inbox                                                   */}
        {/* ----------------------------------------------------------------- */}
        <div className="os-card flex flex-col gap-3 min-w-0">
          <div className="flex items-center gap-2.5 min-w-0">
            <h2 className="text-sm font-semibold text-[var(--text)] m-0">صندوق الاعتماد</h2>
            {byStatus.pending.length > 0 ? (
              <span className="os-tag os-tag-amber">
                {byStatus.pending.length > 1
                  ? `${byStatus.pending.length} إجراءات بانتظار قرارك`
                  : 'إجراء بانتظار قرارك'}
              </span>
            ) : null}
            <button
              onClick={refresh}
              disabled={loading}
              aria-label="تحديث المقترحات"
              title="تحديث المقترحات"
              className="os-btn os-btn-ghost os-btn-sm ml-auto flex-none"
            >
              <RefreshCw size={12} className={loading ? 'animate-spin' : ''} />
              <span className="hidden sm:inline">تحديث</span>
            </button>
          </div>

          <p className="text-[11px] text-[var(--muted)] m-0">
            قراءة بلا حدود · الكتابة بتأكيدك أنت.
          </p>

          {lastDecision ? (
            <div
              role={lastDecision.status === 'failed' ? 'alert' : 'status'}
              className={`rounded-xl border px-3.5 py-2.5 flex items-start gap-2.5 ${
                lastDecision.status === 'approved'
                  ? 'border-[var(--green)]/30 bg-[var(--green)]/8'
                  : 'border-[var(--red)]/30 bg-[var(--red)]/8'
              }`}
            >
              {lastDecision.status === 'approved' ? (
                <CheckCircle2 size={15} className="text-[var(--green)] flex-none mt-0.5" />
              ) : lastDecision.status === 'denied' ? (
                <XCircle size={15} className="text-[var(--red)] flex-none mt-0.5" />
              ) : (
                <AlertTriangle size={15} className="text-[var(--red)] flex-none mt-0.5" />
              )}
              <div className="min-w-0 flex-1 flex flex-col gap-1">
                <b
                  className={`text-xs font-semibold ${
                    lastDecision.status === 'approved'
                      ? 'text-[var(--green)]'
                      : 'text-[var(--red)]'
                  }`}
                >
                  {lastDecision.status === 'approved'
                    ? 'تم اعتماد الإجراء وتنفيذه.'
                    : lastDecision.status === 'denied'
                      ? 'تم رفض الإجراء — لم يُنفَّذ شيء.'
                      : 'فشل التنفيذ بعد الاعتماد.'}
                </b>
                <span dir="auto" className="mono text-[10px] text-[var(--muted)] break-words">
                  {lastDecision.summary}
                </span>
                {lastDecision.status === 'failed' && lastDecision.decision_note ? (
                  <span dir="auto" className="text-[10px] text-[var(--red)] leading-relaxed">
                    {lastDecision.decision_note}
                  </span>
                ) : null}
              </div>
              <button
                onClick={() => setLastDecision(null)}
                aria-label="إغلاق التنبيه"
                className="os-btn os-btn-ghost os-btn-sm flex-none"
              >
                <X size={12} />
              </button>
            </div>
          ) : null}

          {decisionError ? (
            <div
              role="alert"
              className="rounded-xl border border-[var(--red)]/30 bg-[var(--red)]/8 px-3.5 py-3 text-xs text-[var(--red)] leading-relaxed"
            >
              {decisionError}
            </div>
          ) : null}

          {loadError ? (
            <div
              role="alert"
              className="rounded-xl border border-[var(--red)]/30 bg-[var(--red)]/8 px-3.5 py-3 text-xs text-[var(--red)] leading-relaxed flex items-center gap-2.5 flex-wrap"
            >
              <span className="flex-1 min-w-0">{loadError}</span>
              <button onClick={refresh} className="os-btn os-btn-ghost os-btn-sm flex-none">
                <RefreshCw size={12} />
                إعادة المحاولة
              </button>
            </div>
          ) : null}

          {/* Status filter tabs */}
          <div className="flex items-center gap-1.5 border-b border-[var(--line)] pb-2 flex-wrap">
            {STATUSES.map((status) => {
              const isActive = tab === status;
              return (
                <button
                  key={status}
                  onClick={() => setTab(status)}
                  aria-label={TAB_LABELS[status]}
                  className={`px-3 py-1.5 rounded-xl text-xs font-medium transition-all flex items-center gap-2 ${
                    isActive
                      ? 'bg-[var(--acc)]/15 text-[var(--text)] border border-[var(--acc)]/30'
                      : 'text-[var(--muted)] hover:text-[var(--text)] hover:bg-white/5 border border-transparent'
                  }`}
                >
                  <span>{TAB_LABELS[status]}</span>
                  <span className="mono text-[10px] px-1.5 py-0.5 bg-[var(--card2)] rounded-full text-[var(--dim)]">
                    {byStatus[status].length}
                  </span>
                </button>
              );
            })}
          </div>

          {/* Action list */}
          {loading && activeItems.length === 0 ? (
            <div className="flex items-center justify-center gap-2 py-12 text-xs text-[var(--muted)]">
              <Loader2 size={14} className="animate-spin" />
              جارٍ تحميل المقترحات…
            </div>
          ) : activeItems.length === 0 ? (
            <div className="text-center py-12 px-4">
              {tab === 'pending' ? (
                <>
                  <ShieldCheck size={26} className="mx-auto mb-2 text-[var(--dim)]" />
                  <div className="text-xs font-medium text-[var(--muted)]">لا مقترحات بانتظار قرارك</div>
                  <div className="text-[11px] text-[var(--dim)] mt-1 leading-relaxed">
                    عندما يقترح المدير العام إجراءً (فريق، موظف، تعديل عقد) يظهر هنا أولاً
                    وينتظر اعتمادك.
                  </div>
                </>
              ) : (
                <>
                  <Inbox size={26} className="mx-auto mb-2 text-[var(--dim)]" />
                  <div className="text-xs text-[var(--muted)]">لا توجد إجراءات بهذه الحالة.</div>
                </>
              )}
            </div>
          ) : (
            <ul role="list" className="flex flex-col gap-2.5 m-0 p-0 list-none">
              {activeItems.map((action) => {
                const meta = kindMeta(action.kind);
                const tag = STATUS_TAGS[action.status];
                const isConfirming = confirming?.id === action.id;
                const created = localCreatedAt[action.id];
                return (
                  <li
                    key={action.id}
                    className="rounded-xl border border-[var(--line)] bg-[var(--card2)]/45 p-3.5 flex flex-col gap-2"
                  >
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="os-tag">
                        <meta.Icon size={11} />
                        {meta.label}
                      </span>
                      <span className={`os-tag ${tag.cls}`}>{tag.label}</span>
                      <span className="mono text-[10px] text-[var(--dim)] ml-auto">
                        #{action.id.slice(0, 8)}
                      </span>
                    </div>

                    <p dir="auto" className="text-xs text-[var(--text)] leading-relaxed m-0">
                      {action.summary}
                    </p>

                    <div className="text-[10px] text-[var(--dim)]">
                      {action.status === 'pending' ? (
                        created ? (
                          <>اقترحه المدير العام · {fmtClock(created)}</>
                        ) : (
                          <>بواسطة {action.created_by || 'المدير العام'}</>
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
                      <div className="flex items-center gap-2 flex-wrap pt-0.5">
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
                        className={`rounded-xl border p-3 flex items-center gap-3 flex-wrap ${
                          confirming?.mode === 'approve'
                            ? 'border-[var(--acc)]/35 bg-[var(--acc)]/8'
                            : 'border-[var(--red)]/35 bg-[var(--red)]/8'
                        }`}
                      >
                        <span className="text-xs text-[var(--text)] leading-relaxed flex-1 min-w-[200px]">
                          {confirming?.mode === 'approve'
                            ? 'تأكيد اعتماد هذا الإجراء؟ سيُنفَّذ فوراً.'
                            : 'تأكيد رفض هذا الإجراء؟ لن يُنفَّذ شيء.'}
                        </span>
                        <div className="flex items-center gap-2 ml-auto">
                          <button
                            onClick={() => void decide(action, confirming?.mode ?? 'approve')}
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
      </div>
    </div>
  );
};
