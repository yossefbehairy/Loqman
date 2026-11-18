/*
 * Dialog frame, navigation rail, section cards and row layout follow RuseBot
 * 0.1.127 src/components/SettingsModal.tsx and SettingsPrimitives.tsx
 * (Apache-2.0, Copyright 2026 Milind Soni and OpenMausBot contributors).
 * Pages: General, Appearance, AI (engines.status / engines.check /
 * engines.models), About.
 */
import { useEffect, useRef, useState } from 'react';
import { Download, Info, Palette, Search, Sparkles, User, X } from 'lucide-react';
import { cn } from '../lib/cn';
import { SKINS, applySkin, readSkin, type SkinId } from '../lib/skins';
import { FONT_IDS, applyFont, readFont, type FontId } from '../lib/fonts';
import { CHAT_WIDTH_IDS, applyChatWidth, readChatWidth, type ChatWidthId } from '../lib/chat-width';
import { AiProviders } from './AiProviders';

export type PageId = 'general' | 'appearance' | 'ai' | 'about';
const PAGES: Array<{ id: PageId; label: string; icon: typeof User; keywords?: string }> = [
  { id: 'general', label: 'General', icon: User, keywords: 'profile name data backup export' },
  { id: 'appearance', label: 'Appearance', icon: Palette, keywords: 'theme font chat width skin' },
  { id: 'ai', label: 'AI', icon: Sparkles, keywords: 'providers engines models accounts claude chatgpt codex opencode antigravity sign in' },
  { id: 'about', label: 'About', icon: Info },
];

function Card({ title, subtitle, children }: { title?: string; subtitle?: string; children?: React.ReactNode }) {
  return (
    <div className="rounded-xl bg-card p-4">
      {title && <div className="text-[15px] font-medium text-ink">{title}</div>}
      {subtitle && <div className={cn('text-[13px] leading-relaxed text-ink-secondary', title && 'mt-0.5')}>{subtitle}</div>}
      {children && <div className={title || subtitle ? 'mt-4' : undefined}>{children}</div>}
    </div>
  );
}

function Row({ id, title, subtitle, children }: { id: string; title: string; subtitle?: string; children: React.ReactNode }) {
  return (
    <div role="group" aria-labelledby={id} className="px-4">
      <div className="grid min-w-0 grid-cols-1 items-center gap-3 sm:grid-cols-[minmax(0,1fr)_auto] sm:gap-6">
        <div className="min-w-0">
          <div id={id} className="text-[14px] font-medium text-ink">
            {title}
          </div>
          {subtitle && <div className="mt-1 text-[12px] leading-relaxed text-ink-secondary">{subtitle}</div>}
        </div>
        <div className="min-w-0 sm:max-w-[240px]">{children}</div>
      </div>
    </div>
  );
}

interface BackupBridge {
  saveBackup?: () => Promise<{ saved: boolean; path?: string; size?: number; cancelled?: boolean }>;
}
/** Only the desktop app (Electron preload) can write a backup file; the browser dev server cannot. */
const backupBridge = () => (typeof window !== 'undefined' ? (window as unknown as { agentos?: BackupBridge }).agentos : undefined);

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

type BackupState = { kind: 'idle' } | { kind: 'running' } | { kind: 'saved'; path: string; size?: number } | { kind: 'error'; text: string };

function DataCard() {
  const available = typeof backupBridge()?.saveBackup === 'function';
  const [state, setState] = useState<BackupState>({ kind: 'idle' });
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);

  const run = async () => {
    const save = backupBridge()?.saveBackup;
    if (!save) return;
    setState({ kind: 'running' });
    try {
      const out = await save();
      if (!alive.current) return;
      if (out?.saved && out.path) setState({ kind: 'saved', path: out.path, size: out.size });
      else setState({ kind: 'idle' }); // cancelled in the save dialog: nothing to report
    } catch (e) {
      if (!alive.current) return;
      let text = e instanceof Error ? e.message : String(e);
      try {
        const parsed = JSON.parse(text) as { detail?: unknown };
        if (parsed && typeof parsed.detail === 'string') text = parsed.detail;
      } catch {
        /* plain message */
      }
      setState({ kind: 'error', text });
    }
  };

  return (
    <Card title="Data" subtitle="A copy of your chats, agents and missions as one file you can keep somewhere safe.">
      <div className="flex flex-col items-start gap-2">
        <button type="button" data-backup-button disabled={!available || state.kind === 'running'} onClick={() => void run()} className="ui-button ui-button-md">
          <Download size={13} aria-hidden="true" />
          {state.kind === 'running' ? 'Backing up…' : 'Back up data…'}
        </button>
        {!available && <p className="text-[12px] text-ink-secondary">Backups are available in the desktop app.</p>}
        {state.kind === 'running' && (
          <p role="status" className="text-[12px] text-ink-secondary">
            Saving the backup…
          </p>
        )}
        {state.kind === 'saved' && (
          <p role="status" dir="auto" className="break-all text-[12px] text-success">
            Saved to {state.path}
            {state.size != null ? ` (${formatSize(state.size)})` : ''}
          </p>
        )}
        {state.kind === 'error' && (
          <p role="alert" dir="auto" className="break-words text-[12px] text-danger">
            Could not back up. {state.text}
          </p>
        )}
      </div>
    </Card>
  );
}

const SELECT =
  'w-full min-w-0 cursor-pointer rounded-lg border border-hairline/40 bg-inset py-2 pl-3 pr-3 text-[13px] text-ink focus:outline-none focus:ring-1 focus:ring-focus';

export function SettingsModal({
  onClose,
  userName,
  onUserName,
  initialPage = 'general',
}: {
  onClose: () => void;
  initialPage?: PageId;
  userName: string;
  onUserName: (name: string) => void;
}) {
  const [page, setPage] = useState<PageId>(initialPage);
  const [search, setSearch] = useState('');
  const [skin, setSkin] = useState<SkinId>(() => readSkin());
  const [font, setFont] = useState<FontId>(() => readFont());
  const [width, setWidth] = useState<ChatWidthId>(() => readChatWidth());
  const dialogRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    dialogRef.current?.focus();
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose();
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose]);

  const pages = PAGES.filter((p) => !search.trim() || `${p.label} ${p.keywords ?? ''}`.toLowerCase().includes(search.trim().toLowerCase()));
  const current = PAGES.find((p) => p.id === page)!;

  return (
    <div
      className="glass-popup-frame"
      onMouseDown={(e) => e.target === e.currentTarget && onClose()}
    >
      <div aria-hidden="true" className="glass-scrim pointer-events-none absolute inset-0" />
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="app-settings-title"
        tabIndex={-1}
        className="glass-surface glass-popup animate-pop-in relative flex overflow-hidden rounded-[24px] outline-none"
      >
        <span id="app-settings-title" className="sr-only">
          Settings
        </span>
        <nav className="glass-rail hidden min-h-0 w-[200px] shrink-0 flex-col gap-1 overflow-y-auto border-r border-hairline/30 p-3 sm:flex">
          <div className="shrink-0 px-2 py-3 text-[15px] font-semibold text-ink">Settings</div>
          <div className="mb-2 mt-1 flex min-h-8 shrink-0 items-center gap-2 rounded-lg border border-transparent bg-control/70 px-2.5 py-2 focus-within:border-focus">
            <Search size={14} className="shrink-0 text-ink-secondary" />
            <input
              data-settings-search="true"
              placeholder="Search"
              aria-label="Search settings"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="w-full bg-transparent text-[13px] text-ink placeholder:text-ink-secondary focus:outline-none"
            />
          </div>
          <div className="flex flex-col gap-0.5 pb-2">
            {pages.map((p) => (
              <button
                key={p.id}
                data-settings-page={p.id}
                aria-current={page === p.id ? 'page' : undefined}
                onClick={() => setPage(p.id)}
                className={cn(
                  'flex min-h-9 cursor-pointer items-center gap-2.5 rounded-lg px-2.5 py-2 text-left text-[13px] transition-colors motion-reduce:transition-none',
                  page === p.id ? 'bg-control text-ink' : 'text-ink-secondary hover:bg-control/50 hover:text-ink'
                )}
              >
                <p.icon size={15} className="shrink-0" />
                {p.label}
              </button>
            ))}
          </div>
        </nav>
        <div className="flex min-h-0 min-w-0 flex-1 flex-col">
          <div className="flex shrink-0 items-center justify-between gap-3 border-b border-hairline/30 px-3 py-3 sm:pl-9">
            <select
              aria-label="Settings page"
              value={page}
              onChange={(e) => setPage(e.target.value as PageId)}
              className={cn(SELECT, 'sm:hidden')}
            >
              {PAGES.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.label}
                </option>
              ))}
            </select>
            <span className="hidden text-[15px] font-semibold text-ink sm:block">{current.label}</span>
            <button aria-label="Close settings" title="Close settings (Esc)" onClick={onClose} className="ui-icon-button shrink-0 cursor-pointer">
              <X size={16} />
            </button>
          </div>
          <div className="flex flex-1 flex-col gap-4 overflow-y-auto px-3 py-4 sm:px-5 sm:pb-5">
            {page === 'general' && (
              <section data-settings-block="general" className="flex flex-col gap-4">
                <Card title="Profile" subtitle="Your name as it appears in the sidebar. Saved on this device.">
                  <input
                    aria-label="Your name"
                    placeholder="Your name"
                    value={userName}
                    onChange={(e) => onUserName(e.target.value)}
                    className="w-full rounded-lg border border-hairline/40 bg-inset px-3 py-2 text-[14px] text-ink placeholder:text-ink-secondary focus:outline-none"
                  />
                </Card>
                <DataCard />
              </section>
            )}
            {page === 'appearance' && (
              <section data-settings-block="appearance" className="flex flex-col gap-4">
                <Row id="rb-skin" title="Theme" subtitle={SKINS.find((s) => s.id === skin)?.tagline}>
                  <select
                    aria-label="Theme"
                    value={skin}
                    onChange={(e) => {
                      const next = e.target.value as SkinId;
                      applySkin(next);
                      setSkin(next);
                    }}
                    className={SELECT}
                  >
                    {SKINS.map((s) => (
                      <option key={s.id} value={s.id}>
                        {s.name}
                      </option>
                    ))}
                  </select>
                </Row>
                <Row id="rb-font" title="Font" subtitle="Follow the theme or pick a typeface.">
                  <select
                    aria-label="Font"
                    value={font}
                    onChange={(e) => {
                      const next = e.target.value as FontId;
                      applyFont(next);
                      setFont(next);
                    }}
                    className={SELECT}
                  >
                    {FONT_IDS.map((f) => (
                      <option key={f} value={f}>
                        {f === 'skin' ? 'Theme default' : f.charAt(0).toUpperCase() + f.slice(1)}
                      </option>
                    ))}
                  </select>
                </Row>
                <Row id="rb-width" title="Chat width" subtitle="How wide the conversation column grows.">
                  <select
                    aria-label="Chat width"
                    value={width}
                    onChange={(e) => {
                      const next = e.target.value as ChatWidthId;
                      applyChatWidth(next);
                      setWidth(next);
                    }}
                    className={SELECT}
                  >
                    {CHAT_WIDTH_IDS.map((w) => (
                      <option key={w} value={w}>
                        {w.charAt(0).toUpperCase() + w.slice(1)}
                      </option>
                    ))}
                  </select>
                </Row>
              </section>
            )}
            {page === 'ai' && <AiProviders />}
            {page === 'about' && (
              <section data-settings-block="about" className="flex flex-col gap-4">
                <Card title="Loqman" subtitle="Local-first multi-agent workflow app.">
                  <p className="text-[12.5px] leading-relaxed text-ink-secondary">
                    This interface is adapted from RuseBot 0.1.127, Copyright 2026 Milind Soni and OpenMausBot contributors, used under the
                    Apache License 2.0. RuseBot is a modified version of OpenMausBot; the OpenMausBot name and mascot are not licensed
                    for this product. Changes: the RuseBot server, store and cloud features were replaced by the Loqman worker.
                  </p>
                </Card>
              </section>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
