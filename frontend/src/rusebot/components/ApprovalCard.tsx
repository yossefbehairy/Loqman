/*
 * Visual structure follows RuseBot 0.1.127 src/components/ApprovalCard.tsx and
 * PendingApproval.tsx (Apache-2.0, Copyright 2026 Milind Soni and OpenMausBot
 * contributors): card container, title row, monospace detail block, status
 * line and the rounded approve/deny controls. Decisions go to the Loqman
 * worker (approvals.decide for tool permissions, ceo.approve / ceo.deny for
 * manager proposals).
 */
import { useState } from 'react';
import { Check, ShieldCheck, X } from 'lucide-react';
import { cn } from '../lib/cn';

const APPROVAL_CONTROL =
  'inline-flex h-7 shrink-0 items-center justify-center gap-1.5 whitespace-nowrap rounded-full px-3 text-[13px] font-medium leading-5 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus disabled:opacity-60';

/**
 * `{ failed }`: the decision was accepted but the action could not be applied.
 * `{ already }`: Loqman decided it first (it applies the agents and teams it proposes itself).
 */
export type DecideResult = void | { failed: string } | { already: 'approve' | 'deny' };

export interface ApprovalCardProps {
  title: string;
  detail: string;
  scope?: string;
  kind: 'tool' | 'manager';
  confirmedDecision?: 'approve' | 'deny';
  decisionPending?: boolean;
  decisionError?: string;
  onRetryStatus?: () => void;
  refreshingStatus?: boolean;
  onDecide: (decision: 'approve' | 'deny') => Promise<DecideResult>;
}

export function ApprovalCard({
  title, detail, scope, kind, confirmedDecision, decisionPending, decisionError, onRetryStatus, refreshingStatus, onDecide,
}: ApprovalCardProps) {
  const [busy, setBusy] = useState<null | 'approve' | 'deny'>(null);
  const [localOutcome, setOutcome] = useState<null | 'approve' | 'deny'>(null);
  const outcome = confirmedDecision ?? localOutcome;
  const [failure, setFailure] = useState<string | null>(null);
  const [applyError, setApplyError] = useState<string | null>(null);
  const [byLoqman, setByLoqman] = useState(false);
  const decide = async (decision: 'approve' | 'deny') => {
    if (outcome || busy || decisionPending) return;
    if (kind === 'tool') {
      await onDecide(decision);
      return;
    }
    setBusy(decision);
    setFailure(null);
    setApplyError(null);
    try {
      const result = await onDecide(decision);
      if (result && 'failed' in result) setApplyError(result.failed);
      if (result && 'already' in result) {
        setByLoqman(true);
        setOutcome(result.already);
        return;
      }
      setOutcome(decision);
    } catch (e) {
      setFailure(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };
  const settled = outcome !== null;
  let waitingLabel = 'Waiting for your answer';
  if (kind === 'tool' && decisionPending) {
    waitingLabel = 'Submitting decision…';
    if (decisionError) waitingLabel = onRetryStatus ? 'Decision unconfirmed' : 'Approval unavailable';
  }
  return (
    <div
      data-approval-card={kind}
      className={cn(
        'w-full max-w-[840px] rounded-2xl border bg-card p-4',
        settled ? 'approval-settled border-hairline/20' : 'border-accent/40'
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
      {applyError && (
        <p role="alert" className="mt-2 text-[12.5px] text-danger">
          {applyError}
        </p>
      )}
      {(decisionError || failure) && (
        <p role="alert" className="mt-2 text-[12.5px] text-danger">
          {decisionError ?? failure}
        </p>
      )}
      {onRetryStatus && (
        <button
          type="button"
          disabled={refreshingStatus}
          onClick={onRetryStatus}
          className="mt-1 text-[12.5px] font-medium underline disabled:opacity-60"
        >
          Retry status
        </button>
      )}
      <div className="mt-3 flex flex-wrap items-center justify-between gap-2 text-[13px] text-ink-secondary">
        <span className="flex items-center gap-1.5">
          {outcome ? (
            <>
              {outcome === 'approve' && !applyError ? <Check size={14} className="text-success" /> : <X size={14} />}
              {byLoqman
                ? `Loqman already ${outcome === 'approve' ? 'approved' : 'denied'} this`
                : outcome === 'approve'
                  ? applyError
                    ? 'Not applied'
                    : 'Approved'
                  : 'Denied'}
            </>
          ) : (
            <>
              <ShieldCheck size={14} className="text-accent" />
              {waitingLabel}
            </>
          )}
        </span>
        {!settled && (
          <span className="flex items-center gap-2">
            <button
              type="button"
              disabled={busy !== null || decisionPending}
              onClick={() => void decide('deny')}
              className={cn(APPROVAL_CONTROL, 'bg-control text-ink hover:bg-raised-hover')}
            >
              Deny
            </button>
            <button
              type="button"
              disabled={busy !== null || decisionPending}
              onClick={() => void decide('approve')}
              className={cn(APPROVAL_CONTROL, 'approval-confirm bg-accent text-accent-ink')}
            >
              Approve
            </button>
          </span>
        )}
      </div>
    </div>
  );
}
