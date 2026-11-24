/*
 * The original layout follows RuseBot 0.1.127
 * src/components/TeamMapPage.tsx and TeamCanvas.tsx (Apache-2.0, Copyright
 * 2026 Milind Soni and OpenMausBot contributors): page header, dotted canvas,
 * and mission controls. Organization membership comes from the company state.
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import { ChevronDown, Crown, Layers, FileText, MessageCircle, UserPlus, UsersRound, X } from 'lucide-react';
import { OrganizationChart } from './OrganizationChart';
import './agent-details.css';
import { organizationNodes } from './organizationLayout';
import { cn } from '../lib/cn';
import type { Agent, Team } from '../types';
import { CEO_PROFILE_ID, cancelMission, isMissionTerminal, delegateToAgent, deleteProfile, updateProfileInstructions, type Mission, type Profile } from '../data/worker';
import { messageOf } from '../state/useChat';
import { useProviders } from '../state/useProviders';
import { contractModelLabel, effectiveModel } from '../state/effectiveModel';

const DOTS = {
  backgroundImage: 'radial-gradient(circle, color-mix(in srgb, var(--color-ink) 14%, transparent) 1px, transparent 1px)',
  backgroundSize: '22px 22px',
} as const;

function PageHeader({ icon, title, count, subtitle, action }: { icon: React.ReactNode; title: string; count?: string; subtitle: string; action?: React.ReactNode }) {
  return (
    <header className="flex shrink-0 flex-wrap items-center justify-between gap-3 border-b border-hairline/40 px-6 py-4 max-md:pl-12">
      <div>
        <div className="flex items-center gap-2.5">
          {icon}
          <h1 className="text-[17px] font-semibold">{title}</h1>
          {count && <span className="ml-1 text-[11px] text-ink-secondary">{count}</span>}
        </div>
        <p className="mt-1 text-[12px] text-ink-secondary">{subtitle}</p>
      </div>
      {action}
    </header>
  );
}

const MISSION_POLL_MS = 3000;
const MISSIONS_SHOWN = 8;

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

function MissionRow({
  mission,
  open,
  onToggle,
  onCancelled,
}: {
  mission: Mission;
  open: boolean;
  onToggle: () => void;
  /** Resolves when the mission list has been read again. */
  onCancelled: () => Promise<void>;
}) {
  const summary = resultText(mission.final_summary);
  const failure = typeof mission.error_summary === 'string' ? mission.error_summary : '';
  const tasks = mission.tasks ?? [];
  const active = !isMissionTerminal(mission.status);
  const [confirming, setConfirming] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [cancelError, setCancelError] = useState<string | null>(null);
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);

  const cancel = async () => {
    setCancelling(true);
    setCancelError(null);
    try {
      await cancelMission(mission.id);
      await onCancelled();
      if (alive.current) setConfirming(false);
    } catch (e) {
      if (alive.current) setCancelError(messageOf(e));
    } finally {
      if (alive.current) setCancelling(false);
    }
  };

  return (
    <li data-mission={mission.id} className="rounded-lg border border-hairline/40 bg-card text-[12.5px]">
      <div className="flex items-center">
        <button
          type="button"
          aria-expanded={open}
          onClick={onToggle}
          className="flex min-w-0 flex-1 items-center gap-3 rounded-lg px-3 py-2 text-left hover:bg-raised focus-visible:outline-2 focus-visible:outline-accent"
        >
          <ChevronDown size={14} className={cn('shrink-0 text-ink-secondary transition-transform', !open && '-rotate-90')} />
          <span dir="auto" className="min-w-0 flex-1 truncate">
            {mission.title}
          </span>
          <span className="shrink-0 text-ink-secondary">{tasks.length} tasks</span>
          <span className="shrink-0 rounded-full bg-raised px-2 py-0.5 text-[11px] text-ink-secondary">{mission.status}</span>
        </button>
        {active && !confirming && (
          <button
            type="button"
            data-mission-cancel={mission.id}
            aria-label={`Cancel mission ${mission.title}`}
            onClick={() => setConfirming(true)}
            className="ui-button ui-button-sm mr-2 shrink-0"
          >
            Cancel
          </button>
        )}
      </div>
      {active && confirming && (
        <div data-mission-confirm className="flex flex-wrap items-center gap-2 border-t border-hairline/30 px-3 py-2">
          <span className="min-w-0 flex-1 text-ink-secondary">Stop this mission? Work already done is kept.</span>
          <button type="button" disabled={cancelling} onClick={() => void cancel()} className="ui-button ui-button-sm text-danger">
            {cancelling ? 'Cancelling…' : 'Yes, cancel mission'}
          </button>
          <button
            type="button"
            disabled={cancelling}
            onClick={() => {
              setConfirming(false);
              setCancelError(null);
            }}
            className="ui-button ui-button-sm"
          >
            Keep running
          </button>
        </div>
      )}
      {cancelError && (
        <p role="alert" dir="auto" className="break-words border-t border-hairline/30 px-3 py-2 text-danger">
          Could not cancel. {cancelError}
        </p>
      )}
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

export function AgentPanel({
  agent,
  profile,
  modelLabel,
  onClose,
  onChat,
  onChanged,
}: {
  agent: Agent;
  profile: Profile | null;
  modelLabel?: string;
  onClose: () => void;
  onChat: () => void;
  onChanged: () => void;
}) {
  const drawerModel = effectiveModel(useProviders(), profile?.engine, profile?.default_model_id ?? agent.model);
  const [task, setTask] = useState('');
  const taskRevision = useRef(0);
  const [instructions, setInstructions] = useState(profile?.instructions ?? '');
  const [note, setNote] = useState<{ kind: 'ok' | 'error'; text: string } | null>(null);
  const [busy, setBusy] = useState(false);

  const delegate = async () => {
    const assignment = task.trim();
    if (!assignment) return;
    const submittedRevision = taskRevision.current;
    setBusy(true);
    setNote(null);
    try {
      if (!profile) throw new Error('This agent has no profile to run the work under.');
      // The worker resolves the engine and model: the agent's own engine, else the default chosen in Settings.
      const mission = await delegateToAgent(profile, assignment);
      setNote({ kind: 'ok', text: `Delegated. Mission ${mission.status}.` });
      if (taskRevision.current === submittedRevision) setTask('');
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
      className="agent-details"
      onKeyDown={event => {
        if (event.key === 'Escape' && !event.nativeEvent.isComposing) {
          event.stopPropagation();
          onClose();
        }
      }}
    >
      <header className="agent-details-header">
        <span className="agent-details-avatar" aria-hidden="true">{initials(agent.name)}</span>
        <div className="agent-details-identity">
          <h2 dir="auto">{agent.name}</h2>
          {agent.role?.trim() && <p dir="auto">{agent.role}</p>}
        </div>
        <button type="button" onClick={onClose} aria-label="Close details" className="ui-icon-button">
          <X size={16} />
        </button>
      </header>

      <div className="agent-details-body">
        <button type="button" onClick={onChat} className="ui-button agent-details-chat">
          <MessageCircle size={15} /> Open chat
        </button>

        <section className="agent-details-section">
          <h3>Overview</h3>
          <dl className="agent-details-fields">
            <dt>Status</dt>
            <dd>{agent.status}</dd>
            <dt>Model</dt>
            <dd dir="auto">{modelLabel ?? drawerModel.label}</dd>
            <dd data-model-note>Used for delegated tasks. Chats inherit available Agent settings unless changed in the + menu.</dd>
            <dt>Effort</dt>
            <dd>{agent.reason}</dd>
            <dt>Timeout</dt>
            <dd>{agent.timeout}s</dd>
          </dl>
        </section>

        <section className="agent-details-section">
          <h3>{profile ? <label htmlFor="rb-contract">Contract</label> : 'Contract'}</h3>
          {profile ? (
            <>
              <textarea id="rb-contract" value={instructions} onChange={e => setInstructions(e.target.value)} rows={3} dir="auto" />
              <button type="button" disabled={busy || instructions === profile.instructions} onClick={() => void saveInstructions()} className="ui-button ui-button-sm">
                Save contract
              </button>
            </>
          ) : (
            <p dir="auto" className="agent-details-contract">{agent.prompt || 'No contract text.'}</p>
          )}
        </section>

        <section className="agent-details-section">
          <h3><label htmlFor="rb-delegate">Delegate a task</label></h3>
          <textarea
            id="rb-delegate"
            value={task}
            onChange={e => {
              taskRevision.current++;
              setTask(e.target.value);
            }}
            rows={3}
            dir="auto"
            placeholder={`What should ${agent.name} do?`}
          />
          <button type="button" disabled={busy || !task.trim()} onClick={() => void delegate()} className="ui-button ui-button-sm">
            Delegate
          </button>
        </section>

        {note && (
          <p role={note.kind === 'error' ? 'alert' : 'status'} className={cn('text-[12.5px]', note.kind === 'error' ? 'text-danger' : 'text-success')}>
            {note.text}
          </p>
        )}

        {agent.id !== CEO_PROFILE_ID && (
          <div className="agent-details-footer">
            <button type="button" disabled={busy} onClick={() => void remove()} className="ui-button agent-details-delete">
              Delete agent
            </button>
          </div>
        )}
      </div>
    </aside>
  );
}

export interface TeamViewProps {
  mode: 'team' | 'contracts';
  loaded: boolean;
  contractsStatus: 'loading' | 'ready' | 'error';
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
  const { teams, agents, profiles, missions, contractsStatus } = props;
  const providers = useProviders();
  const profilesById = useMemo(() => new Map(profiles.map(profile => [profile.id, profile])), [profiles]);
  const modelOf = (member: Agent) => contractModelLabel(providers, profilesById.get(member.profileId ?? member.id)?.engine, member.model);
  const [selected, setSelectedState] = useState<string | null>(null);
  const setSelected = (id: string | null) => {
    setSelectedState(id);
    props.onPanelChange(id !== null);
  };
  const agent = agents.find((a) => a.id === selected) ?? null;
  const profile = agent?.profileId ? (profilesById.get(agent.profileId) ?? null) : null;

  const ceo = agents.find(a => a.id === CEO_PROFILE_ID);
  const nodes = useMemo(() => organizationNodes(agents, teams), [agents, teams]);
  const named = nodes.filter(node => node.kind === 'team').length;
  const members = agents.length - Number(Boolean(ceo));
  const chartTrigger = useRef<HTMLElement | null>(null);
  const newAgent = <button type="button" data-new-agent-page onClick={props.onNewAgent} className="ui-button ui-button-sm">
    <UserPlus size={14} /> New agent
  </button>;
  const contracts = props.mode === 'contracts';
  const [openMission, setOpenMission] = useState<string | null>(null);
  const [allMissions, setAllMissions] = useState(false);
  // The eight newest missions, plus every one still running however old it is; the rest wait behind "Show older".
  const shownMissions = missions.filter((m, i) => allMissions || i < MISSIONS_SHOWN || !isMissionTerminal(m.status));
  const hiddenMissions = missions.length - shownMissions.length;
  const anyActive = missions.some((m) => !isMissionTerminal(m.status));
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
      {contracts ? (
        <PageHeader icon={<FileText size={18} />} title="Contracts" action={newAgent}
          count={contractsStatus === 'ready' ? `${agents.length} ${agents.length === 1 ? 'member' : 'members'}` : undefined}
          subtitle="What each employee is hired to do, with the model and limits they run under." />
      ) : (
        <header className="org-heading">
          <div className="org-title"><UsersRound size={25} /><div><h1>Team</h1><p>Your agents and how they work together.</p></div></div>
          <div className="org-summary" aria-label="Organization summary">
            <span><Crown /><b>{Number(Boolean(ceo))}</b> CEO</span>
            <span><Layers /><b>{named}</b> {named === 1 ? 'team' : 'teams'}</span>
            <span><UsersRound /><b>{members}</b> {members === 1 ? 'agent' : 'agents'}</span>
          </div>
          {newAgent}
        </header>
      )}
      <div className="relative flex min-h-0 flex-1">
        <div className="relative flex min-h-0 min-w-0 flex-1">
          <div className="relative min-h-0 min-w-0 flex-1 overflow-auto" style={DOTS} role="region" aria-label={contracts ? 'Contracts' : 'Team canvas'}>
            {contracts ? (
              <div className="mx-auto max-w-3xl p-6">
                {contractsStatus === 'loading' && <p role="status" className="text-[13px] text-ink-secondary">Loading contracts…</p>}
                {contractsStatus === 'ready' && (agents.length ? (
                  <ul className="flex flex-col gap-2">
                    {agents.map(a => {
                      const model = modelOf(a);
                      return (
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
                              <span dir="auto" title={a.name} className="block truncate text-[14px] font-semibold">
                                {a.name}
                              </span>
                              <span dir="auto" title={a.role} className="block truncate text-[12px] text-ink-secondary">
                                {a.role}
                              </span>
                            </span>
                            <span className="flex min-w-0 max-w-[45%] items-center gap-1 text-[11px] text-ink-secondary">
                              <bdi title={model} className="truncate">{model}</bdi>
                              <span className="shrink-0 whitespace-nowrap">· {a.reason} · {a.timeout}s</span>
                            </span>
                          </button>
                        </li>
                      );
                    })}
                  </ul>
                ) : <p className="text-[13px] text-ink-secondary">No contracts yet.</p>)}
              </div>
            ) : (
              <OrganizationChart ceo={ceo} nodes={nodes} profiles={profiles} loaded={props.loaded} detailsOpen={Boolean(agent)} onChat={props.onOpenChat}
                onDepartmentOpen={() => setSelected(null)}
                onAgent={(member, trigger) => {
                  chartTrigger.current = trigger;
                  setSelected(member.id);
                  requestAnimationFrame(() => document.querySelector<HTMLElement>('[data-agent-panel] [aria-label="Close details"]')?.focus({ preventScroll: true }));
                }} />
            )}
            {missions.length > 0 && (
              <section aria-label="Delegated missions" className="mx-auto max-w-3xl px-6 pb-8">
                <h2 className="mb-2 text-[13px] font-semibold">Delegated missions</h2>
                <ul className="flex flex-col gap-1.5">
                  {shownMissions.map((m) => (
                    <MissionRow
                      key={m.id}
                      mission={m}
                      open={openMission === m.id}
                      onToggle={() => setOpenMission(openMission === m.id ? null : m.id)}
                      onCancelled={onPollMissions}
                    />
                  ))}
                </ul>
                {(hiddenMissions > 0 || (allMissions && missions.length > MISSIONS_SHOWN)) && (
                  <button type="button" data-missions-older onClick={() => setAllMissions((v) => !v)} className="ui-button ui-button-sm mt-2">
                    {allMissions ? 'Show fewer' : `Show ${hiddenMissions} older ${hiddenMissions === 1 ? 'mission' : 'missions'}`}
                  </button>
                )}
              </section>
            )}
          </div>
        </div>
        {agent && (
          <AgentPanel
            key={agent.id}
            agent={agent}
            profile={profile}
            modelLabel={contracts ? modelOf(agent) : undefined}
            onClose={() => {
              setSelected(null);
              if (!contracts && chartTrigger.current?.isConnected) chartTrigger.current.focus({ preventScroll: true });
            }}
            onChat={() => props.onOpenChat(agent)}
            onChanged={props.onChanged}
          />
        )}
      </div>
    </main>
  );
}
