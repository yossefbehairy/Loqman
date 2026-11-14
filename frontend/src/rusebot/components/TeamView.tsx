/*
 * Layout, classes and card dimensions follow RuseBot 0.1.127
 * src/components/TeamMapPage.tsx and TeamCanvas.tsx (Apache-2.0, Copyright
 * 2026 Milind Soni and OpenMausBot contributors): page header, dotted canvas,
 * department sections and 236x126 bot cards. The canvas is static (no pan,
 * zoom or drag-to-arrange). Structure comes from the classic AgentOS company
 * storage; profiles, missions and delegation use the AgentOS worker.
 */
import { useEffect, useMemo, useState } from 'react';
import { ChevronDown, FileText, MessageCircle, Send, Trash2, UserPlus, UsersRound, X } from 'lucide-react';
import { cn } from '../lib/cn';
import type { Agent, Team } from '../../types/agentos';
import { CEO_PROFILE_ID, isMissionTerminal, delegateToAgent, deleteProfile, updateProfileInstructions, type Mission, type Profile } from '../data/worker';
import { messageOf } from '../state/useChat';

const DOTS = {
  backgroundImage: 'radial-gradient(circle, color-mix(in srgb, var(--color-ink) 14%, transparent) 1px, transparent 1px)',
  backgroundSize: '22px 22px',
} as const;

function PageHeader({ icon, title, count, subtitle, action }: { icon: React.ReactNode; title: string; count: string; subtitle: string; action?: React.ReactNode }) {
  return (
    <header className="flex shrink-0 flex-wrap items-center justify-between gap-3 border-b border-hairline/40 px-6 py-4 max-md:pl-12">
      <div>
        <div className="flex items-center gap-2.5">
          {icon}
          <h1 className="text-[17px] font-semibold">{title}</h1>
          <span className="ml-1 text-[11px] text-ink-secondary">{count}</span>
        </div>
        <p className="mt-1 text-[12px] text-ink-secondary">{subtitle}</p>
      </div>
      {action}
    </header>
  );
}

const MISSION_POLL_MS = 3000;

/** A stored output or summary is plain text, a `{ result }` object, or some other JSON. */
function resultText(value: unknown): string {
  if (value == null) return '';
  if (typeof value === 'string') return value;
  if (typeof value === 'object' && typeof (value as { result?: unknown }).result === 'string') return (value as { result: string }).result;
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function MissionRow({ mission, open, onToggle }: { mission: Mission; open: boolean; onToggle: () => void }) {
  const summary = resultText(mission.final_summary);
  const failure = typeof mission.error_summary === 'string' ? mission.error_summary : '';
  const tasks = mission.tasks ?? [];
  return (
    <li data-mission={mission.id} className="rounded-lg border border-hairline/40 bg-card text-[12.5px]">
      <button
        type="button"
        aria-expanded={open}
        onClick={onToggle}
        className="flex w-full items-center gap-3 rounded-lg px-3 py-2 text-left hover:bg-raised focus-visible:outline-2 focus-visible:outline-accent"
      >
        <ChevronDown size={14} className={cn('shrink-0 text-ink-secondary transition-transform', !open && '-rotate-90')} />
        <span dir="auto" className="min-w-0 flex-1 truncate">
          {mission.title}
        </span>
        <span className="shrink-0 text-ink-secondary">{tasks.length} tasks</span>
        <span className="shrink-0 rounded-full bg-raised px-2 py-0.5 text-[11px] text-ink-secondary">{mission.status}</span>
      </button>
      {open && (
        <div data-mission-detail className="flex flex-col gap-2 border-t border-hairline/30 px-3 py-2.5">
          {summary && (
            <div>
              <div className="text-[11px] font-semibold text-ink-secondary">Summary</div>
              <p dir="auto" className="mt-1 whitespace-pre-wrap break-words text-ink">
                {summary}
              </p>
            </div>
          )}
          {failure && (
            <p role="alert" dir="auto" className="whitespace-pre-wrap break-words text-danger">
              {failure}
            </p>
          )}
          {tasks.map((t) => {
            const out = resultText(t.output);
            const err = typeof t.error_message === 'string' ? t.error_message : '';
            return (
              <div key={t.id} data-mission-task={t.id} className="rounded-md bg-inset px-2.5 py-2">
                <div className="flex items-center gap-2">
                  <span dir="auto" className="min-w-0 flex-1 truncate font-medium">
                    {t.title ?? 'Task'}
                  </span>
                  <span className="shrink-0 text-[11px] text-ink-secondary">{t.status}</span>
                </div>
                {out && (
                  <pre dir="auto" className="mt-1 max-h-48 overflow-auto whitespace-pre-wrap break-words font-mono text-[12px] text-ink">
                    {out}
                  </pre>
                )}
                {err && (
                  <p dir="auto" className="mt-1 whitespace-pre-wrap break-words text-danger">
                    {err}
                  </p>
                )}
              </div>
            );
          })}
          {!summary && !failure && tasks.length === 0 && <p className="text-ink-secondary">Nothing to show yet.</p>}
          {!summary && !failure && tasks.length > 0 && !isMissionTerminal(mission.status) && (
            <p className="text-ink-secondary">Still working. This updates by itself.</p>
          )}
        </div>
      )}
    </li>
  );
}

function initials(name: string): string {
  return name.trim().charAt(0).toUpperCase() || '?';
}

function AgentCard({ agent, selected, onOpen, onChat }: { agent: Agent; selected: boolean; onOpen: () => void; onChat: () => void }) {
  return (
    <article
      data-agent-card={agent.id}
      className={cn(
        'relative h-[126px] w-[236px] shrink-0 rounded-xl border bg-card shadow-sm transition-colors hover:border-ink-secondary/40',
        selected ? 'border-accent/60' : 'border-hairline/50'
      )}
    >
      <button
        type="button"
        data-bot-id={agent.id}
        aria-label={`Open ${agent.name}`}
        onClick={onOpen}
        className="flex h-[82px] w-full items-center gap-3 rounded-t-xl px-4 text-left focus-visible:outline-2 focus-visible:outline-accent"
      >
        <span className="flex size-[38px] shrink-0 items-center justify-center rounded-full bg-raised text-[15px] font-semibold text-ink-secondary">
          {initials(agent.name)}
        </span>
        <span className="min-w-0 flex-1">
          <span className="flex items-center gap-1.5">
            <span dir="auto" className="truncate text-[14px] font-semibold">
              {agent.name}
            </span>
            {agent.mgr && <span className="text-[11px] text-warning">★</span>}
          </span>
          <span dir="auto" className="mt-1 block truncate text-[11px] text-ink-secondary">
            {agent.role}
          </span>
        </span>
      </button>
      <div className="flex h-[43px] items-center gap-1 border-t border-hairline/30 px-2">
        <button
          type="button"
          onClick={onChat}
          aria-label={`Open chat with ${agent.name}`}
          title="Open chat"
          className="flex size-8 shrink-0 items-center justify-center rounded-lg text-ink-secondary hover:bg-control hover:text-ink focus-visible:outline-2 focus-visible:outline-accent"
        >
          <MessageCircle size={15} />
        </button>
        <span title={`Default model: ${agent.model}`} className="ml-auto flex h-8 min-w-0 max-w-[130px] items-center rounded-md px-2 text-[10px] text-ink-secondary">
          <span className="truncate">{agent.model}</span>
        </span>
      </div>
    </article>
  );
}

export function AgentPanel({
  agent,
  profile,
  onClose,
  onChat,
  onChanged,
}: {
  agent: Agent;
  profile: Profile | null;
  onClose: () => void;
  onChat: () => void;
  onChanged: () => void;
}) {
  const [task, setTask] = useState('');
  const [instructions, setInstructions] = useState(profile?.instructions ?? '');
  const [note, setNote] = useState<{ kind: 'ok' | 'error'; text: string } | null>(null);
  const [busy, setBusy] = useState(false);

  const delegate = async () => {
    if (!task.trim()) return;
    setBusy(true);
    setNote(null);
    try {
      if (!profile) throw new Error('This agent has no profile to run the work under.');
      // The worker resolves the engine and model: the agent's own engine, else the default chosen in Settings.
      const mission = await delegateToAgent(profile, task.trim());
      setNote({ kind: 'ok', text: `Delegated. Mission ${mission.status}.` });
      setTask('');
      onChanged();
    } catch (e) {
      setNote({ kind: 'error', text: messageOf(e) });
    } finally {
      setBusy(false);
    }
  };

  const remove = async () => {
    if (!window.confirm(`Delete ${agent.name}? This cannot be undone.`)) return;
    setBusy(true);
    setNote(null);
    try {
      await deleteProfile(agent.id);
      onChanged();
      onClose();
    } catch (e) {
      setNote({ kind: 'error', text: messageOf(e) });
      setBusy(false);
    }
  };

  const saveInstructions = async () => {
    if (!profile) return;
    setBusy(true);
    setNote(null);
    try {
      await updateProfileInstructions(profile.id, instructions);
      setNote({ kind: 'ok', text: 'Contract saved.' });
      onChanged();
    } catch (e) {
      setNote({ kind: 'error', text: messageOf(e) });
    } finally {
      setBusy(false);
    }
  };

  return (
    <aside
      aria-label={`${agent.name} details`}
      data-agent-panel={agent.id}
      className="absolute inset-y-0 right-0 z-30 flex w-[360px] max-w-full flex-col overflow-y-auto border-l border-hairline/40 bg-panel shadow-xl 2xl:static 2xl:shadow-none"
    >
      <div className="flex items-start gap-3 border-b border-hairline/40 px-5 py-4">
        <span className="flex size-10 shrink-0 items-center justify-center rounded-full bg-raised text-[16px] font-semibold text-ink-secondary">
          {initials(agent.name)}
        </span>
        <div className="min-w-0 flex-1">
          <h2 dir="auto" className="truncate text-[15px] font-semibold">
            {agent.name}
          </h2>
          <p dir="auto" className="truncate text-[12px] text-ink-secondary">
            {agent.role}
          </p>
        </div>
        <button type="button" onClick={onClose} aria-label="Close details" className="rounded-md p-1 text-ink-secondary hover:bg-raised hover:text-ink">
          <X size={16} />
        </button>
      </div>
      <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-2 px-5 py-4 text-[12.5px]">
        <dt className="text-ink-secondary">Status</dt>
        <dd>{agent.status}</dd>
        <dt className="text-ink-secondary">Model</dt>
        <dd className="break-all">{profile?.default_model_id ?? agent.model}</dd>
        <dt className="text-ink-secondary">Effort</dt>
        <dd>{agent.reason}</dd>
        <dt className="text-ink-secondary">Timeout</dt>
        <dd>{agent.timeout}s</dd>
      </dl>
      <div className="flex flex-col gap-3 px-5 pb-5">
        <button
          type="button"
          onClick={onChat}
          className="inline-flex h-9 items-center justify-center gap-2 rounded-full bg-accent px-4 text-[13px] font-medium text-accent-ink hover:brightness-110"
        >
          <MessageCircle size={14} /> Open chat
        </button>
        {agent.id !== CEO_PROFILE_ID && (
          <button type="button" disabled={busy} onClick={() => void remove()} className="ui-button ui-button-sm self-start text-danger">
            <Trash2 size={13} /> Delete agent
          </button>
        )}
        <label className="text-[12px] font-medium text-ink-secondary" htmlFor="rb-contract">
          Contract
        </label>
        {profile ? (
          <>
            <textarea
              id="rb-contract"
              value={instructions}
              onChange={(e) => setInstructions(e.target.value)}
              rows={6}
              dir="auto"
              className="w-full resize-y rounded-lg border border-hairline/40 bg-inset px-3 py-2 text-[13px] text-ink focus:outline-none focus:ring-1 focus:ring-focus"
            />
            <button
              type="button"
              disabled={busy || instructions === profile.instructions}
              onClick={() => void saveInstructions()}
              className="ui-button ui-button-sm self-start"
            >
              Save contract
            </button>
          </>
        ) : (
          <pre dir="auto" className="max-h-48 overflow-auto whitespace-pre-wrap break-words rounded-lg bg-inset px-3 py-2 text-[12.5px] text-ink">
            {agent.prompt || 'No contract text.'}
          </pre>
        )}
        <label className="text-[12px] font-medium text-ink-secondary" htmlFor="rb-delegate">
          Delegate a task
        </label>
        <textarea
          id="rb-delegate"
          value={task}
          onChange={(e) => setTask(e.target.value)}
          rows={3}
          dir="auto"
          placeholder={`What should ${agent.name} do?`}
          className="w-full resize-y rounded-lg border border-hairline/40 bg-inset px-3 py-2 text-[13px] text-ink placeholder:text-ink-secondary focus:outline-none focus:ring-1 focus:ring-focus"
        />
        <button
          type="button"
          disabled={busy || !task.trim()}
          onClick={() => void delegate()}
          className="ui-button ui-button-sm self-start"
        >
          <Send size={13} /> Delegate
        </button>
        {note && (
          <p role={note.kind === 'error' ? 'alert' : 'status'} className={cn('text-[12.5px]', note.kind === 'error' ? 'text-danger' : 'text-success')}>
            {note.text}
          </p>
        )}
      </div>
    </aside>
  );
}

export interface TeamViewProps {
  mode: 'team' | 'contracts';
  teams: Team[];
  agents: Agent[];
  profiles: Profile[];
  missions: Mission[];
  onOpenChat: (agent: Agent) => void;
  onChanged: () => void;
  /** Re-read only the mission list (cheap); used while missions are active. */
  /** Resolves when the request has settled; polling waits for it before scheduling the next one. */
  onPollMissions: () => Promise<void>;
  onPanelChange: (open: boolean) => void;
  onNewAgent: () => void;
}

export function TeamView(props: TeamViewProps) {
  const { teams, agents, profiles, missions } = props;
  const [selected, setSelectedState] = useState<string | null>(null);
  const setSelected = (id: string | null) => {
    setSelectedState(id);
    props.onPanelChange(id !== null);
  };
  const agent = agents.find((a) => a.id === selected) ?? null;
  const profile = agent?.profileId ? (profiles.find((p) => p.id === agent.profileId) ?? null) : null;

  const groups = useMemo(() => {
    const out: Array<{ key: string; name: string; members: Agent[] }> = [];
    const lead = agents.filter((a) => !a.team);
    if (lead.length) out.push({ key: 'exec', name: 'Executive', members: lead });
    for (const t of teams) out.push({ key: t.id, name: t.name, members: agents.filter((a) => a.team === t.id) });
    return out;
  }, [agents, teams]);

  const contracts = props.mode === 'contracts';
  const [openMission, setOpenMission] = useState<string | null>(null);
  const shownMissions = missions.slice(0, 8);
  const anyActive = shownMissions.some((m) => !isMissionTerminal(m.status));
  const { onPollMissions } = props;
  useEffect(() => {
    if (!anyActive) return;
    let alive = true;
    let timer: number | undefined;
    // The next poll is scheduled only after the previous one settles, so slow requests never overlap.
    const tick = async () => {
      if (document.visibilityState === 'visible') await onPollMissions().catch(() => undefined);
      if (alive) timer = window.setTimeout(() => void tick(), MISSION_POLL_MS);
    };
    timer = window.setTimeout(() => void tick(), MISSION_POLL_MS);
    return () => {
      alive = false;
      window.clearTimeout(timer);
    };
  }, [anyActive, onPollMissions]);

  return (
    <main className="relative flex h-full min-w-0 flex-1 flex-col bg-app" data-view={props.mode}>
      <PageHeader
        action={
          <button type="button" data-new-agent-page onClick={props.onNewAgent} className="ui-button ui-button-sm">
            <UserPlus size={14} /> New agent
          </button>
        }
        icon={contracts ? <FileText size={18} /> : <UsersRound size={18} />}
        title={contracts ? 'Contracts' : 'Team'}
        count={`${agents.length} ${agents.length === 1 ? 'member' : 'members'}`}
        subtitle={
          contracts
            ? 'What each employee is hired to do, with the model and limits they run under.'
            : 'Your managers, their departments, and how they work together.'
        }
      />
      <div className="relative flex min-h-0 flex-1">
        <div className="relative min-h-0 min-w-0 flex-1 overflow-auto" style={DOTS} role="region" aria-label={contracts ? 'Contracts' : 'Team canvas'}>
          {contracts ? (
            <ul className="mx-auto flex max-w-3xl flex-col gap-2 p-6">
              {agents.map((a) => (
                <li key={a.id}>
                  <button
                    type="button"
                    onClick={() => setSelected(a.id)}
                    data-contract-row={a.id}
                    className="flex w-full items-center gap-3 rounded-xl border border-hairline/50 bg-card px-4 py-3 text-left hover:border-ink-secondary/40"
                  >
                    <span className="flex size-9 shrink-0 items-center justify-center rounded-full bg-raised text-[14px] font-semibold text-ink-secondary">
                      {initials(a.name)}
                    </span>
                    <span className="min-w-0 flex-1">
                      <span dir="auto" className="block truncate text-[14px] font-semibold">
                        {a.name}
                      </span>
                      <span dir="auto" className="block truncate text-[12px] text-ink-secondary">
                        {a.role}
                      </span>
                    </span>
                    <span className="shrink-0 text-[11px] text-ink-secondary">
                      {a.model} · {a.reason} · {a.timeout}s
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          ) : (
            <div className="flex flex-wrap items-start gap-6 p-6">
              {groups.map((g) => (
                <section
                  key={g.key}
                  aria-label={`${g.name} department`}
                  className="rounded-2xl border border-hairline/50 bg-panel/90 shadow-sm"
                >
                  <header className="flex h-16 items-center gap-2 px-5">
                    <h2 dir="auto" className="min-w-0 flex-1 truncate text-[13px] font-semibold tracking-tight">
                      {g.name}
                    </h2>
                    <span className="text-[11px] tabular-nums text-ink-secondary">{g.members.length}</span>
                  </header>
                  <div className="flex flex-col gap-4 px-5 pb-5">
                    {g.members.map((a) => (
                      <AgentCard key={a.id} agent={a} selected={selected === a.id} onOpen={() => setSelected(a.id)} onChat={() => props.onOpenChat(a)} />
                    ))}
                    {g.members.length === 0 && <p className="w-[236px] text-[12px] text-ink-secondary">No members yet.</p>}
                  </div>
                </section>
              ))}
            </div>
          )}
          {missions.length > 0 && (
            <section aria-label="Delegated missions" className="mx-auto max-w-3xl px-6 pb-8">
              <h2 className="mb-2 text-[13px] font-semibold">Delegated missions</h2>
              <ul className="flex flex-col gap-1.5">
                {shownMissions.map((m) => (
                  <MissionRow key={m.id} mission={m} open={openMission === m.id} onToggle={() => setOpenMission(openMission === m.id ? null : m.id)} />
                ))}
              </ul>
            </section>
          )}
        </div>
        {agent && (
          <AgentPanel
            key={agent.id}
            agent={agent}
            profile={profile}
            onClose={() => setSelected(null)}
            onChat={() => props.onOpenChat(agent)}
            onChanged={props.onChanged}
          />
        )}
      </div>
    </main>
  );
}
