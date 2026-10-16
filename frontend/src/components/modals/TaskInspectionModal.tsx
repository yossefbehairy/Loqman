import React, { useState } from 'react';
import type { TaskDeliverable } from '../../types/agentos';
import {
  X,
  CheckCircle2,
  AlertCircle,
  Copy,
  Check,
  MessagesSquare,
  FileCode2,
  ArrowRight,
  ShieldCheck,
  Clock,
} from 'lucide-react';

interface Props {
  deliverable: TaskDeliverable;
  reviewId?: string;
  reviewStatus?: 'pending' | 'approved' | 'changes';
  onClose: () => void;
  onApprove?: (id: string) => void;
  onRequestChanges?: (id: string) => void;
}

export const TaskInspectionModal: React.FC<Props> = ({
  deliverable,
  reviewId,
  reviewStatus,
  onClose,
  onApprove,
  onRequestChanges,
}) => {
  const [activeTab, setActiveTab] = useState<'dialogue' | 'output'>('dialogue');
  const [isCopied, setIsCopied] = useState(false);

  const copyJson = async () => {
    if (!deliverable.fullJson) return;
    try {
      await navigator.clipboard.writeText(JSON.stringify(deliverable.fullJson, null, 2));
    } catch {
      try {
        const ta = document.createElement('textarea');
        ta.value = JSON.stringify(deliverable.fullJson, null, 2);
        ta.style.position = 'fixed';
        ta.style.opacity = '0';
        document.body.appendChild(ta);
        ta.select();
        document.execCommand('copy');
        ta.remove();
      } catch {
        return;
      }
    }
    setIsCopied(true);
    window.setTimeout(() => setIsCopied(false), 2000);
  };

  const isPending = reviewStatus === 'pending' && !!reviewId;
  const isApproved = reviewStatus === 'approved';
  const isReviewContext = !!reviewId;

  return (
    <div
      className="fixed inset-0 bg-black/70 backdrop-blur-md flex items-center justify-center p-3 sm:p-5 z-50 animate-in fade-in duration-150"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label={deliverable.title}
        className="os-card w-full max-w-3xl max-h-[92vh] flex flex-col p-0 overflow-hidden relative shadow-2xl border-[var(--line-strong)]"
      >
        {/* Modal Top Header */}
        <div className="p-4 sm:p-5 bg-[var(--card2)]/90 border-b border-[var(--line)] flex items-start justify-between gap-4">
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2.5 flex-wrap mb-1">
              <span className="os-tag os-tag-mono text-xs font-semibold">
                {deliverable.department}
              </span>
              <span
                className={`os-tag ${
                  isApproved
                    ? 'os-tag-green'
                    : reviewStatus === 'changes'
                    ? 'os-tag-red'
                    : 'os-tag-amber'
                } text-xs font-semibold`}
              >
                {isApproved
                  ? 'Approved'
                  : reviewStatus === 'changes'
                  ? 'Changes requested'
                  : 'Waiting for review'}
              </span>
              {deliverable.tasksCount !== undefined && (
                <span className="text-[11px] text-[var(--muted)] mono">
                  {deliverable.tasksCount} tasks checked
                  {deliverable.heldCount ? ` · ${deliverable.heldCount} held for review` : ''}
                </span>
              )}
            </div>

            <h2
              className="text-lg sm:text-xl font-bold text-[var(--text)] tracking-tight truncate"
              title={deliverable.title}
            >
              {deliverable.title}
            </h2>
            <p className="text-xs text-[var(--muted)] mt-0.5 line-clamp-1 leading-relaxed">
              {deliverable.summary}
            </p>
          </div>

          <button
            onClick={onClose}
            className="p-1.5 rounded-lg text-[var(--muted)] hover:text-[var(--text)] hover:bg-white/5 transition-colors flex-none"
            title="Close modal (Esc)"
          >
            <X size={18} />
          </button>
        </div>

        {/* View Switcher Tabs */}
        <div className="px-5 pt-3 border-b border-[var(--line)] bg-[var(--card2)]/40 flex items-center gap-2">
          <button
            onClick={() => setActiveTab('dialogue')}
            className={`flex items-center gap-2 px-3.5 py-2 border-b-2 text-xs font-semibold transition-all ${
              activeTab === 'dialogue'
                ? 'border-[var(--acc)] text-[var(--acc)]'
                : 'border-transparent text-[var(--muted)] hover:text-[var(--text)]'
            }`}
          >
            <MessagesSquare size={14} />
            <span>حوار ومناقشة الفريق (Inter-Agent Dialogue)</span>
            <span className="mono text-[10px] px-1.5 py-0.5 rounded-full bg-[var(--card)] text-[var(--text)]">
              {deliverable.turns.length}
            </span>
          </button>

          <button
            onClick={() => setActiveTab('output')}
            className={`flex items-center gap-2 px-3.5 py-2 border-b-2 text-xs font-semibold transition-all ${
              activeTab === 'output'
                ? 'border-[var(--acc)] text-[var(--acc)]'
                : 'border-transparent text-[var(--muted)] hover:text-[var(--text)]'
            }`}
          >
            <FileCode2 size={14} />
            <span>المخرجات والتقرير الفني (Deliverable Output)</span>
          </button>
        </div>

        {/* Modal Scrollable Body */}
        <div className="flex-1 overflow-y-auto p-4 sm:p-5 min-h-0 text-xs">
          {activeTab === 'dialogue' ? (
            <div className="flex flex-col gap-3">
              <div className="p-3 rounded-xl bg-[var(--acc)]/10 border border-[var(--acc)]/25 text-[var(--text)] text-xs flex items-center gap-2.5">
                <ShieldCheck size={16} className="text-[var(--acc)] flex-none" />
                <span>
                  هذا السجل يعرض التبادل الحي والنقاش الفني بين الوكلاء (Inter-Agent Handover) حتى الوصول لهذه المخرجات:
                </span>
              </div>

              {deliverable.turns.map((t, idx) => (
                <div
                  key={`${t.turn}_${idx}_${t.from}`}
                  className="p-3.5 rounded-2xl bg-[var(--card2)] border border-[var(--line)] flex flex-col gap-2 hover:border-[var(--line-strong)] transition-all"
                >
                  <div className="flex items-center justify-between gap-2 flex-wrap">
                    <div className="flex items-center gap-2">
                      <span className="mono text-[10px] font-bold px-2 py-0.5 rounded-full bg-[var(--card)] text-[var(--acc)] border border-[var(--line2)]">
                        Turn {t.turn}
                      </span>
                      <b className="text-[var(--text)] text-xs font-semibold">{t.from}</b>
                      <span className="text-[10px] text-[var(--dim)]">({t.role})</span>
                      <ArrowRight size={12} className="text-[var(--dim)]" />
                      <span className="text-xs text-[var(--muted)]">{t.to}</span>
                    </div>

                    <span className="mono text-[11px] text-[var(--dim)] flex items-center gap-1">
                      <Clock size={11} />
                      {t.time}
                    </span>
                  </div>

                  <div className="p-3 rounded-xl bg-[var(--card)]/80 text-xs text-[var(--text)] leading-relaxed whitespace-pre-wrap select-text font-sans border border-[var(--line)]/50">
                    {t.text}
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <div className="flex flex-col gap-3.5">
              {deliverable.fullJson ? (
                <>
                  <div className="flex items-center justify-between">
                    <span className="font-semibold text-xs text-[var(--muted)] uppercase tracking-wider">
                      Structured Deliverable JSON Schema
                    </span>
                    <button
                      onClick={copyJson}
                      className="os-btn os-btn-ghost os-btn-sm text-[11px] flex items-center gap-1.5"
                    >
                      {isCopied ? <Check size={12} className="text-[var(--green)]" /> : <Copy size={12} />}
                      <span>{isCopied ? 'Copied' : 'Copy JSON'}</span>
                    </button>
                  </div>

                  <pre className="mono p-4 rounded-xl bg-[var(--card2)] border border-[var(--line)] text-[11.5px] text-[var(--acc)] overflow-x-auto max-h-[380px] select-text leading-relaxed">
                    {JSON.stringify(deliverable.fullJson, null, 2)}
                  </pre>
                </>
              ) : (
                <div className="p-8 text-center text-xs text-[var(--muted)]">
                  No structured JSON payload attached to this task.
                </div>
              )}
            </div>
          )}
        </div>

        {/* Modal Footer Actions */}
        <div className="p-4 bg-[var(--card2)]/90 border-t border-[var(--line)] flex items-center justify-between gap-3">
          <div className="text-[11px] text-[var(--dim)] hidden sm:block">
            {isApproved
              ? 'Approved by supervisor'
              : isReviewContext
              ? 'Waiting for human verification in Inbox'
              : 'Active work — no review required'}
          </div>

          <div className="flex items-center gap-2 ml-auto">
            {isPending && onApprove && reviewId && (
              <button
                onClick={() => {
                  onApprove(reviewId);
                  onClose();
                }}
                className="os-btn os-btn-sm"
              >
                <CheckCircle2 size={13} />
                Approve deliverable
              </button>
            )}

            {isPending && onRequestChanges && reviewId && (
              <button
                onClick={() => {
                  onRequestChanges(reviewId);
                  onClose();
                }}
                className="os-btn os-btn-ghost os-btn-sm text-[var(--red)] border-[var(--red)]/30 hover:bg-[var(--red)]/10"
              >
                <AlertCircle size={13} />
                Request changes
              </button>
            )}

            <button onClick={onClose} className="os-btn os-btn-ghost os-btn-sm">
              Close
            </button>
          </div>
        </div>
      </div>
    </div>
  );
};
