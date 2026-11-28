/*
 * Card layout follows the attachment tiles of RuseBot 0.1.127
 * src/components/AttachmentGallery.tsx / AttachmentPreview.tsx (Apache-2.0,
 * Copyright 2026 Milind Soni and OpenMausBot contributors): rounded surface,
 * file name row, expandable preview. Review state and content come from the
 * Loqman worker (deliverables.*).
 */
import { useRef, useState } from 'react';
import { Check, ChevronDown, Download, FileText, X } from 'lucide-react';
import { cn } from '../lib/cn';
import type { Draft } from '../data/worker';
import { getDraft } from '../data/worker';
import { messageOf } from '../state/useChat';

const CONTROL =
  'inline-flex h-7 shrink-0 items-center justify-center gap-1.5 whitespace-nowrap rounded-full px-3 text-[13px] font-medium leading-5 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus disabled:opacity-60';

const STATUS_LABEL: Record<string, string> = {
  pending_review: 'Awaiting review',
  approved: 'Approved',
  rejected: 'Rejected',
};

export function DeliverableCard({
  draft,
  onReview,
  onDownload,
}: {
  draft: Draft;
  onReview: (draft: Draft, decision: 'approve' | 'reject') => Promise<string | void>;
  onDownload: (draft: Draft) => Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  const [content, setContent] = useState<string | null>(draft.content);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const previewRequest = useRef(0);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  const loadPreview = async () => {
    const ticket = ++previewRequest.current;
    setPreviewError(null);
    try {
      const full = await getDraft(draft.run_id, draft.id);
      if (ticket === previewRequest.current) setContent(full.content ?? '');
    } catch (e) {
      if (ticket === previewRequest.current) setPreviewError(messageOf(e));
    }
  };

  const toggle = () => {
    const next = !open;
    setOpen(next);
    if (!next) previewRequest.current++;
    else if (content === null) void loadPreview();
  };

  const run = async (label: 'review' | 'download', action: () => Promise<string | void>) => {
    setBusy(true);
    setActionError(null);
    try {
      const refreshError = await action();
      if (refreshError) setActionError('Review saved, but the deliverable list could not be refreshed. ' + refreshError);
    } catch (e) {
      setActionError('Could not ' + label + ' deliverable. ' + messageOf(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      data-deliverable={draft.id}
      data-deliverable-status={draft.status}
      className="w-full max-w-[840px] rounded-2xl border border-hairline/40 bg-card p-3"
    >
      <div className="flex items-center gap-2">
        <FileText size={16} className="shrink-0 text-ink-secondary" aria-hidden="true" />
        <button
          type="button"
          onClick={toggle}
          aria-expanded={open}
          aria-label={`Preview ${draft.filename}`}
          className="flex min-w-0 flex-1 items-center gap-2 rounded-md text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus"
        >
          <span dir="auto" className="min-w-0 truncate text-[14px] font-medium text-ink">
            {draft.filename}
          </span>
          <span
            className={cn(
              'shrink-0 rounded-full px-2 py-0.5 text-[11px] font-medium',
              draft.status === 'approved' && 'bg-success/15 text-success',
              draft.status === 'rejected' && 'bg-danger/10 text-danger',
              draft.status === 'pending_review' && 'deliverable-pending bg-warning/15 text-warning'
            )}
          >
            {STATUS_LABEL[draft.status] ?? draft.status}
          </span>
          <ChevronDown size={14} className={cn('shrink-0 text-ink-tertiary transition-transform', open && 'rotate-180')} aria-hidden="true" />
        </button>
        {draft.status === 'pending_review' && (
          <span className="flex shrink-0 items-center gap-2">
            <button
              type="button"
              disabled={busy}
            onClick={() => void run('review', () => onReview(draft, 'reject'))}
              className={cn(CONTROL, 'bg-control text-ink hover:bg-raised-hover')}
            >
              <X size={13} aria-hidden="true" /> Reject
            </button>
            <button
              type="button"
              disabled={busy}
            onClick={() => void run('review', () => onReview(draft, 'approve'))}
              className={cn(CONTROL, 'approval-confirm bg-accent text-accent-ink')}
            >
              <Check size={13} aria-hidden="true" /> Approve
            </button>
          </span>
        )}
        {draft.downloadable && (
          <button
            type="button"
            disabled={busy}
            onClick={() => void run('download', () => onDownload(draft))}
            className={cn(CONTROL, 'bg-control text-ink hover:bg-raised-hover')}
          >
            <Download size={13} aria-hidden="true" /> Download
          </button>
        )}
      </div>
      {actionError && <p role="alert" dir="auto" className="mt-2 text-[13px] text-danger">{actionError}</p>}
      {open && previewError && (
        <div className="mt-2 flex items-start gap-2 text-[13px] text-danger">
          <p role="alert" dir="auto">Could not load preview. {previewError}</p>
          <button type="button" className="shrink-0 font-medium underline" onClick={() => void loadPreview()}>Retry preview</button>
        </div>
      )}
      {open && !previewError && content === null && <p role="status" className="mt-2 text-[13px] text-ink-secondary">Loading preview…</p>}
      {open && content !== null && (
        <pre
          tabIndex={0}
          aria-label={`Contents of ${draft.filename}`}
          className="mt-2 max-h-64 overflow-auto whitespace-pre-wrap break-words rounded-lg bg-inset px-3 py-2 font-mono text-[12.5px] leading-relaxed text-ink"
        >
          {content}
        </pre>
      )}
    </div>
  );
}
