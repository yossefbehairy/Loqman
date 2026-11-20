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
import { AppearanceSettings } from './AppearanceSettings';
import { AiProviders } from './AiProviders';
import { ProviderPicker } from './ai/ProviderPicker';
import { SettingsSelect } from './ai/SettingsSelect';
import { saveDefaultProvider, useProviders } from '../state/useProviders';
import type { ProfilePhoto } from '../state/useProfilePhoto';
import { UserAvatar } from './UserAvatar';

export type PageId = 'general' | 'appearance' | 'ai' | 'about';
const PAGES: Array<{ id: PageId; label: string; icon: typeof User; keywords?: string }> = [
  { id: 'general', label: 'General', icon: User, keywords: 'profile photo avatar name default data backup export' },
  { id: 'appearance', label: 'Appearance', icon: Palette, keywords: 'theme typography font text size chat width skin' },
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
    <Card title="Data & backup" subtitle="A copy of your chats, agents and missions as one file you can keep somewhere safe.">
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

export function SettingsModal({
  onClose,
  userName,
  onUserName,
  profilePhoto,
  initialPage = 'general',
}: {
  onClose: () => void;
  initialPage?: PageId;
  userName: string;
  onUserName: (name: string) => void;
  profilePhoto: ProfilePhoto;
}) {
  const providers = useProviders();
  const savedDefault = providers.defaultPreference?.saved ?? null;
  const effectiveDefault = providers.defaultPreference?.effective ?? null;
  const photoInput = useRef<HTMLInputElement>(null);
  const [page, setPage] = useState<PageId>(initialPage);
  const [search, setSearch] = useState('');
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
            <div className="min-w-0 flex-1 sm:hidden">
              <SettingsSelect
                id="settings-page"
                aria-label="Settings page"
                value={page}
                onValueChange={(value) => setPage(value as PageId)}
                options={PAGES.map(p => ({ value: p.id, label: p.label }))}
              />
            </div>
            <span className="hidden text-[15px] font-semibold text-ink sm:block">{current.label}</span>
            <button aria-label="Close settings" title="Close settings (Esc)" onClick={onClose} className="ui-icon-button shrink-0 cursor-pointer">
              <X size={16} />
            </button>
          </div>
          <div className="flex flex-1 flex-col gap-4 overflow-y-auto px-3 py-4 sm:px-5 sm:pb-5">
            {page === 'general' && (
              <section data-settings-block="general" className="flex flex-col gap-4">
                <Card title="Profile">
                  <div className="flex min-w-0 flex-col gap-4 sm:flex-row sm:items-center sm:gap-6">
                    <div className="flex shrink-0 justify-center sm:w-32">
                      <UserAvatar name={userName} photo={profilePhoto.url} large />
                    </div>
                    <div className="min-w-0 flex-1">
                      <label htmlFor="settings-display-name" className="mb-1 block text-[13px] font-medium text-ink-secondary">Display Name</label>
                      <input
                        id="settings-display-name"
                        aria-label="Your name"
                        placeholder="Your name"
                        value={userName}
                        onChange={(e) => onUserName(e.target.value)}
                        className="w-full rounded-lg border border-hairline/40 bg-inset px-3 py-2 text-[14px] text-ink placeholder:text-ink-secondary focus:outline-none"
                      />
                      <p className="mt-2 text-[13px] leading-relaxed text-ink-secondary">Your name as it appears in the sidebar. Saved on this device.</p>
                    </div>
                  </div>
                  <div className="mt-3 flex flex-wrap items-center gap-3">
                    <input ref={photoInput} type="file" accept="image/png,image/jpeg,image/webp" aria-label="Choose profile photo" aria-describedby={profilePhoto.error ? 'profile-photo-help profile-photo-error' : 'profile-photo-help'} className="hidden" onChange={(e) => {
                      const file = e.target.files?.[0];
                      e.target.value = '';
                      if (file) void profilePhoto.change(file);
                    }} />
                    <button type="button" className="ui-button ui-button-md" disabled={profilePhoto.busy} aria-describedby={profilePhoto.error ? 'profile-photo-help profile-photo-error' : 'profile-photo-help'} onClick={() => photoInput.current?.click()}>
                      Change photo
                    </button>
                    {profilePhoto.url && <button type="button" className="ui-button ui-button-md profile-photo-remove" disabled={profilePhoto.busy} onClick={() => void profilePhoto.change(null)}>Remove photo</button>}
                    {profilePhoto.busy && <span role="status" className="text-[12px] text-ink-secondary">Updating photo…</span>}
                  </div>
                  <p id="profile-photo-help" className="mt-2 text-[12px] leading-relaxed text-ink-secondary">PNG, JPEG or WebP, up to 10 MB. Photo stays in this browser or desktop app; it is not synced or included in backups.</p>
                  {profilePhoto.error && <p id="profile-photo-error" role="alert" className="mt-2 text-[12px] text-danger">{profilePhoto.error}</p>}
                </Card>
                <Card title="Default for new chats" subtitle="Used by the chat box, by Loqman, for missions and as the starting point for new agents. You can still change it per chat.">
                  <div data-providers-default>
                    <ProviderPicker idPrefix="ai-default" value={savedDefault ?? effectiveDefault} onChange={(next) => void saveDefaultProvider(next)} />
                    {!savedDefault && effectiveDefault && (
                      <p className="mt-2 text-[12px] text-ink-secondary">No saved default. The worker uses {effectiveDefault.engine} · {effectiveDefault.model}.</p>
                    )}
                    {savedDefault && !providers.selectable.some((entry) => entry.id === savedDefault.engine) && (
                      <p className="mt-2 text-[12px] text-ink-secondary">Saved default is unavailable. {savedDefault.engine} · {savedDefault.model}</p>
                    )}
                    {providers.defaultSaving && <p role="status" className="mt-2 text-[12px] text-ink-secondary">Saving default…</p>}
                    {providers.defaultError && <p role="alert" className="mt-2 text-[12px] text-danger">Could not save the default. {providers.defaultError}</p>}
                  </div>
                </Card>
                <DataCard />
              </section>
            )}
            {page === 'appearance' && <AppearanceSettings />}
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
