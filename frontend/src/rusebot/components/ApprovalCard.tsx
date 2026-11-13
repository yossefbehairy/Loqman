/*
 * Visual structure follows RuseBot 0.1.127 src/components/ApprovalCard.tsx and
 * PendingApproval.tsx (Apache-2.0, Copyright 2026 Milind Soni and OpenMausBot
 * contributors): card container, title row, monospace detail block, status
 * line and the rounded approve/deny controls. Decisions go to the AgentOS
 * worker (approvals.decide for tool permissions, ceo.approve / ceo.deny for
 * manager proposals).
 */
import { useState } from 'react';
import { Check, ShieldCheck, X } from 'lucide-react';
import { cn } from '../lib/cn';

const APPROVAL_CONTROL =
  'inline-flex h-7 shrink-0 items-center justify-center gap-1.5 whitespace-nowrap rounded-full px-3 text-[13px] font-medium leading-5 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus disabled:opacity-60';

export interface ApprovalCardProps {
  title: string;
  detail: string;
  scope?: string;
  kind: 'tool' | 'manager';
  onDecide: (decision: 'approve' | 'deny') => Promise<void>;
}

export function ApprovalCard({ title, detail, scope, kind, onDecide }: ApprovalCardProps) {
  const [busy, setBusy] = useState<null | 'approve' | 'deny'>(null);
  const [outcome, setOutcome] = useState<null | 'approve' | 'deny'>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const decide = async (decision: 'approve' | 'deny') => {
    setBusy(decision);
    setFailure(null);
    try {
      await onDecide(decision);
      setOutcome(decision);
    } catch (e) {
      setFailure(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };
  const settled = outcome !== null;
  return (
    <div
      data-approval-card={kind}
      className={cn(
        'w-full max-w-[840px] rounded-2xl border bg-card p-4',
        settled ? 'border-hairline/30 opacity-70' : 'border-accent/40'
      )}
    >
      <div className="flex items-baseline justify-between gap-3">
        <div dir="auto" className="text-[15px] font-semibold text-ink">
          {title}
        </div>
        {scope && <span className="shrink-0 font-mono text-[11px] text-ink-secondary">{scope}</span>}
      </div>
      <pre
        tabIndex={0}
        aria-label="Details"
        className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap break-words rounded-lg bg-inset px-3 py-2 font-mono text-[12.5px] leading-relaxed text-ink"
      >
        {detail}
      </pre>
      {failure && (
        <p role="alert" className="mt-2 text-[12.5px] text-danger">
          {failure}
        </p>
      )}
      <div className="mt-3 flex flex-wrap items-center justify-between gap-2 text-[13px] text-ink-secondary">
        <span className="flex items-center gap-1.5">
          {outcome ? (
            <>
              {outcome === 'approve' ? <Check size={14} className="text-success" /> : <X size={14} />}
              {outcome === 'approve' ? 'Approved' : 'Denied'}
            </>
          ) : (
            <>
              <ShieldCheck size={14} className="text-accent" />
              Waiting for your answer
            </>
          )}
        </span>
        {!settled && (
          <span className="flex items-center gap-2">
            <button
              type="button"
              disabled={busy !== null}
              onClick={() => void decide('deny')}
              className={cn(APPROVAL_CONTROL, 'bg-control text-ink hover:bg-raised-hover')}
            >
              Deny
            </button>
            <button
              type="button"
              disabled={busy !== null}
              onClick={() => void decide('approve')}
              className={cn(APPROVAL_CONTROL, 'bg-accent text-accent-ink hover:brightness-110')}
            >
              Approve
            </button>
          </span>
        )}
      </div>
    </div>
  );
}
