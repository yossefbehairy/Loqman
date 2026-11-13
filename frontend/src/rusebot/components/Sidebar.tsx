/*
 * Layout, classes and states follow RuseBot 0.1.127 src/components/Sidebar.tsx,
 * SidebarSimpleTop.tsx, SidebarFooter.tsx and SidebarFooterNav.tsx (Apache-2.0,
 * Copyright 2026 Milind Soni and OpenMausBot contributors). Data comes from
 * props; the RuseBot store is not used.
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import { ChevronLeft, ChevronRight, FileText, Loader2, MessageSquarePlus, PackageCheck, Plus, Search, Settings, UserPlus, UsersRound, X } from 'lucide-react';
import { cn } from '../lib/cn';
import { GlassBar, GlassScrollFrame, GlassScroller } from './GlassScrollFrame';
import type { Conversation } from '../data/worker';
import type { LegacyThread } from '../data/legacy';

export type View = 'chat' | 'team' | 'contracts' | 'results';

const SIMPLE_TOP_BUTTON =
  'flex size-[30px] shrink-0 items-center justify-center rounded-full bg-raised/50 text-ink hover:bg-raised focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus';

function stamp(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  const today = new Date();
  return d.toDateString() === today.toDateString()
    ? d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })
    : d.toLocaleDateString([], { month: 'short', day: 'numeric' });
}

function NavRow({
  id,
  label,
  active,
  badge,
  onClick,
  children,
}: {
  id: string;
  label: string;
  active: boolean;
  badge?: number;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      data-sidebar-nav={id}
      onClick={onClick}
      aria-label={label}
      title={label}
      aria-current={active ? 'page' : undefined}
      className={cn(
        'relative flex size-9 shrink-0 items-center justify-center rounded-xl transition-colors',
        active ? 'bg-raised text-accent' : 'text-ink hover:bg-raised/50'
      )}
    >
      {children}
      {badge ? (
        <span className="absolute -right-0.5 -top-0.5 flex min-w-4 items-center justify-center rounded-full bg-danger px-0.5 text-[9.5px] font-semibold leading-4 text-danger-ink">
          {badge > 9 ? '9+' : badge}
        </span>
      ) : null}
    </button>
  );
}

export interface SidebarProps {
  open: boolean;
  onClose: () => void;
  rail: boolean;
  onToggleRail: () => void;
  view: View;
  onView: (view: View) => void;
  conversations: Conversation[];
  legacy: LegacyThread[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  onNewChat: () => void;
  onNewAgent: () => void;
  onSettings: () => void;
  activeRunIds: Record<string, string>;
  needsYou: number;
  onNeedsYou: () => void;
  userName: string;
  previews: Record<string, string>;
}

export function Sidebar(props: SidebarProps) {
  const { rail, view } = props;
  const [searchOpen, setSearchOpen] = useState(false);
  const [query, setQuery] = useState('');
  const asideRef = useRef<HTMLElement>(null);
  const [plusOpen, setPlusOpen] = useState(false);
  const plusRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!plusOpen) return;
    const close = (e: MouseEvent) => {
      if (!plusRef.current?.contains(e.target as Node)) setPlusOpen(false);
    };
    const esc = (e: KeyboardEvent) => e.key === 'Escape' && setPlusOpen(false);
    document.addEventListener('mousedown', close);
    document.addEventListener('keydown', esc);
    return () => {
      document.removeEventListener('mousedown', close);
      document.removeEventListener('keydown', esc);
    };
  }, [plusOpen]);
  const q = query.trim().toLowerCase();

  useEffect(() => {
    if (!searchOpen) setQuery('');
  }, [searchOpen]);

  const rows = useMemo(
    () => props.conversations.filter((c) => !q || c.title.toLowerCase().includes(q)),
    [props.conversations, q]
  );
  const legacyRows = useMemo(
    () => props.legacy.filter((t) => !q || t.title.toLowerCase().includes(q)),
    [props.legacy, q]
  );

  const row = (id: string, title: string, when: string, preview: string, working: boolean) => {
    const current = view === 'chat' && props.selectedId === id;
    if (rail) {
      return (
        <button
          key={id}
          type="button"
          data-sidebar-thread-row={id}
          title={title}
          aria-label={title}
          aria-current={current ? 'page' : undefined}
          onClick={() => props.onSelect(id)}
          className={cn(
            'mx-auto flex size-[46px] shrink-0 items-center justify-center rounded-xl text-[15px] font-medium',
            current ? 'bg-raised text-ink' : 'text-ink-secondary hover:bg-raised/50 hover:text-ink'
          )}
        >
          {title.trim().charAt(0).toUpperCase() || '?'}
        </button>
      );
    }
    return (
      <div
        key={id}
        className={cn(
          'group/thread relative flex min-w-0 items-center rounded-xl',
          current ? 'bg-raised' : 'hover:bg-raised/50'
        )}
      >
        <button
          type="button"
          data-sidebar-thread-row={id}
          aria-current={current ? 'page' : undefined}
          title={title}
          onClick={() => props.onSelect(id)}
          className="flex min-h-[46px] min-w-0 flex-1 items-center gap-3 rounded-xl px-3 py-2 text-left outline-none focus-visible:ring-1 focus-visible:ring-accent/60"
        >
          <span className="flex size-8 shrink-0 items-center justify-center rounded-full bg-raised text-[13px] font-medium text-ink-secondary">
            {title.trim().charAt(0).toUpperCase() || '?'}
          </span>
          <span className="flex min-w-0 flex-1 flex-col">
            <span className="flex items-baseline gap-2">
              <span dir="auto" className={cn('min-w-0 flex-1 truncate text-[14px] font-medium leading-5', current ? 'text-ink' : 'text-ink')}>
                {title}
              </span>
              {when && <time className="shrink-0 tabular-nums text-[10px] text-ink-secondary">{when}</time>}
            </span>
            <span className="flex items-center gap-1">
              <span dir="auto" className="min-w-0 flex-1 truncate text-[12.5px] leading-4 text-ink-secondary">
                {preview}
              </span>
              {working && <Loader2 size={11} className="shrink-0 animate-spin text-success" aria-label="Working" />}
            </span>
          </span>
        </button>
      </div>
    );
  };

  return (
    <aside
      ref={asideRef}
      tabIndex={-1}
      aria-label="Chats and navigation"
      data-sidebar
      className={cn(
        'relative flex h-full shrink-0 flex-col border-r border-hairline/40 bg-panel',
        'transition-[width] duration-200',
        rail ? 'w-[92px]' : 'w-[288px] md:w-[var(--sidebar-width,288px)]',
        'md:max-w-[calc(100vw-320px)]',
        'max-md:absolute max-md:inset-y-0 max-md:left-0 max-md:z-40',
        'max-md:transition-transform max-md:duration-200',
        props.open ? 'max-md:translate-x-0' : 'max-md:-translate-x-full'
      )}
    >
      <GlassScrollFrame className="flex-1">
        <GlassBar edge="top">
          <div
            data-sidebar-top-row
            className={cn('flex items-center', rail ? 'flex-col gap-1 px-2 pt-3.5 pb-1' : 'relative h-12 pl-4 pr-2')}
          >
            {!rail && <div data-sidebar-top-slot className="@container/sidebar-top flex min-w-0 flex-1 items-center justify-end" />}
            <div data-sidebar-top-buttons className={cn('relative flex shrink-0 items-center', rail ? 'flex-col gap-1' : 'gap-1 pl-0.5')}>
              <button
                type="button"
                data-sidebar-search-toggle
                onClick={() => setSearchOpen((o) => !o)}
                aria-label="Search chats"
                aria-expanded={searchOpen}
                title="Search chats"
                className={cn(SIMPLE_TOP_BUTTON, searchOpen && 'bg-raised')}
              >
                <Search size={15} strokeWidth={2} aria-hidden="true" />
              </button>
              <div className="relative" ref={plusRef}>
                <button
                  type="button"
                  data-sidebar-new
                  onClick={() => setPlusOpen((o) => !o)}
                  aria-haspopup="menu"
                  aria-expanded={plusOpen}
                  aria-label="New"
                  title="New chat or agent"
                  className={cn(SIMPLE_TOP_BUTTON, plusOpen && 'bg-raised')}
                >
                  <Plus size={16} strokeWidth={2} aria-hidden="true" />
                </button>
                {plusOpen && (
                  <div
                    role="menu"
                    aria-label="Create"
                    className="absolute right-0 top-full z-50 mt-1.5 w-[200px] rounded-xl border border-hairline/50 bg-menu p-1.5 shadow-2xl shadow-black/30"
                  >
                    <button
                      type="button"
                      role="menuitem"
                      data-new-chat
                      onClick={() => {
                        setPlusOpen(false);
                        props.onNewChat();
                      }}
                      className="flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-left text-[13.5px] text-ink hover:bg-raised/70"
                    >
                      <MessageSquarePlus size={15} aria-hidden="true" /> New chat
                    </button>
                    <button
                      type="button"
                      role="menuitem"
                      data-new-agent
                      onClick={() => {
                        setPlusOpen(false);
                        props.onNewAgent();
                      }}
                      className="flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-left text-[13.5px] text-ink hover:bg-raised/70"
                    >
                      <UserPlus size={15} aria-hidden="true" /> New agent
                    </button>
                  </div>
                )}
              </div>
            </div>
          </div>
          {searchOpen && !rail && (
            <div data-sidebar-search-pill className="px-3 pb-2 pt-0.5">
              <div className="flex h-[34px] items-center gap-2 rounded-full bg-raised/60 pl-3 pr-1.5 text-ink-secondary focus-within:ring-1 focus-within:ring-focus">
                <Search size={15} aria-hidden="true" className="shrink-0" />
                <input
                  autoFocus
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Escape') {
                      e.preventDefault();
                      setSearchOpen(false);
                    }
                  }}
                  placeholder="Search chats"
                  aria-label="Search chats"
                  className="min-w-0 flex-1 bg-transparent text-[13px] text-ink placeholder:text-ink-tertiary focus:outline-none"
                />
                <button
                  type="button"
                  onClick={() => setSearchOpen(false)}
                  aria-label="Close search"
                  title="Close search"
                  className="flex size-6 shrink-0 items-center justify-center rounded-full text-ink-tertiary hover:bg-raised hover:text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-focus"
                >
                  <X size={14} aria-hidden="true" />
                </button>
              </div>
            </div>
          )}
        </GlassBar>

        <button
          type="button"
          data-sidebar-edge-toggle
          onClick={props.onToggleRail}
          aria-label={rail ? 'Expand sidebar' : 'Collapse sidebar'}
          aria-expanded={!rail}
          title={rail ? 'Expand sidebar' : 'Collapse sidebar'}
          className="absolute -right-[14px] top-[7px] z-30 hidden h-8 w-[14px] items-center justify-center rounded-r-[4px] border border-l-0 border-hairline/50 bg-panel text-ink-tertiary shadow-[1px_0_2px_rgb(0_0_0/0.06)] hover:bg-raised hover:text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus md:flex"
        >
          {rail ? <ChevronRight size={12} strokeWidth={2.25} aria-hidden="true" /> : <ChevronLeft size={12} strokeWidth={2.25} aria-hidden="true" />}
        </button>

        <GlassScroller className={rail ? '[scrollbar-gutter:stable_both-edges]' : 'px-2'}>
          <div className="flex flex-col gap-1 pt-2">
            {props.needsYou > 0 && (
              <button
                type="button"
                data-needs-you
                onClick={props.onNeedsYou}
                title="Needs you"
                aria-label={`Needs you: ${props.needsYou}`}
                className={cn(
                  'flex items-center rounded-xl text-ink transition-colors hover:bg-raised/50',
                  rail ? 'mx-auto size-[46px] justify-center' : 'min-h-9 w-full gap-3 px-3 py-1.5 text-left'
                )}
              >
                <span className="flex size-6 shrink-0 items-center justify-center rounded-full bg-warning text-[12px] font-semibold text-ink">
                  {props.needsYou}
                </span>
                {!rail && <span className="flex-1 truncate text-[14px] font-medium leading-5">Needs you</span>}
              </button>
            )}
            {rows.map((c) =>
              row(
                c.id,
                c.title,
                stamp(c.updated_at),
                props.previews[c.id] ?? '',
                Boolean(props.activeRunIds[c.id])
              )
            )}
            {legacyRows.length > 0 && !rail && (
              <div className="px-2 pb-1 pt-3 text-[11px] font-medium uppercase tracking-[0.08em] text-ink-secondary">
                Earlier chats
              </div>
            )}
            {legacyRows.map((t) =>
              row(
                `legacy:${t.id}`,
                t.title,
                t.ts ? stamp(new Date(t.ts).toISOString()) : '',
                t.turns[t.turns.length - 1]?.reply.slice(0, 80) ?? '',
                false
              )
            )}
            {rows.length === 0 && legacyRows.length === 0 && q && (
              <div className="px-3 py-6 text-center text-[13px] text-ink-secondary">No chats match “{query}”.</div>
            )}
          </div>
        </GlassScroller>

        <GlassBar edge="bottom">
          <div className={cn('pb-3 pt-2', rail ? 'flex flex-col items-center gap-1 px-2' : 'px-3')}>
            <div className={cn('flex gap-1', rail ? 'flex-col items-center' : 'items-center not-first:mt-3')}>
              <div className={rail ? '' : 'min-w-0 flex-1'}>
                <button
                  type="button"
                  aria-haspopup="dialog"
                  aria-label="Settings"
                  onClick={props.onSettings}
                  className={rail ? 'flex size-[46px] items-center justify-center rounded-xl hover:bg-raised/50' : 'w-full'}
                >
                  {rail ? (
                    <Settings size={18} className="text-ink-secondary" />
                  ) : (
                    <span className="flex min-h-[46px] w-full items-center gap-3 rounded-xl px-3 py-2 text-left transition-colors hover:bg-raised/50">
                      <span className="flex size-8 shrink-0 items-center justify-center rounded-full bg-raised text-[13px] font-medium text-ink-secondary">
                        {props.userName.trim().charAt(0).toUpperCase() || '?'}
                      </span>
                      <span className="min-w-0 flex-1 truncate text-[14px] font-medium leading-5 text-ink">{props.userName}</span>
                    </span>
                  )}
                </button>
              </div>
              <div data-sidebar-bottom-buttons="" className={cn('flex shrink-0 gap-0.5', rail ? 'flex-col items-center' : 'items-center')}>
                <NavRow id="team" label="Team" active={view === 'team'} onClick={() => props.onView('team')}>
                  <UsersRound size={18} />
                </NavRow>
                <NavRow id="contracts" label="Contracts" active={view === 'contracts'} onClick={() => props.onView('contracts')}>
                  <FileText size={18} />
                </NavRow>
                <NavRow id="results" label="Results" active={view === 'results'} onClick={() => props.onView('results')}>
                  <PackageCheck size={18} />
                </NavRow>
              </div>
            </div>
          </div>
        </GlassBar>
      </GlassScrollFrame>
    </aside>
  );
}
