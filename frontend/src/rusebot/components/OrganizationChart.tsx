import { useLayoutEffect, useMemo, useRef, useState, type CSSProperties } from 'react';
import { createPortal } from 'react-dom';
import { ArrowRight, CodeXml, FlaskConical, Layers, MessageCircle, Pencil, Search, UsersRound, X } from 'lucide-react';
import { AgentAvatar } from './AgentAvatar';
import type { Profile } from '../data/worker';
import { effectiveModel } from '../state/effectiveModel';
import { useProviders } from '../state/useProviders';
import type { Agent } from '../types';
import { chartLayout, type ChartNode } from './organizationLayout';
import { useOrganizationZoom } from './useOrganizationZoom';
import { OrganizationZoomToolbar } from './OrganizationZoomToolbar';
import './organization-chart.css';

type TeamNode = Extract<ChartNode, { kind: 'team' }>;
interface Actions {
  onAgent: (agent: Agent, trigger: HTMLElement) => void;
  onChat: (agent: Agent) => void;
}

function TeamIcon({ name }: { name: string }) {
  const icons = { development: CodeXml, research: FlaskConical, design: Pencil };
  const Icon = Object.entries(icons).find(([key]) => key === name.toLowerCase())?.[1] ?? Layers;
  return <span className="org-symbol"><Icon size={22} aria-hidden="true" /></span>;
}

function ChatButton({ agent, onChat }: { agent: Agent; onChat: Actions['onChat'] }) {
  return <button className="org-chat" type="button" aria-label={`Open chat with ${agent.name}`} title="Open chat" onClick={() => onChat(agent)}>
    <MessageCircle size={17} />
  </button>;
}

function Member({ agent, model, onAgent, onChat, standalone, style }: Actions & {
  agent: Agent; model: string; standalone?: boolean; style?: CSSProperties;
}) {
  return <article className={standalone ? 'org-member org-solo' : 'org-member'} data-agent-card={agent.id} data-org-solo={standalone ? agent.id : undefined} style={style}>
    <button type="button" className="org-identity" data-bot-id={agent.id} aria-label={`Open ${agent.name}`} onClick={event => onAgent(agent, event.currentTarget)}>
      <AgentAvatar label={agent.name} />
      <span className="org-label">
        <strong dir="auto" title={agent.name}>{agent.name}</strong>
        <small dir="auto" title={`${agent.role} (${model})`}>{agent.role}</small>
      </span>
    </button>
    <ChatButton agent={agent} onChat={onChat} />
  </article>;
}

function TeamCard({ team, style, onRoster, onAgent, onChat, modelOf }: Actions & {
  team: TeamNode; style?: CSSProperties; onRoster: (trigger: HTMLButtonElement) => void; modelOf: (agent: Agent) => string;
}) {
  const compact = team.members.length > 3;
  const preview = team.members.slice(0, 3);
  const viewAll = <button type="button" className="org-more" aria-haspopup="dialog" onClick={event => onRoster(event.currentTarget)}>
    View all {team.members.length} agents <ArrowRight size={14} />
  </button>;
  return <section data-org-group={team.id} className="org-group" style={style} aria-label={team.name}>
    <header>
      <TeamIcon name={team.name} />
      <h2 dir="auto" title={team.name}>{team.name}</h2>
      <span className="org-pill"><UsersRound size={13} />{team.members.length} Agents</span>
    </header>
    {compact ? <div className="org-preview">
      <div className="org-initials">{preview.map(agent =>
        <button key={agent.id} data-bot-id={agent.id} aria-label={`Open ${agent.name}`} title={agent.name} onClick={event => onAgent(agent, event.currentTarget)}>
          <AgentAvatar label={agent.name} size="sm" />
        </button>
      )}</div>
      <small>+{team.members.length - preview.length} more</small>
      {viewAll}
    </div> : <>
      <div className="org-rows">{preview.map(agent =>
        <Member key={agent.id} agent={agent} model={modelOf(agent)} onAgent={onAgent} onChat={onChat} />
      )}</div>
      {viewAll}
    </>}
  </section>;
}

function cardStyle(box: { x: number; y: number; width: number; height: number }): CSSProperties {
  return { left: box.x - box.width / 2, top: box.y - box.height / 2, width: box.width, height: box.height };
}

export function OrganizationChart({ nodes, ceo, profiles, loaded, detailsOpen, onAgent, onChat, onDepartmentOpen }: Actions & {
  nodes: ChartNode[]; ceo?: Agent; profiles: Profile[]; loaded: boolean; detailsOpen: boolean; onDepartmentOpen: () => void;
}) {
  const providers = useProviders();
  const engines = useMemo(() => new Map(profiles.map(p => [p.id, p.engine])), [profiles]);
  const modelOf = (agent: Agent) => effectiveModel(providers, engines.get(agent.profileId ?? agent.id), agent.model).label;
  const host = useRef<HTMLDivElement>(null);
  const [toolbarViewport, setToolbarViewport] = useState<HTMLElement | null>(null);
  const [[width, height, workspaceWidth], setSize] = useState([0, 0, 0]);
  const rosterTrigger = useRef<HTMLButtonElement | null>(null);
  const [viewAll, setViewAll] = useState<string | null>(null);
  const active = nodes.find((node): node is TeamNode => node.kind === 'team' && node.id === viewAll);
  useLayoutEffect(() => {
    const frame = host.current!;
    const viewport = frame.parentElement!;
    const column = viewport.parentElement!;
    const workspace = column.parentElement!;
    setToolbarViewport(viewport);
    const measure = () => {
      const w = frame.clientWidth;
      const h = viewport.offsetHeight;
      const fullWidth = workspace.clientWidth;
      setSize(previous => previous[0] === w && previous[1] === h && previous[2] === fullWidth ? previous : [w, h, fullWidth]);
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(frame);
    observer.observe(viewport);
    observer.observe(workspace);
    return () => observer.disconnect();
  }, []);

  const narrow = workspaceWidth < 840;
  const layout = useMemo(() => chartLayout(nodes, width, height), [nodes, width, height]);
  const showZoom = loaded && !narrow && Boolean(ceo || nodes.length);
  const zoom = useOrganizationZoom(host, layout, { width, height }, {
    enabled: showZoom && !active, narrow, panelOpen: Boolean(active || detailsOpen),
  });

  function openDetails(agent: Agent, trigger: HTMLElement) {
    zoom.rememberViewport();
    setViewAll(null);
    onAgent(agent, active && rosterTrigger.current ? rosterTrigger.current : trigger);
  }

  return <div className="org-frame" ref={host}>
    <div className={narrow ? undefined : 'org-surface'} style={narrow ? undefined : { width: zoom.horizontal.extent, height: zoom.vertical.extent }}>
      <div className={narrow ? 'org-stage org-stack' : 'org-stage'} style={narrow ? undefined : {
        width: layout.width, height: layout.height,
        left: zoom.horizontal.start, top: zoom.vertical.start, transform: `scale(${zoom.scale})`,
      }}>
        {!narrow && ceo && <svg aria-hidden="true" className="org-lines" width={layout.width} height={layout.height}>
          {layout.cards.map(({node, path, from, to}) => <g key={`${node.kind}:${node.id}`}>
            <path d={path} /><circle cx={from.x} cy={from.y} r="3.5" /><circle cx={to.x} cy={to.y} r="3.5" />
          </g>)}
        </svg>}
        {!ceo && !nodes.length && <p className="org-empty" role="status">{loaded ? 'No team members yet.' : 'Loading team…'}</p>}
        {ceo && <article className="org-hub" data-agent-card={ceo.id} style={narrow ? undefined : cardStyle(layout.hub)}>
          <button className="org-chief" data-bot-id={ceo.id} aria-label={`Open ${ceo.name}`} onClick={e => openDetails(ceo, e.currentTarget)}>
            <strong><bdi>{ceo.name}</bdi> <span className="text-warning">★</span></strong>
            <small>CEO &amp; Chief Orchestrator</small>
          </button>
          <ChatButton agent={ceo} onChat={onChat} />
        </article>}
        {layout.cards.map(box => {
          const { node } = box;
          const style = narrow ? undefined : cardStyle(box);
          return node.kind === 'agent'
            ? <Member key={`agent:${node.id}`} standalone style={style} agent={node.agent} model={modelOf(node.agent)} onAgent={openDetails} onChat={onChat} />
            : <TeamCard key={`team:${node.id}`} team={node} style={style} modelOf={modelOf} onAgent={openDetails} onChat={onChat}
                onRoster={trigger => {
                  zoom.rememberViewport();
                  rosterTrigger.current = trigger;
                  onDepartmentOpen();
                  setViewAll(node.id);
                }} />;
        })}
      </div>
    </div>
    {showZoom && toolbarViewport?.parentElement && createPortal(
      <div className="org-zoom-dock">
        {!active && <OrganizationZoomToolbar viewport={toolbarViewport} scale={zoom.scale} zoomBy={zoom.zoomBy} fit={zoom.fit} />}
      </div>,
      toolbarViewport.parentElement,
    )}
    {active && <TeamRoster team={active} modelOf={modelOf} onClose={() => setViewAll(null)} onAgent={openDetails} onChat={onChat} />}
  </div>;
}

function TeamRoster({ team, modelOf, onClose, onAgent, onChat }: Actions & {
  team: TeamNode; modelOf: (agent: Agent) => string; onClose: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [query, setQuery] = useState('');
  useLayoutEffect(() => {
    const node = dialog.current!;
    node.showModal();
    node.querySelector('input')?.focus({ preventScroll: true });
    return () => node.close();
  }, []);
  const needle = query.trim().toLocaleLowerCase();
  const matches = team.members.filter(a => [a.name, a.role].some(label => label.toLocaleLowerCase().includes(needle)));
  return <dialog ref={dialog} className="org-panel" aria-labelledby="team-roster-title" onCancel={e => { e.preventDefault(); onClose(); }}>
    <header>
      <TeamIcon name={team.name} />
      <div><h2 id="team-roster-title" dir="auto">{team.name}</h2><small><UsersRound size={15} />{team.members.length} Agents</small></div>
      <button className="ui-icon-button" aria-label="Close team roster" onClick={onClose}><X size={18} /></button>
    </header>
    <label><Search size={17} /><input dir="auto" aria-label="Search team members" placeholder="Search team members…" value={query} onChange={e => setQuery(e.target.value)} /></label>
    <div className="org-roster">{matches.map(agent => <Member key={agent.id} agent={agent} model={modelOf(agent)} onAgent={onAgent} onChat={onChat} />)}
      {!matches.length && <p role="status">No matching agents. Try another name or role.</p>}</div>
  </dialog>;
}
