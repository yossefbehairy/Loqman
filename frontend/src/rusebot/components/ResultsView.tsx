/*
 * Page chrome follows RuseBot 0.1.127 src/components/TeamMapPage.tsx header
 * (Apache-2.0, Copyright 2026 Milind Soni and OpenMausBot contributors).
 * Content is Loqman-specific: runs waiting on you (tool approvals, manager
 * proposals) and every deliverable draft produced by recorded runs.
 */
import { PackageCheck } from 'lucide-react';
import { ApprovalCard, type DecideResult } from './ApprovalCard';
import { DeliverableCard } from './DeliverableCard';
import type { CeoAction } from '../data/worker';
import type { ChatState } from '../state/useChat';
import { useKeepDecided } from '../state/useKeepDecided';

export function ResultsView({
  chat,
  ceoActions,
  ceoActionsConfirmed,
  onCeoDecide,
}: {
  chat: ChatState;
  ceoActions: CeoAction[];
  ceoActionsConfirmed: boolean;
  onCeoDecide: (action: CeoAction, decision: 'approve' | 'deny') => Promise<DecideResult>;
}) {
  const { list: drafts, loading, error } = chat.resultsDrafts;
  const { shown: managerActions, decide: decideAction } = useKeepDecided<CeoAction>(ceoActions);
  const waiting = chat.approvals.length + ceoActions.length;
  const approvalRead = chat.approvalRead;
  return (
    <main className="relative flex h-full min-w-0 flex-1 flex-col bg-app" data-view="results">
      <header className="flex shrink-0 flex-wrap items-center justify-between gap-3 border-b border-hairline/40 px-6 py-4 max-md:pl-12">
        <div>
          <div className="flex items-center gap-2.5">
            <PackageCheck size={18} />
            <h1 className="text-[17px] font-semibold">Results</h1>
            {drafts && <span className="ml-1 text-[11px] text-ink-secondary">{drafts.length} deliverables</span>}
          </div>
          <p className="mt-1 text-[12px] text-ink-secondary">What needs your decision, and the work your team has handed in.</p>
        </div>
      </header>
      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto flex w-full max-w-3xl flex-col gap-6 px-6 py-6">
          <section aria-label="Needs you" className="flex flex-col gap-2">
            <h2 className="text-[13px] font-semibold">Needs you ({waiting})</h2>
            {approvalRead.loading && !approvalRead.list && !approvalRead.error && (
              <p role="status" className="text-[13px] text-ink-secondary">Loading tool approvals…</p>
            )}
            {approvalRead.error && (
              <div className="flex items-start gap-2 text-[13px] text-danger">
                <p role="alert" dir="auto">
                  {approvalRead.list
                    ? 'Could not refresh tool approvals. Showing the last loaded tool approvals.'
                    : 'Could not load tool approvals.'} {approvalRead.error}
                </p>
                <button
                  type="button"
                  className="shrink-0 font-medium underline disabled:opacity-60"
                  disabled={approvalRead.loading}
                  onClick={() => void chat.refreshApprovals()}
                >
                  Retry
                </button>
              </div>
            )}
            {waiting === 0 && ceoActionsConfirmed && approvalRead.list && !approvalRead.loading && !approvalRead.error && (
              <p className="text-[13px] text-ink-secondary">Nothing is waiting on you.</p>
            )}
            {chat.toolApprovalCards.map(({ key, approval, outcome, locked, error, canRefresh }) => (
              <ApprovalCard
                key={key}
                kind="tool"
                title="A run wants permission"
                detail={approval.action_summary}
                scope={approval.scope}
                confirmedDecision={outcome}
                decisionPending={locked}
                decisionError={error}
                onRetryStatus={canRefresh ? chat.refreshApprovals : undefined}
                refreshingStatus={approvalRead.loading}
                onDecide={(decision) => chat.decide(approval, decision)}
              />
            ))}
            {managerActions.map((a) => (
              <ApprovalCard
                key={a.id}
                kind="manager"
                title={`Manager proposal · ${a.kind.replace(/_/g, ' ')}`}
                detail={a.summary}
                onDecide={(d) => decideAction(a, () => onCeoDecide(a, d))}
              />
            ))}
          </section>
          <section aria-label="Deliverables" className="flex flex-col gap-2">
            <h2 className="text-[13px] font-semibold">Deliverables</h2>
            {loading && <p role="status" className="text-[13px] text-ink-secondary">
              {drafts ? 'Refreshing deliverables…' : 'Loading deliverables…'}
            </p>}
            {error && (
              <div className="flex items-start gap-2 text-[13px] text-danger">
                <p role="alert" dir="auto">
                  {drafts ? 'Could not refresh deliverables. Showing the last loaded results.' : 'Could not load deliverables.'} {error}
                </p>
                <button type="button" className="shrink-0 font-medium underline" onClick={() => void chat.loadAllDrafts()}>Retry</button>
              </div>
            )}
            {!loading && !error && drafts?.length === 0 && (
              <p className="text-[13px] text-ink-secondary">
                No deliverables yet. Save a finished reply from a chat to review it here.
              </p>
            )}
            {drafts?.map((d) => (
              <DeliverableCard key={d.id} draft={d} onReview={chat.reviewDraft} onDownload={chat.downloadDraft} />
            ))}
          </section>
        </div>
      </div>
    </main>
  );
}
