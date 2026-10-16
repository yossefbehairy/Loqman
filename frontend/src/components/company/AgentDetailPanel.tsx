import React from 'react';
import type { Agent, Team } from '../../types/agentos';
import { STATUS_LABELS } from '../../data/agentosDefaults';
import { initials } from '../../utils/names';
import { MessageSquare, Edit3, UserPlus, Trash2, X, AlertCircle, Activity } from 'lucide-react';

interface Props {
  agent: Agent | null;
  agents: Agent[];
  teams: Team[];
  armedDeleteTeamId: string | null;
  onClose?: () => void;
  onSelectAgent: (id: string | null) => void;
  onOpenConversation: (agentId: string) => void;
  onEditContract: (agentId: string) => void;
  onAddSpecialist: (teamId: string) => void;
  onDeleteTeam: (teamId: string) => void;
  onInspectAgentTask: (agentId: string) => void;
}

export const AgentDetailPanel: React.FC<Props> = ({
  agent,
  agents,
  teams,
  armedDeleteTeamId,
  onClose,
  onSelectAgent,
  onOpenConversation,
  onEditContract,
  onAddSpecialist,
  onDeleteTeam,
  onInspectAgentTask,
}) => {
  if (!agent) {
    return (
      <div className="text-center py-10 px-4 text-[var(--muted)]">
        <b className="text-[var(--text)] text-sm block mb-1">No one selected</b>
        <p className="text-xs leading-relaxed">
          Click a person in the graph or org chart to see their summary.
          <br />
          Managers open conversations. Specialists display their contract.
        </p>
      </div>
    );
  }

  const team = teams.find((t) => t.id === agent.team);
  const isCeo = agent.mgr && !agent.team;
  const tagLabel = isCeo ? 'Executive' : agent.mgr ? 'Manager' : 'Specialist';
  const tagColor = isCeo ? 'os-tag-mono' : agent.mgr ? 'os-tag-amber' : 'os-tag-green';

  const subordinates = isCeo
    ? teams.map((tt) => agents.find((x) => x.id === tt.mgr)).filter((m): m is Agent => Boolean(m))
    : agent.mgr && team
    ? agents.filter((x) => x.team === team.id && x.id !== agent.id)
    : [];

  const hasNoSpecialists = agent.mgr && team && subordinates.length === 0;
  const isDeleteArmed = team && armedDeleteTeamId === team.id;

  return (
    <div className="flex flex-col gap-3 text-[13px] relative">
      {onClose && (
        <button
          onClick={onClose}
          className="absolute top-0 right-0 p-1 text-[var(--dim)] hover:text-[var(--text)] transition-colors"
          title="Close inspector"
        >
          <X size={16} />
        </button>
      )}

      {/* Header Tag and Name */}
      <div>
        <span className={`os-tag ${tagColor} mb-2`}>{tagLabel}</span>
        <h3 className="text-lg font-semibold text-[var(--text)] tracking-tight m-0">{agent.name}</h3>
        <p className="text-xs text-[var(--muted)] m-0 mt-0.5">{agent.role}</p>
      </div>

      {/* Key-Value Properties */}
      <div className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1.5 py-2 border-y border-[var(--line)] text-xs">
        <span className="text-[var(--muted)]">Team</span>
        <span className="text-[var(--text)] font-medium">{team ? team.name : 'Company (CEO)'}</span>

        <span className="text-[var(--muted)]">Status</span>
        <span className="flex items-center gap-1.5">
          <span
            className={`w-2 h-2 rounded-full ${
              agent.status === 'Active'
                ? 'bg-[var(--green)]'
                : agent.status === 'Attention'
                ? 'bg-[var(--amber)] ring-2 ring-[var(--amber)]/30'
                : 'bg-[var(--dim)]'
            }`}
          />
          <span className="text-[var(--text)]">{STATUS_LABELS[agent.status] || agent.status}</span>
        </span>

        <span className="text-[var(--muted)]">Model</span>
        <span className="mono text-[11px] text-[var(--acc)]">{agent.model}</span>

        <span className="text-[var(--muted)]">Reasoning</span>
        <span className="text-[var(--text)] capitalize">{agent.reason}</span>

        <span className="text-[var(--muted)]">Timeout</span>
        <span className="text-[var(--text)]">{agent.timeout}s</span>

        <span className="text-[var(--muted)]">Strict JSON</span>
        <span className="text-[var(--text)]">{agent.json ? 'On' : 'Off'}</span>
      </div>

      {/* System Prompt */}
      <div>
        <div className="text-xs text-[var(--muted)] font-medium mb-1">System Prompt</div>
        <div className="bg-[var(--card2)] border border-[var(--line)] rounded-xl p-2.5 text-xs text-[var(--muted)] font-normal max-h-48 overflow-y-auto whitespace-pre-wrap leading-relaxed select-text">
          {agent.prompt || '—'}
        </div>
      </div>

      {/* No Specialists Warning */}
      {hasNoSpecialists && (
        <div className="flex items-start gap-2 p-2.5 rounded-xl border border-[var(--amber)]/40 bg-[var(--amber)]/10 text-xs">
          <AlertCircle size={16} className="text-[var(--amber)] flex-none mt-0.5" />
          <div>
            <b className="text-[var(--amber)] block font-semibold">No specialists yet</b>
            <span className="text-[var(--muted)]">This desk cannot execute work until you add at least one specialist.</span>
          </div>
        </div>
      )}

      {/* Subordinates / Team Members */}
      {subordinates.length > 0 && (
        <div>
          <div className="text-xs font-semibold text-[var(--muted)] uppercase tracking-wider mb-1.5">
            {isCeo ? 'Department Managers' : 'Team Members'}
          </div>
          <div className="flex flex-col gap-1 max-h-36 overflow-y-auto">
            {subordinates.map((sub) => {
              const subTeam = teams.find((t) => t.id === sub.team);
              return (
                <button
                  key={sub.id}
                  onClick={() => onSelectAgent(sub.id)}
                  className="flex items-center gap-2.5 p-1.5 rounded-xl hover:bg-white/5 transition-colors text-left group"
                >
                  <span
                    className="w-6 h-6 rounded-full flex items-center justify-center text-[10px] font-bold text-[#18181b] flex-none"
                    style={{ background: subTeam ? subTeam.color : '#fafafa' }}
                  >
                    {initials(sub.name)}
                  </span>
                  <div className="min-w-0 flex-1">
                    <div className="font-medium text-xs text-[var(--text)] truncate group-hover:text-[var(--acc)]">
                      {sub.name}
                    </div>
                    <div className="text-[10px] text-[var(--muted)] truncate">{sub.role}</div>
                  </div>
                </button>
              );
            })}
          </div>
        </div>
      )}

      {/* Actions */}
      <div className="flex flex-col gap-2 pt-2 border-t border-[var(--line)]">
        <button
          onClick={() => onInspectAgentTask(agent.id)}
          className="os-btn os-btn-sm w-full bg-[var(--acc)]/15 text-[var(--acc)] border border-[var(--acc)]/35 hover:bg-[var(--acc)]/25 font-semibold py-2"
          title="See current task and inter-agent dialogue"
        >
          <Activity size={14} />
          <span>View active task & dialogue (ماذا يفعل الآن)</span>
        </button>

        <div className="flex flex-wrap gap-2">
          {agent.mgr && (
            <button
              onClick={() => onOpenConversation(agent.id)}
              className="os-btn os-btn-sm flex-1"
            >
              <MessageSquare size={14} />
              Open conversation
            </button>
          )}

          <button
            onClick={() => onEditContract(agent.id)}
            className="os-btn os-btn-sm os-btn-ghost flex-1"
          >
            <Edit3 size={14} />
            Edit contract
          </button>
        </div>

        {agent.mgr && team && (
          <button
            onClick={() => onAddSpecialist(team.id)}
            className="os-btn os-btn-sm os-btn-ghost w-full"
          >
            <UserPlus size={14} />
            Add specialist
          </button>
        )}

        {agent.mgr && team && (
          <button
            onClick={() => onDeleteTeam(team.id)}
            className={`os-btn os-btn-sm os-btn-danger w-full ${isDeleteArmed ? 'animate-pulse' : ''}`}
          >
            <Trash2 size={14} />
            {isDeleteArmed ? 'Click again to confirm delete' : 'Delete team'}
          </button>
        )}
      </div>
    </div>
  );
};
