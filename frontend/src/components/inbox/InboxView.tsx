import React, { useState } from 'react';
import type { InboxItem, Agent } from '../../types/agentos';
import { CheckCheck, CheckCircle2, AlertCircle, ExternalLink, Eye, MessagesSquare } from 'lucide-react';

interface Props {
  inbox: InboxItem[];
  agents: Agent[];
  onApproveReview: (id: string) => void;
  onRequestChanges: (id: string) => void;
  onMarkNoticeRead: (id: string) => void;
  onApproveAllReviews: () => void;
  onMarkAllAsRead: () => void;
  onOpenTarget: (itemId: string, targetAgentId?: string) => void;
  onInspectDeliverable?: (item: InboxItem) => void;
}

export const InboxView: React.FC<Props> = ({
  inbox,
  agents,
  onApproveReview,
  onRequestChanges,
  onMarkNoticeRead,
  onApproveAllReviews,
  onMarkAllAsRead,
  onOpenTarget,
  onInspectDeliverable,
}) => {
  const [tab, setTab] = useState<'all' | 'review' | 'notice'>('all');

  const filteredItems = inbox.filter((item) => {
    if (tab === 'review') return item.kind === 'review';
    if (tab === 'notice') return item.kind === 'notice';
    return true;
  });

  return (
    <div className="flex flex-col gap-4 max-w-5xl">
      {/* Inbox Header */}
      <div className="flex items-end justify-between gap-3 flex-wrap">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-[var(--text)] m-0">Inbox</h1>
          <p className="text-xs text-[var(--muted)] m-0 mt-1">
            Reviews and notices from your teams. Opening an item starts no work.
          </p>
        </div>

        <div className="flex items-center gap-2 ml-auto flex-wrap">
          <button
            onClick={onMarkAllAsRead}
            disabled={inbox.length === 0}
            className="os-btn os-btn-ghost os-btn-sm disabled:opacity-40 disabled:cursor-not-allowed"
          >
            <CheckCheck size={14} />
            Mark all as read
          </button>
          <button
            onClick={onApproveAllReviews}
            disabled={!inbox.some((i) => i.kind === 'review' && i.st === 'pending')}
            className="os-btn os-btn-sm disabled:opacity-40 disabled:cursor-not-allowed"
          >
            <CheckCircle2 size={14} />
            Approve all reviews
          </button>
        </div>
      </div>

      {/* Filter Tabs */}
      <div className="flex items-center gap-1.5 border-b border-[var(--line)] pb-2">
        {(
          [
            ['all', 'All'],
            ['review', 'Reviews'],
            ['notice', 'Notices'],
          ] as const
        ).map(([k, label]) => {
          const isActive = tab === k;
          const count =
            k === 'all'
              ? inbox.length
              : inbox.filter((i) => i.kind === k).length;

          return (
            <button
              key={k}
              onClick={() => setTab(k)}
              className={`px-3 py-1.5 rounded-xl text-xs font-medium transition-all flex items-center gap-2 ${
                isActive
                  ? 'bg-[var(--acc)]/15 text-[var(--text)] border border-[var(--acc)]/30'
                  : 'text-[var(--muted)] hover:text-[var(--text)] hover:bg-white/5 border border-transparent'
              }`}
            >
              <span>{label}</span>
              <span className="mono text-[10px] px-1.5 py-0.5 bg-[var(--card2)] rounded-full text-[var(--dim)]">
                {count}
              </span>
            </button>
          );
        })}
      </div>

      {/* Items List */}
      <div className="flex flex-col gap-2.5">
        {filteredItems.map((item) => {
          if (item.kind === 'review') {
            const isPending = item.st === 'pending';
            const isApproved = item.st === 'approved';
            const tagCls = isApproved
              ? 'os-tag-green'
              : item.st === 'changes'
              ? 'os-tag-red'
              : 'os-tag-amber';
            const statusLabel = isApproved
              ? 'Approved'
              : item.st === 'changes'
              ? 'Changes requested'
              : 'Waiting for review';

            return (
              <div
                key={item.id}
                role="button"
                tabIndex={0}
                aria-label={`Inspect ${item.title}`}
                onClick={() => onInspectDeliverable?.(item)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault();
                    onInspectDeliverable?.(item);
                  }
                }}
                className="os-card flex items-start gap-3.5 p-4 hover:border-[var(--line-strong)] transition-all cursor-pointer group"
                title="Click to view task details and inter-agent dialogue"
              >
                <div className="w-2.5 h-2.5 rounded-full bg-[var(--amber)] mt-1.5 flex-none" />
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    <b className="text-sm font-semibold text-[var(--text)] group-hover:text-[var(--acc)] transition-colors">
                      {item.title}
                    </b>
                    <span className={`os-tag ${tagCls} text-[11px]`}>{statusLabel}</span>
                  </div>
                  <div className="text-xs text-[var(--muted)] mt-1 leading-relaxed">{item.sub}</div>
                  <div className="text-[11px] text-[var(--dim)] mt-2 mono">{item.time}</div>
                </div>

                <div className="flex items-center gap-2 ml-auto flex-none flex-wrap">
                  {onInspectDeliverable && (
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        onInspectDeliverable(item);
                      }}
                      className="os-btn os-btn-ghost os-btn-sm text-[var(--acc)] border-[var(--acc)]/30 hover:bg-[var(--acc)]/10"
                      title="Inspect deliverable and dialogue"
                    >
                      <MessagesSquare size={13} />
                      <span>Inspect</span>
                    </button>
                  )}

                  {isPending && (
                    <>
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          onApproveReview(item.id);
                        }}
                        className="os-btn os-btn-sm"
                      >
                        <CheckCircle2 size={13} />
                        Approve
                      </button>
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          onRequestChanges(item.id);
                        }}
                        className="os-btn os-btn-ghost os-btn-sm text-[var(--red)] border-[var(--red)]/30 hover:bg-[var(--red)]/10"
                      >
                        <AlertCircle size={13} />
                        Request changes
                      </button>
                    </>
                  )}
                </div>
              </div>
            );
          }

          // Notice Item
          const targetAgent = agents.find((a) => a.id === item.target);
          const isUnread = !item.read;

          return (
            <div
              key={item.id}
              className={`os-card flex items-start gap-3.5 p-4 transition-all ${
                isUnread ? 'border-[var(--acc)]/40 bg-[var(--acc)]/5' : 'hover:border-[var(--line-strong)]'
              }`}
            >
              <span
                className={`w-2.5 h-2.5 rounded-full mt-1.5 flex-none transition-all ${
                  isUnread
                    ? 'bg-[var(--acc)] shadow-[0_0_10px_var(--acc)] ring-2 ring-[var(--acc)]/40'
                    : 'bg-[var(--line2)]'
                }`}
              />

              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                  <b className="text-sm font-semibold text-[var(--text)]">{item.title}</b>
                  {isUnread && (
                    <span className="os-tag os-tag-mono text-[10px] py-0.5">Unread</span>
                  )}
                </div>
                <div className="text-xs text-[var(--muted)] mt-1 leading-relaxed">{item.sub}</div>
                <div className="text-[11px] text-[var(--dim)] mt-2 mono">
                  {item.time}
                  {targetAgent && ` · ${targetAgent.name}`}
                </div>
              </div>

              <div className="flex items-center gap-2 ml-auto flex-none">
                {targetAgent && (
                  <button
                    onClick={() => onOpenTarget(item.id, item.target)}
                    className="os-btn os-btn-ghost os-btn-sm"
                    title="Open employee or conversation"
                  >
                    <ExternalLink size={13} />
                    Open
                  </button>
                )}
                {isUnread && (
                  <button
                    onClick={() => onMarkNoticeRead(item.id)}
                    className="os-btn os-btn-ghost os-btn-sm"
                    title="Mark notice as read"
                  >
                    <Eye size={13} />
                    Mark read
                  </button>
                )}
              </div>
            </div>
          );
        })}

        {filteredItems.length === 0 && (
          <div className="os-card text-center py-12 text-[var(--muted)] text-xs">
            No items in this inbox folder.
          </div>
        )}
      </div>
    </div>
  );
};
