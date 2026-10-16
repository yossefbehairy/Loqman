import React from 'react';
import type { Agent, Team } from '../../types/agentos';
import { STATUS_LABELS } from '../../data/agentosDefaults';
import { initials } from '../../utils/names';
import { AgentDetailPanel } from './AgentDetailPanel';
import { MessageSquare, Focus, Plus } from 'lucide-react';

interface Props {
  agents: Agent[];
  teams: Team[];
  selectedId: string | null;
  focusTeamId: string | null;
  armedDeleteTeamId: string | null;
  onSelectAgent: (id: string | null) => void;
  onFocusTeam: (teamId: string) => void;
  onOpenConversation: (agentId: string) => void;
  onEditContract: (agentId: string) => void;
  onAddSpecialist: (teamId: string) => void;
  onDeleteTeam: (teamId: string) => void;
  onInspectAgentTask: (agentId: string) => void;
}

export const CompanyOrgChart: React.FC<Props> = ({
  agents,
  teams,
  selectedId,
  focusTeamId,
  armedDeleteTeamId,
  onSelectAgent,
  onFocusTeam,
  onOpenConversation,
  onEditContract,
  onAddSpecialist,
  onDeleteTeam,
  onInspectAgentTask,
}) => {
  const ceo = agents.find((a) => a.mgr && !a.team);
  const selectedAgent = agents.find((a) => a.id === selectedId) || null;

  return (
    <div className="grid grid-cols-1 lg:grid-cols-[1fr_320px] gap-4 items-start mt-2">
      <div className="flex flex-col items-center min-w-0">
        {/* CEO Executive Card */}
        {ceo && (
          <div className="w-full max-w-[320px] flex flex-col items-center">
            <div
              role="button"
              tabIndex={0}
              aria-label={`Select ${ceo.name}`}
              onClick={() => onSelectAgent(ceo.id)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault();
                  onSelectAgent(ceo.id);
                }
              }}
              className={`os-card w-full text-center p-4 cursor-pointer hover:border-[var(--line-strong)] transition-all ${
                selectedId === ceo.id ? 'border-[var(--acc)] ring-1 ring-[var(--acc)]/30' : ''
              }`}
            >
              <div className="w-12 h-12 rounded-full bg-[#fafafa] text-[#18181b] font-bold text-base flex items-center justify-center mx-auto mb-2 shadow-md">
                {initials(ceo.name)}
              </div>
              <b className="text-base text-[var(--text)] block">{ceo.name}</b>
              <div className="text-xs text-[var(--muted)]">{ceo.role}</div>
              <div className="mt-1.5 inline-flex items-center gap-1.5 text-xs text-[var(--amber)]">
                <span className="w-2 h-2 rounded-full bg-[var(--amber)] animate-pulse" />
                {STATUS_LABELS[ceo.status] || ceo.status}
              </div>

              <div className="flex items-center justify-center gap-2 mt-3 pt-3 border-t border-[var(--line)]">
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    onOpenConversation(ceo.id);
                  }}
                  className="os-btn os-btn-sm"
                >
                  <MessageSquare size={13} />
                  Open conversation
                </button>
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    onSelectAgent(ceo.id);
                  }}
                  className="os-btn os-btn-sm os-btn-ghost"
                >
                  Summary
                </button>
              </div>
            </div>

            {/* Vertical connector line */}
            <div className="w-0.5 h-7 bg-[var(--line2)] my-0" />
          </div>
        )}

        {/* Departments Grid */}
        <div className="w-full grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
          {teams.map((t) => {
            const manager = agents.find((a) => a.id === t.mgr);
            const specialists = agents.filter((a) => a.team === t.id && a.id !== t.mgr);
            const isDimmed = focusTeamId && focusTeamId !== t.id;

            return (
              <div
                key={t.id}
                className={`os-card flex flex-col p-4 transition-opacity ${
                  isDimmed ? 'opacity-40 hover:opacity-100' : ''
                }`}
              >
                {/* Department Header */}
                <div className="flex items-center gap-2 mb-3 pb-2 border-b border-[var(--line)]">
                  <span className="w-2.5 h-2.5 rounded-full flex-none" style={{ background: t.color }} />
                  <b className="text-sm text-[var(--text)] font-semibold truncate flex-1">{t.name}</b>
                  <span className="text-xs text-[var(--muted)] mono">
                    {specialists.length + (manager ? 1 : 0)}
                  </span>
                  <button
                    onClick={() => onFocusTeam(t.id)}
                    className="os-btn os-btn-ghost os-btn-sm ml-auto text-[11px]"
                    title="Focus desk in graph view"
                  >
                    <Focus size={12} />
                    Focus
                  </button>
                </div>

                {/* Manager Card */}
                {manager ? (
                  <button
                    onClick={() => onSelectAgent(manager.id)}
                    className={`flex items-center gap-2.5 p-2 rounded-xl text-left transition-all ${
                      selectedId === manager.id
                        ? 'bg-[var(--acc)]/10 border border-[var(--acc)]/40'
                        : 'hover:bg-white/5 border border-transparent'
                    }`}
                  >
                    <span
                      className="w-8 h-8 rounded-full flex items-center justify-center text-xs font-bold text-[#18181b] flex-none"
                      style={{ background: t.color }}
                    >
                      {initials(manager.name)}
                    </span>
                    <div className="min-w-0 flex-1">
                      <div className="font-semibold text-xs text-[var(--text)] truncate">{manager.name}</div>
                      <div className="text-[11px] text-[var(--muted)] truncate">{manager.role}</div>
                      <div className="text-[10px] text-[var(--dim)] mt-0.5">
                        {STATUS_LABELS[manager.status] || manager.status}
                      </div>
                    </div>
                    <span className="os-tag os-tag-amber text-[10px] px-1.5 py-0.5 ml-auto">Manager</span>
                  </button>
                ) : (
                  <div className="text-xs text-[var(--muted)] italic p-2">No manager assigned</div>
                )}

                {/* Specialists List */}
                <div className="flex flex-col gap-1 mt-2 pt-2 border-t border-[var(--line)] flex-1 min-h-[90px]">
                  {specialists.map((spec) => (
                    <button
                      key={spec.id}
                      onClick={() => onSelectAgent(spec.id)}
                      className={`flex items-center gap-2.5 p-1.5 rounded-xl text-left transition-all ${
                        selectedId === spec.id
                          ? 'bg-[var(--acc)]/10 border border-[var(--acc)]/40'
                          : 'hover:bg-white/5 border border-transparent'
                      }`}
                    >
                      <span
                        className="w-7 h-7 rounded-full flex items-center justify-center text-[10px] font-bold text-[#18181b] flex-none"
                        style={{ background: t.color }}
                      >
                        {initials(spec.name)}
                      </span>
                      <div className="min-w-0 flex-1">
                        <div className="font-medium text-xs text-[var(--text)] truncate">{spec.name}</div>
                        <div className="text-[10px] text-[var(--muted)] truncate">{spec.role}</div>
                      </div>
                      <span
                        className={`w-2 h-2 rounded-full ${
                          spec.status === 'Active'
                            ? 'bg-[var(--green)]'
                            : spec.status === 'Attention'
                            ? 'bg-[var(--amber)] ring-1 ring-[var(--amber)]'
                            : 'bg-[var(--dim)]'
                        }`}
                      />
                    </button>
                  ))}

                  {specialists.length === 0 && (
                    <div className="text-xs text-[var(--muted)] italic p-3 text-center">
                      No specialists yet. Add one to activate this desk.
                    </div>
                  )}
                </div>

                {/* Add Specialist Button */}
                <button
                  onClick={() => onAddSpecialist(t.id)}
                  className="os-btn os-btn-ghost os-btn-sm w-full mt-3"
                >
                  <Plus size={13} />
                  Add specialist
                </button>
              </div>
            );
          })}
        </div>
      </div>

      {/* Detail Inspector Aside */}
      <aside className="os-card sticky top-4">
        <AgentDetailPanel
          agent={selectedAgent}
          agents={agents}
          teams={teams}
          armedDeleteTeamId={armedDeleteTeamId}
          onSelectAgent={onSelectAgent}
          onOpenConversation={onOpenConversation}
          onEditContract={onEditContract}
          onAddSpecialist={onAddSpecialist}
          onDeleteTeam={onDeleteTeam}
          onInspectAgentTask={onInspectAgentTask}
        />
      </aside>
    </div>
  );
};
