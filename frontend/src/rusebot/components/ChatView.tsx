/*
 * Structure, classes and states follow RuseBot 0.1.127 src/components/ChatView.tsx,
 * ChatEmptyState.tsx, ChatErrorBanner.tsx and WorkingIndicator.tsx (Apache-2.0,
 * Copyright 2026 Milind Soni and OpenMausBot contributors): chat header pill,
 * centred transcript column, message bubbles, copy action, working dots and
 * the floating composer dock. Messages, runs and approvals arrive as props.
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import { Check, Copy, Cpu, X } from 'lucide-react';
import { cn } from '../lib/cn';
import { useComposerDockPad } from '../lib/composer-dock';
import { userBubbleStyle } from '../lib/user-bubble-tone';
import { GlassBar, GlassScrollFrame } from './GlassScrollFrame';
import { ChatMarkdown } from './ChatMarkdown';
import { Composer } from './Composer';
import { ApprovalCard } from './ApprovalCard';
import { DeliverableCard } from './DeliverableCard';
import { stripContract, type ChatState, type EngineState } from '../state/useChat';
import type { Agent } from '../types';
import { useKeepDecided } from '../state/useKeepDecided';
import type { Conversation } from '../data/worker';
import { parseWorkerTime } from '../data/time';
import type { LegacyThread } from '../data/legacy';

const FALLBACK_NAME = 'Loqman';

function WorkingDots({ size = 4, className }: { size?: number; className?: string }) {
  return (
    <span className={cn('flex items-center gap-1', className)} aria-hidden="true">
      {[0, 200, 400].map((delay) => (
        <span
          key={delay}
          className="animate-status-pulse rounded-full bg-current"
          style={{ width: size, height: size, animationDelay: `${delay}ms` }}
        />
      ))}
    </span>
  );
}

function CopyButton({ text, className }: { text: string; className?: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      aria-label="Copy message"
      title="Copy message"
      onClick={() => {
        navigator.clipboard
          .writeText(text)
          .then(() => {
            setCopied(true);
            window.setTimeout(() => setCopied(false), 1400);
          })
          .catch(() => undefined);
      }}
      className={cn(
        'p-1.5 opacity-0 focus-visible:opacity-100 group-hover:opacity-100 group-focus-within:opacity-100 touch:opacity-100',
        'inline-flex size-7 items-center justify-center rounded-full text-ink-tertiary transition-colors hover:bg-ink/10 hover:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus',
        className
      )}
    >
      {copied ? <Check size={14} /> : <Copy size={14} />}
    </button>
  );
}

function clock(iso: string): string {
  const d = parseWorkerTime(iso);
  return Number.isNaN(d.getTime()) ? '' : d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
}

function dayLabel(iso: string): string {
  const d = parseWorkerTime(iso);
  if (Number.isNaN(d.getTime())) return '';
  const when = clock(iso);
  return d.toDateString() === new Date().toDateString()
    ? `Today ${when}`
    : `${d.toLocaleDateString([], { month: 'short', day: 'numeric' })} ${when}`;
}

const USER_BUBBLE_STYLE = userBubbleStyle('chrome');

function Bubble({ role, text, iso }: { role: 'user' | 'assistant'; text: string; iso: string }) {
  const mine = role === 'user';
  return (
    <div data-message-row="" data-role={role} className={cn('group flex w-full flex-col', mine ? 'animate-msg-in items-end' : 'items-start')}>
      <div className={cn('flex w-full items-center gap-1.5', mine ? 'justify-end' : 'justify-start')}>
        {mine && (
          <div className="relative flex flex-row-reverse items-center gap-0.5 self-end pb-0.5" data-testid="message-actions">
            <CopyButton text={text} />
          </div>
        )}
        <div
          data-chat-bubble="true"
          className={cn(
            'w-fit max-w-[min(var(--chat-bubble-max),var(--chat-bubble-pct))] text-[15px] leading-relaxed rounded-[23px] px-4 py-2.5 font-normal',
            mine ? 'bg-bubble-user whitespace-pre-wrap text-ink user-bubble-colored antialiased' : 'bg-card text-ink'
          )}
          style={mine ? USER_BUBBLE_STYLE : undefined}
          title={parseWorkerTime(iso).toLocaleString()}
        >
          {mine ? <div className="chat-text">{text}</div> : <ChatMarkdown text={text} />}
        </div>
        {!mine && (
          <div className="relative flex items-center gap-0.5 self-end pb-0.5" data-testid="message-actions">
            <CopyButton text={text} />
          </div>
        )}
        <span
          className={cn(
            'self-end pb-1 text-[11px] tabular-nums text-ink-tertiary opacity-0 transition-opacity group-hover:opacity-100',
            mine ? 'order-first mr-2' : 'ml-2'
          )}
        >
          {clock(iso)}
        </span>
      </div>
    </div>
  );
}

function EmptyChat({ name, role, description }: { name: string; role?: string; description: string }) {
  return (
    <div className="flex flex-1 flex-col items-center justify-center gap-3 py-24 text-center">
      <div className="flex size-16 items-center justify-center rounded-full bg-raised text-[26px] font-semibold text-ink-secondary">
        {name.charAt(0)}
      </div>
      <div dir="auto" className="text-[17px] font-semibold text-ink">{name}</div>
      {role && <div dir="auto" className="-mt-2 text-[12.5px] text-ink-secondary">{role}</div>}
      <div className="max-w-[360px] text-[14px] text-ink-secondary">{description}</div>
    </div>
  );
}

export function ChatErrorBanner({ message, onDismiss }: { message: string | null; onDismiss: () => void }) {
  if (!message) return null;
  return (
    <div className="w-full px-5">
      <div role="alert" className="mb-2 flex items-start gap-2 rounded-lg border border-danger/30 bg-danger/10 px-3 py-2 text-[13px] text-danger">
        <div className="min-w-0 flex-1 break-words">
          <p dir="auto">{message}</p>
        </div>
        <button type="button" onClick={onDismiss} aria-label="Dismiss" title="Dismiss" className="shrink-0 rounded p-0.5 hover:bg-danger/10">
          <X size={13} />
        </button>
      </div>
    </div>
  );
}

export function ChatView({
  chat,
  engines,
  conversation,
  legacyThread,
  agent,
  onOpenProfile,
  onNewAgent,
  onManageProviders,
  ready = true,
}: {
  chat: ChatState;
  engines: EngineState;
  conversation: Conversation | null;
  legacyThread: LegacyThread | null;
  agent: Agent | null;
  onOpenProfile: () => void;
  onNewAgent: () => void;
  onManageProviders?: () => void;
  /** False until the conversation list and the agents are loaded, so the remembered chat is not shown as Loqman first. */
  ready?: boolean;
}) {
  const dockRef = useRef<HTMLDivElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const dock = useComposerDockPad(dockRef);
  const ASSISTANT_NAME = ready ? (agent?.name ?? FALLBACK_NAME) : '';
  const running = Boolean(chat.run && (chat.run.status === 'queued' || chat.run.status === 'running'));
  const title = !ready ? '' : (conversation?.title ?? legacyThread?.title ?? 'New chat');

  const lastKey = `${chat.messages.length}:${chat.pendingUser ?? ''}:${running}:${legacyThread?.turns.length ?? 0}`;
  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [lastKey, chat.selectedId]);

  const runApprovals = useMemo(
    () => chat.approvals.filter((a) => chat.run && a.run_id === chat.run.runId),
    [chat.approvals, chat.run]
  );
  const { shown: approvals, decide: decideApproval } = useKeepDecided(runApprovals);

  const rows = chat.messages.map((m, i) => {
    const prev = chat.messages[i - 1];
    const showDay = !prev || parseWorkerTime(prev.created_at).toDateString() !== parseWorkerTime(m.created_at).toDateString();
    const run = m.role === 'assistant' ? chat.runsBySeq[m.seq] : undefined;
    const drafts = run ? (chat.drafts[run.id] ?? []) : [];
    return (
      <div className="contents" key={m.seq} data-mid={m.seq}>
        {(i === 0 || showDay) && <div className="py-3 text-center text-[13px] text-ink-secondary">{dayLabel(m.created_at)}</div>}
        <Bubble role={m.role} text={stripContract(m.content)} iso={m.created_at} />
        {run && (
          <div className="flex w-full flex-col items-start gap-2" data-run-id={run.id}>
            {drafts.map((d) => (
              <DeliverableCard key={d.id} draft={d} onReview={chat.reviewDraft} onDownload={chat.downloadDraft} />
            ))}
            {run.status === 'succeeded' && drafts.length === 0 && (
              <button
                type="button"
                onClick={() => void chat.makeDeliverable(run.id)}
                className="ui-chip text-[12.5px] text-ink-secondary hover:text-ink"
              >
                Save reply as deliverable for review
              </button>
            )}
          </div>
        )}
      </div>
    );
  });

  return (
    <main className="relative flex h-full min-w-0 flex-1 flex-col bg-app">
      <GlassScrollFrame className="flex-1 [--glass-tint:var(--color-app)]">
        <GlassBar edge="top" className="z-[25]">
          <div className="@container/chathead px-5 py-3 pl-11 md:pl-5">
            <div
              data-chathead-row="true"
              className="flex items-center justify-between @max-[30rem]/chathead:flex-wrap @max-[30rem]/chathead:gap-y-1 @min-[30rem]/chathead:grid @min-[30rem]/chathead:grid-cols-[minmax(0,1fr)_minmax(0,auto)_minmax(max-content,1fr)]"
            >
              <div
                data-chathead-identity="true"
                className="flex min-w-0 items-center gap-2 @max-[30rem]/chathead:basis-full @min-[30rem]/chathead:col-start-2 @min-[30rem]/chathead:justify-self-center"
              >
                <button
                  type="button"
                  data-chathead-pill="true"
                  data-tour="bot-pill"
                  title={`${ASSISTANT_NAME} in the team`}
                  aria-label={`Open ${ASSISTANT_NAME}'s team`}
                  onClick={onOpenProfile}
                  className="group flex h-9 min-w-0 items-center gap-2.5 rounded-full border border-hairline/40 bg-control/60 py-0.5 pl-2 pr-3.5 text-ink hover:bg-raised-hover"
                >
                  <span className="flex size-6 shrink-0 items-center justify-center rounded-full bg-raised text-[12px] font-semibold text-ink-secondary">
                    {ASSISTANT_NAME.charAt(0)}
                  </span>
                  <span className="min-w-0 truncate text-[14px] font-medium text-ink">{ASSISTANT_NAME}</span>
                  {engines.choice && (
                    <span
                      title={`${engines.choice.engine} · ${engines.choice.model}`}
                      className="flex shrink-0 items-center gap-1 whitespace-nowrap rounded-full bg-accent/12 px-2 py-0.5 text-[11px] font-medium text-accent @max-4xl/chathead:px-1.5"
                    >
                      <Cpu size={11} aria-hidden="true" />
                      <span className="@max-4xl/chathead:sr-only">
                        {engines.engines.find((e) => e.id === engines.choice?.engine)?.title ?? engines.choice.engine}
                      </span>
                    </span>
                  )}
                </button>
                <span aria-hidden="true" className="shrink-0 text-[14px] text-ink-tertiary">
                  /
                </span>
                <span dir="auto" data-chathead-conversation="true" className="min-w-0 max-w-[260px] truncate px-1 text-[13.5px] text-ink-secondary">
                  {title}
                </span>
              </div>
              <div data-chathead-controls="true" className="flex shrink-0 items-center gap-2 @min-[30rem]/chathead:col-start-3 @min-[30rem]/chathead:justify-self-end" />
            </div>
          </div>
        </GlassBar>

        <div ref={scrollRef} className="glass-scroller h-full overflow-x-hidden overflow-y-auto overscroll-y-contain px-5 [overflow-anchor:none]">
          <div
            className="glass-scroller-content chat-column relative flex w-full flex-col gap-3"
            style={{ paddingBottom: dock.pad }}
            role="log"
            aria-live="off"
            aria-label={`Conversation with ${ASSISTANT_NAME}`}
          >
            {legacyThread ? (
              legacyThread.turns.map((t, i) => (
                <div className="contents" key={i}>
                  <Bubble role="user" text={t.question} iso={new Date(t.ts || Date.now()).toISOString()} />
                  {t.reply && <Bubble role="assistant" text={t.reply} iso={new Date(t.ts || Date.now()).toISOString()} />}
                  {t.error && (
                    <div role="alert" className="rounded-lg border border-danger/30 bg-danger/10 px-3 py-2 text-[13px] text-danger">
                      {t.error}
                    </div>
                  )}
                </div>
              ))
            ) : (
              <>
                {ready && chat.messages.length === 0 && !chat.pendingUser && !running && (
                  <EmptyChat name={ASSISTANT_NAME} role={agent?.role} description="Send a message to start the conversation." />
                )}
                {rows}
                {chat.pendingUser && !chat.messages.some((m) => m.role === 'user' && m.content === chat.pendingUser) && (
                  <Bubble role="user" text={chat.pendingUser} iso={new Date().toISOString()} />
                )}
                {approvals.map((a) => (
                  <ApprovalCard
                    key={a.id}
                    kind="tool"
                    title={`${ASSISTANT_NAME} wants permission`}
                    detail={a.action_summary}
                    scope={a.scope}
                    onDecide={(decision) => decideApproval(a, () => chat.decide(a, decision))}
                  />
                ))}
                {running && (
                  <div data-working className="flex items-center gap-2 px-1 text-[13px] text-ink-secondary" role="status">
                    <WorkingDots size={5} className="text-ink-tertiary" />
                    <span>{chat.run?.status === 'queued' ? 'Queued' : (chat.progress ?? 'Working')}</span>
                  </div>
                )}
              </>
            )}
          </div>
        </div>
        <p role="status" aria-live="polite" aria-atomic="true" className="sr-only" data-testid="transcript-announcer">
          {running ? 'Working' : ''}
        </p>

        <div ref={dockRef} className="pointer-events-none absolute inset-x-0 bottom-0 z-[2] chat-column-dock">
          <div className="pointer-events-none relative px-5 pb-3">
            <div className="pointer-events-auto">
              <ChatErrorBanner message={chat.error} onDismiss={chat.dismissError} />
            </div>
            <div className="pointer-events-auto relative w-full">
              <div className="relative">
                <Composer
                  placeholder={ready ? `Message ${ASSISTANT_NAME}` : 'Loading…'}
                  value={chat.draft}
                  onChange={chat.setDraft}
                  onSend={chat.send}
                  onStop={chat.stop}
                  running={running}
                  disabled={engines.loading || !ready}
                  engines={engines}
                  onNewAgent={onNewAgent}
                  onManageProviders={onManageProviders}
                />
              </div>
            </div>
          </div>
        </div>
      </GlassScrollFrame>
    </main>
  );
}
