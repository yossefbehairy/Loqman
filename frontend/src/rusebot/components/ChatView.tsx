/*
 * Structure, classes and states follow RuseBot 0.1.127 src/components/ChatView.tsx,
 * ChatEmptyState.tsx, ChatErrorBanner.tsx and WorkingIndicator.tsx (Apache-2.0,
 * Copyright 2026 Milind Soni and OpenMausBot contributors): chat header pill,
 * centred transcript column, message bubbles, copy action, working dots and
 * the floating composer dock. Messages, runs and approvals arrive as props.
 */
import { useLayoutEffect, useMemo, useRef, useState } from 'react';
import { Check, Copy, Cpu, X } from 'lucide-react';
import { CEO_PROFILE_ID } from '../data/worker';
import { cn } from '../lib/cn';
import { userBubbleStyle } from '../lib/user-bubble-tone';
import { GlassBar, GlassScrollFrame } from './GlassScrollFrame';
import { ChatMarkdown } from './ChatMarkdown';
import { Composer } from './Composer';
import { ApprovalCard } from './ApprovalCard';
import { DeliverableCard } from './DeliverableCard';
import { stripContract, type ChatState } from '../state/useChat';
import type { EngineState } from '../state/useChatEngines';
import { hasLiveContent, type LiveTurn } from '../state/liveTurn';
import type { Agent } from '../types';
import { useKeepDecided } from '../state/useKeepDecided';
import type { Conversation, RunSnapshot } from '../data/worker';
import { parseWorkerTime } from '../data/time';
import type { LegacyThread } from '../data/legacy';

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

// Render only known worker summaries. Older or unexpected diagnostics may contain sensitive detail.
const PUBLIC_RUN_ERRORS = new Set([
  'Internal error.',
  'Output did not match the profile schema.',
  'Run interrupted by server restart or shutdown; it was not re-executed.',
  'Task ended without a verified tool result.',
  'Report was not published. Approval was denied or publication is incomplete.',
  'Task ended with unread file pages.',
  'A source file changed before completion.',
  ...[
    'auth', 'busy', 'cancelled', 'cli_missing', 'model_unavailable', 'offline', 'output_limit',
    'protocol_error', 'quota', 'rate_limited', 'request_refused', 'runtime_error', 'timeout',
    'unsupported_capability', 'upstream_unavailable',
  ].map((code) => `Engine request failed (${code}).`),
]);

function SavedRunOutcome({ run }: { run: RunSnapshot }) {
  if (run.status !== 'failed' && run.status !== 'interrupted') return null;
  const failed = run.status === 'failed';
  const detail = run.error && PUBLIC_RUN_ERRORS.has(run.error) ? run.error : null;
  return (
    <p
      data-saved-run-outcome={run.status}
      role="note"
      dir="auto"
      className={cn('mt-1 max-w-[min(var(--chat-bubble-max),var(--chat-bubble-pct))] px-4 text-[12.5px]', failed ? 'text-danger' : 'text-ink-secondary')}
    >
      <span className="font-medium">{failed ? 'Run failed' : 'Run interrupted'}</span>
      {detail && ` · ${detail}`}
    </p>
  );
}

function Bubble({ role, text, iso, savedRun }: { role: 'user' | 'assistant'; text: string; iso: string; savedRun?: RunSnapshot }) {
  const mine = role === 'user';
  const time = parseWorkerTime(iso).toLocaleString();
  const executedWith = !mine && savedRun?.engine && savedRun.model_id ? `${savedRun.engine} · ${savedRun.model_id}` : null;
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
          title={executedWith ? `${time} · ${executedWith}` : time}
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
      {mine && savedRun && <SavedRunOutcome run={savedRun} />}
    </div>
  );
}

/** The reply while it streams in: live text per item, tool steps as compact chips. The stored message replaces it when the turn ends. */
function LiveReply({ turn }: { turn: LiveTurn }) {
  const last = turn.items.length - 1;
  return (
    <div data-live-bubble="" data-stopped={turn.stopped ? 'true' : 'false'} className="flex w-full flex-col items-start">
      <div className="w-fit max-w-[min(var(--chat-bubble-max),var(--chat-bubble-pct))] rounded-[23px] bg-card px-4 py-2.5 text-[15px] leading-relaxed text-ink">
        {turn.items.map((item, i) => {
          if (item.kind === 'tool') {
            const state = item.status === 'running' ? 'running' : item.status === 'failed' ? 'failed' : 'done';
            return (
              <div key={item.id} data-live-tool="" data-status={state} className="my-1 flex items-center gap-1.5 text-[12.5px] text-ink-secondary">
                <Cpu size={12} aria-hidden="true" />
                <span className="font-medium">{item.title}</span>
                <span>{state === 'running' ? 'running' : state === 'failed' ? 'failed' : 'done'}</span>
              </div>
            );
          }
          if (!item.streamed || !item.text) return null;
          const commentary = item.done && item.phase === 'commentary';
          return (
            <div key={item.id} data-live-item="" data-phase={item.phase ?? 'streaming'} className={cn(commentary && 'text-ink-secondary opacity-70')}>
              <ChatMarkdown text={item.text} live={!item.done && !turn.stopped && i <= last} />
            </div>
          );
        })}
        {turn.stopped && (
          <div data-live-stopped="" className="mt-1 text-[12.5px] text-ink-tertiary">
            Stopped
          </div>
        )}
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

export function ChatErrorBanner({ message, onDismiss, onRetry, providerCheck }: {
  message: string | null;
  onDismiss: () => void;
  onRetry?: (() => void) | null;
  providerCheck: ChatState['retryProviderCheck'];
}) {
  if (!message) return null;
  return (
    <div className="w-full px-5">
      <div role="alert" className="mb-2 flex items-start gap-2 rounded-lg border border-danger/30 bg-danger/10 px-3 py-2 text-[13px] text-danger">
        <div className="min-w-0 flex-1 break-words">
          <p dir="auto">{message}</p>
          {providerCheck === 'checking' && <p role="status">Checking the original engine and model…</p>}
          {providerCheck === 'unavailable' && <p>The original engine and model are currently unavailable. Restore them in Settings → AI before retrying.</p>}
          {providerCheck === 'failed' && <p>Could not check the original engine and model. Retry to check again, or refresh providers in Settings → AI.</p>}
        </div>
        {onRetry && (
          <button type="button" data-chat-retry onClick={onRetry} disabled={providerCheck === 'checking'} className="shrink-0 rounded px-1.5 font-medium underline hover:bg-danger/10 disabled:opacity-60">
            Retry
          </button>
        )}
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
  agentDefaultsNotice,
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
  agentDefaultsNotice?: string;
  /** False until the conversation list and the agents are loaded, so the remembered chat is not shown as Loqman first. */
  ready?: boolean;
}) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const transcriptRef = useRef<HTMLDivElement>(null);
  const scrollState = useRef({ followTail: true, top: 0, maxScroll: 0 });
  const unknownRecipient = chat.selectedAgentId === null;
  const fallbackName = unknownRecipient ? 'Recipient unavailable' : chat.selectedAgentId === CEO_PROFILE_ID ? 'Loqman' : 'Specialist';
  const ASSISTANT_NAME = ready ? (agent?.name ?? fallbackName) : '';
  const running = Boolean(chat.run && (chat.run.status === 'queued' || chat.run.status === 'running'));
  const title = !ready ? '' : (conversation?.title ?? legacyThread?.title ?? (unknownRecipient && chat.selectedId ? 'Saved conversation' : 'New chat'));

  useLayoutEffect(() => {
    const viewport = scrollRef.current!;
    const transcript = transcriptRef.current!;
    // Each conversation opens at its latest message, including after its history loads.
    scrollState.current.followTail = true;
    const keepLatestInView = () => {
      if (!scrollState.current.followTail) return;
      const maxScroll = viewport.scrollHeight - viewport.clientHeight;
      viewport.scrollTop = maxScroll;
      scrollState.current = { followTail: true, top: viewport.scrollTop, maxScroll };
    };
    keepLatestInView();
    // Observe both the content (including streaming Markdown) and the space
    // available for it (including composer growth and window resizing).
    const observer = new ResizeObserver(keepLatestInView);
    observer.observe(viewport);
    observer.observe(transcript);
    return () => observer.disconnect();
  }, [chat.selectedId]);

  const choice = engines.choice;
  const chatModel = choice ? (engines.models.find((m) => m.id === choice.model)?.name ?? choice.model) : '';
  const savedModel = agent && agent.id !== CEO_PROFILE_ID ? agent.model : '';
  const overridden = engines.explicit;

  const live = hasLiveContent(chat.live) ? chat.live : null;

  const runApprovals = useMemo(
    () => chat.approvals.filter((a) => chat.run && a.run_id === chat.run.runId),
    [chat.approvals, chat.run]
  );
  const { shown, decide: decideApproval, retained } = useKeepDecided(runApprovals);
  const approvals = shown.filter(a => a.run_id === chat.run?.runId);

  const rows = chat.messages.map((m, i) => {
    const prev = chat.messages[i - 1];
    const showDay = !prev || parseWorkerTime(prev.created_at).toDateString() !== parseWorkerTime(m.created_at).toDateString();
    const run = chat.runsBySeq[m.seq];
    const replyRun = m.role === 'assistant' ? run : undefined;
    const drafts = replyRun ? (chat.drafts[replyRun.id] ?? []) : [];
    const save = replyRun ? chat.draftSaves[replyRun.id] : undefined;
    return (
      <div className="contents" key={m.seq} data-mid={m.seq}>
        {(i === 0 || showDay) && <div className="py-3 text-center text-[13px] text-ink-secondary">{dayLabel(m.created_at)}</div>}
        <Bubble role={m.role} text={stripContract(m.content)} iso={m.created_at} savedRun={run} />
        {replyRun && (
          <div className="flex w-full flex-col items-start gap-2" data-run-id={replyRun.id}>
            {drafts.map((d) => (
              <DeliverableCard key={d.id} draft={d} onReview={chat.reviewDraft} onDownload={chat.downloadDraft} />
            ))}
            {replyRun.status === 'succeeded' && drafts.length === 0 && (
              <button
                type="button"
                disabled={save?.pending}
                onClick={() => void chat.makeDeliverable(replyRun)}
                className="ui-chip text-[12.5px] text-ink-secondary hover:text-ink"
              >
                {save?.pending ? 'Saving deliverable…' : 'Save reply as deliverable for review'}
              </button>
            )}
            {save?.error && <p role="alert" className="text-[13px] text-danger">Could not save deliverable. {save.error}</p>}
          </div>
        )}
      </div>
    );
  });

  return (
    <main className="relative flex h-full min-w-0 flex-1 flex-col bg-app">
      <GlassScrollFrame className="flex flex-1 flex-col [--glass-tint:var(--color-app)]">
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
                  title={agent ? `${ASSISTANT_NAME} in the team` : 'Open team'}
                  aria-label={agent ? `Open ${ASSISTANT_NAME}'s team` : 'Open team'}
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
                  {choice && (
                    <span
                      data-chathead-model
                      data-model-override={overridden ? 'true' : 'false'}
                      title={
                        overridden
                          ? `Next message model: ${choice.model || 'waiting for catalog'}. Chat override.${savedModel ? ` ${ASSISTANT_NAME} is saved with ${savedModel}.` : ''}`
                          : `Next message model: ${choice.model || 'waiting for catalog'}. Inherited default.`
                      }
                      className="hidden min-w-0 max-w-[180px] items-center gap-1 truncate whitespace-nowrap text-[11px] text-ink-secondary @4xl/chathead:flex"
                    >
                      <span className="truncate">{chatModel}</span>
                      {overridden && <span className="shrink-0 rounded-full bg-warning/15 px-1.5 text-[10px] font-medium text-warning">override</span>}
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

        <div
          key={chat.selectedId ?? 'new-chat'}
          ref={scrollRef}
          onScroll={(e) => {
            const el = e.currentTarget;
            const { scrollTop: top, scrollHeight, clientHeight } = el;
            const maxScroll = scrollHeight - clientHeight;
            const previous = scrollState.current;
            // A resize or content update can move the browser's scroll anchor
            // before ResizeObserver runs. Keep following through that reflow.
            if (top === previous.top || (previous.followTail && maxScroll !== previous.maxScroll)) return;
            scrollState.current = { top, maxScroll, followTail: maxScroll - top <= 2 };
          }}
          className="glass-scroller min-h-0 flex-1 overflow-x-hidden overflow-y-auto overscroll-y-contain px-5">
          <div
            ref={transcriptRef}
            className="chat-column relative flex w-full flex-col gap-3 pt-[var(--glass-top,0px)] pb-3"
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
                {chat.threadError && (
                  <div role="alert" data-thread-error className="flex flex-col items-start gap-2 rounded-lg border border-danger/30 bg-danger/10 px-3 py-3 text-[13px] text-danger">
                    <p dir="auto" className="break-words">
                      This chat could not be loaded completely. {chat.threadError}
                    </p>
                    <button type="button" data-thread-retry onClick={chat.retryThread} className="ui-button ui-button-sm">
                      Retry
                    </button>
                  </div>
                )}
                {chat.threadLoading && (
                  <p role="status" className="py-3 text-center text-[13px] text-ink-secondary">Loading conversation…</p>
                )}
                {ready && !chat.threadLoading && !chat.threadError && chat.messages.length === 0 && !chat.pendingUser && !running && !live && (
                  <EmptyChat name={ASSISTANT_NAME} role={agent?.role} description={unknownRecipient
                    ? (chat.draftRecipientUnverified ? 'This Agent is missing from the Agent list.' : 'Sending is paused until the chat list includes this conversation.')
                    : 'Send a message to start the conversation.'} />
                )}
                {rows}
                {chat.pendingUser && !chat.messages.some((m) => m.role === 'user' && m.content === chat.pendingUser) && (
                  <Bubble role="user" text={chat.pendingUser} iso={new Date().toISOString()} />
                )}
                {approvals.map((a) => {
                  const entry = retained[a.id];
                  const status = entry?.item.status ?? a.status;
                  const confirmed = status === 'approved' ? 'approve' : status === 'denied' ? 'deny' : undefined;
                  return (
                    <ApprovalCard
                      key={a.id}
                      kind="tool"
                      title={`${ASSISTANT_NAME} wants permission`}
                      detail={a.action_summary}
                      scope={a.scope}
                      confirmedDecision={confirmed}
                      decisionPending={Boolean(entry && !confirmed)}
                      onDecide={(decision) => decideApproval(a, (confirm) => chat.decide(a, decision, {
                        conversationId: chat.selectedId,
                        confirm,
                      }))}
                    />
                  );
                })}
                {live && <LiveReply turn={live} />}
                {running && !live && (
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

        <div className="chat-column-dock relative z-[2] w-full shrink-0">
          <div className="pointer-events-none relative px-5 pb-3">
            <div className="pointer-events-auto">
              <ChatErrorBanner message={chat.error} onDismiss={chat.dismissError} onRetry={chat.retry} providerCheck={chat.retryProviderCheck} />
            </div>
            <div className="pointer-events-auto relative w-full">
              <div className="relative">
                <Composer
                  placeholder={!ready ? 'Loading…' : unknownRecipient ? 'Recipient unavailable' : `Message ${ASSISTANT_NAME}`}
                  value={chat.draft}
                  onChange={chat.setDraft}
                  onSend={chat.send}
                  onStop={chat.stop}
                  canStop={chat.canStop}
                  stopping={chat.stopping}
                  running={running}
                  sending={chat.sending}
                  disabled={engines.loading || !ready || unknownRecipient || chat.draftRecipientUnverified}
                  engines={engines}
                  onNewAgent={onNewAgent}
                  onManageProviders={onManageProviders}
                  agentDefaultsNotice={agentDefaultsNotice}
                />
              </div>
            </div>
          </div>
        </div>
      </GlassScrollFrame>
    </main>
  );
}
