import React, { useState, useRef, useEffect, useMemo } from 'react';
import type { Agent, Team, Message } from '../../types/agentos';
import { Search, Send, FileText, Bot, Play, UserRoundPlus, X } from 'lucide-react';
import type { SuggestedMission } from '../../types';
import type { AgentProfile, ModelCatalogEntry } from '../../types';
import { resolveCompanyEmployees } from '../../utils/employeeLink';

interface Props {
  agents: Agent[];
  teams: Team[];
  activeConvoId: string;
  onSelectConvo: (id: string) => void;
  messages: Record<string, Message[]>;
  onSendMessage: (agentId: string, text: string) => void;
  onOpenContract: (agentId: string) => void;
  onLaunchMission: (agentId: string, mission: SuggestedMission) => void;
  onDelegateTask: (payload: { employee_role: string; agent_profile_id?: string; title: string; instructions: string; input_context?: string; expected_output?: string; model_id: string; variant?: string }) => Promise<void>;
  employeeProfiles: AgentProfile[];
  freeModels: ModelCatalogEntry[];
  selectedModelId: string;
  selectedVariant: string;
  busy: Record<string, boolean>;
}

export const ConversationsView: React.FC<Props> = ({
  agents,
  teams,
  activeConvoId,
  onSelectConvo,
  messages,
  onSendMessage,
  onOpenContract,
  onLaunchMission,
  onDelegateTask,
  employeeProfiles,
  freeModels,
  selectedModelId,
  selectedVariant,
  busy,
}) => {
  const [filterQuery, setFilterQuery] = useState('');
  const [inputVal, setInputVal] = useState('');
  const [showDelegation, setShowDelegation] = useState(false);
  const [employeeRole, setEmployeeRole] = useState('');
  const [taskTitle, setTaskTitle] = useState('');
  const [taskInstructions, setTaskInstructions] = useState('');
  const [taskContext, setTaskContext] = useState('');
  const [expectedOutput, setExpectedOutput] = useState('');
  const [taskModelId, setTaskModelId] = useState(selectedModelId);
  const [taskVariant, setTaskVariant] = useState(selectedVariant);
  const [delegating, setDelegating] = useState(false);
  const [delegationError, setDelegationError] = useState('');
  const messagesEndRef = useRef<HTMLDivElement | null>(null);

  const activeAgent = agents.find((a) => a.id === activeConvoId) || agents[0];
  const activeTeam = teams.find((t) => t.id === activeAgent?.team);

  const currentMessages = activeAgent ? messages[activeAgent.id] || [] : [];

  const filteredAgents = agents.filter((m) => {
    const q = filterQuery.toLowerCase();
    const t = teams.find((x) => x.id === m.team);
    return `${m.name} ${m.role} ${t?.name || ''}`.toLowerCase().includes(q);
  });

  const initials = (name: string) =>
    name
      .split(/\s+/)
      .filter((w) => /[A-Za-z\u0600-\u06FF]/.test(w))
      .slice(0, 2)
      .map((w) => w[0])
      .join('')
      .toUpperCase() || '?';

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [currentMessages]);

  const handleSend = () => {
    const trimmed = inputVal.trim();
    if (!trimmed || !activeAgent) return;
    onSendMessage(activeAgent.id, trimmed);
    setInputVal('');
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const taskModel = freeModels.find((model) => model.id === taskModelId);
  // Delegation offers company members only — never the raw backend catalog.
  // The select value is the backend profile id (precise even with shared roles).
  const companyEmployees = useMemo(
    () => resolveCompanyEmployees(agents, employeeProfiles),
    [agents, employeeProfiles]
  );
  const handleDelegate = async () => {
    const entry = companyEmployees.find(({ profile }) => profile.id === employeeRole);
    if (!entry || !taskTitle.trim() || !taskInstructions.trim() || !taskModel) return;
    setDelegating(true);
    try {
      await onDelegateTask({
        employee_role: entry.profile.role_type,
        agent_profile_id: entry.profile.id,
        title: taskTitle.trim(),
        instructions: taskInstructions.trim(),
        input_context: taskContext.trim() || undefined,
        expected_output: expectedOutput.trim() || undefined,
        model_id: taskModel.id,
        variant: taskModel.variants.includes(taskVariant) ? taskVariant : taskModel.variants[0],
      });
      setShowDelegation(false);
      setTaskTitle(''); setTaskInstructions(''); setTaskContext(''); setExpectedOutput('');
      setDelegationError('');
    } catch (error) {
      setDelegationError(error instanceof Error ? error.message : 'Could not start delegated task.');
    } finally {
      setDelegating(false);
    }
  };

  return (
    <div className="flex flex-col gap-3 h-[calc(100vh-130px)] min-h-[500px]">
      <div>
        <h1 className="text-2xl font-bold tracking-tight text-[var(--text)] m-0">OpenCode Chat</h1>
        <p className="text-xs text-[var(--muted)] m-0 mt-1">
          Direct conversation with OpenCode. Messages continue in the same saved session.
        </p>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-[280px_1fr] gap-3 flex-1 min-h-0 overflow-hidden">
        {/* Agent Sidebar List */}
        <div className="os-card flex flex-col p-2.5 overflow-hidden">
          <div className="relative mb-2">
            <Search size={14} className="absolute left-2.5 top-2.5 text-[var(--dim)] pointer-events-none" />
            <input
              type="text"
              placeholder="Search agents"
              value={filterQuery}
              onChange={(e) => setFilterQuery(e.target.value)}
              className="pl-8 pr-2.5 py-1.5 text-xs rounded-xl"
              aria-label="Search agents"
            />
          </div>

          <div className="flex flex-col gap-1 overflow-y-auto flex-1 pr-1">
            {filteredAgents.map((m) => {
              const mTeam = teams.find((t) => t.id === m.team);
              const lastMsg = (messages[m.id] || []).slice(-1)[0];
              const preview = lastMsg
                ? lastMsg.t
                : `${mTeam ? mTeam.name : 'Company'} · Start conversation`;
              const isSelected = activeAgent?.id === m.id;

              return (
                <button
                  key={m.id}
                  onClick={() => onSelectConvo(m.id)}
                  className={`flex items-center gap-2.5 p-2 rounded-xl text-left transition-all ${
                    isSelected
                      ? 'bg-[var(--acc)]/15 border border-[var(--acc)]/30 text-[var(--text)]'
                      : 'hover:bg-white/5 border border-transparent text-[var(--muted)]'
                  }`}
                >
                  <span
                    className="w-8 h-8 rounded-full flex items-center justify-center text-xs font-bold text-[#18181b] flex-none"
                    style={{ background: mTeam ? mTeam.color : '#fafafa' }}
                  >
                    {initials(m.name)}
                  </span>
                  <div className="min-w-0 flex-1">
                    <div className="font-semibold text-xs text-[var(--text)] truncate">{m.name}</div>
                    <div className="text-[11px] truncate text-[var(--dim)]">{preview}</div>
                  </div>
                </button>
              );
            })}

            {filteredAgents.length === 0 && (
              <div className="text-center py-8 text-xs text-[var(--muted)]">No agents match.</div>
            )}
          </div>
        </div>

        {/* Active Chat Room */}
        <div className="os-card flex flex-col p-0 overflow-hidden min-h-0">
          {activeAgent ? (
            <>
              {/* Chat Header */}
              <div className="flex items-center gap-3 px-4 py-3 border-b border-[var(--line)] bg-[var(--card2)]/50">
                <span
                  className="w-8 h-8 rounded-full flex items-center justify-center text-xs font-bold text-[#18181b] flex-none"
                  style={{ background: activeTeam ? activeTeam.color : '#fafafa' }}
                >
                  {initials(activeAgent.name)}
                </span>
                <div className="min-w-0 flex-1">
                  <div className="font-semibold text-sm text-[var(--text)]">OpenCode · {activeAgent.name}</div>
                  <div className="text-xs text-[var(--muted)]">
                    Primary agent: build · {freeModels.find((model) => model.id === selectedModelId)?.name || selectedModelId}
                  </div>
                </div>
                <button
                  onClick={() => onOpenContract(activeAgent.id)}
                  className="os-btn os-btn-ghost os-btn-sm"
                  title="View and edit contract"
                >
                  <FileText size={13} />
                  Contract
                </button>
              </div>

              {/* Message List */}
              <div className="flex-1 overflow-y-auto p-4 flex flex-col gap-3 min-h-0">
                {currentMessages.length > 0 ? (
                  currentMessages.map((msg, idx) => (
                    <div
                      key={idx}
                      className={`max-w-[78%] p-3 rounded-2xl text-xs leading-relaxed whitespace-pre-wrap ${
                        msg.me
                          ? 'self-end bg-[var(--acc)]/15 border border-[var(--acc)]/30 text-[var(--text)] rounded-br-sm'
                          : 'self-start bg-[var(--card2)] border border-[var(--line)] text-[var(--text)] rounded-bl-sm'
                      }`}
                    >
                      <div>{msg.t}</div>
                      <small className="block mt-1 text-[10px] text-[var(--dim)] text-right mono">
                      {msg.me ? 'You' : activeAgent.name} · {msg.time}
                      </small>
                    {!msg.me && msg.suggestedMission && (
                      <button
                        onClick={() => onLaunchMission(activeAgent.id, msg.suggestedMission!)}
                        className="os-btn os-btn-ghost os-btn-sm mt-2"
                      >
                        <Play size={12} /> Run suggested mission
                      </button>
                    )}
                    </div>
                  ))
                ) : (
                  <div className="text-center py-16 text-xs text-[var(--muted)]">
                    <Bot size={28} className="mx-auto mb-2 text-[var(--dim)]" />
                    No messages yet with {activeAgent.name}. Send a message to start chatting.
                  </div>
                )}
                <div ref={messagesEndRef} />
              </div>

              {/* Chat Composer */}
              <div className="p-3 border-t border-[var(--line)] bg-[var(--card2)]/40 flex flex-col gap-2">
                {showDelegation && (
                  <div className="rounded-xl border border-[var(--line)] bg-[var(--card)] p-3 grid grid-cols-1 sm:grid-cols-2 gap-2.5">
                    <div className="sm:col-span-2 flex items-center justify-between text-xs font-semibold text-[var(--text)]">
                      <span>Delegate to AI Employee</span>
                      <button className="os-btn os-btn-ghost os-btn-sm" onClick={() => setShowDelegation(false)} aria-label="Close delegation panel"><X size={13} /></button>
                    </div>
                    <select value={employeeRole} onChange={(e) => setEmployeeRole(e.target.value)} className="text-xs rounded-lg" aria-label="Employee">
                      <option value="">Choose employee</option>
                      {companyEmployees.map(({ agent, profile }) => (
                        <option key={profile.id} value={profile.id}>
                          {agent.name} · {profile.role_type}
                        </option>
                      ))}
                      {companyEmployees.length === 0 && (
                        <option value="" disabled>No company members linked — add them in Company</option>
                      )}
                    </select>
                    <select value={taskModelId} onChange={(e) => { setTaskModelId(e.target.value); setTaskVariant(''); }} className="text-xs rounded-lg" aria-label="Free model">
                      {freeModels.map((model) => <option key={model.id} value={model.id}>{model.name} · free</option>)}
                    </select>
                    <input value={taskTitle} onChange={(e) => setTaskTitle(e.target.value)} placeholder="Task title" className="text-xs rounded-lg" />
                    <select value={taskVariant} onChange={(e) => setTaskVariant(e.target.value)} className="text-xs rounded-lg" aria-label="Reasoning setting">
                      <option value="">Default reasoning</option>
                      {(taskModel?.variants || []).map((variant) => <option key={variant} value={variant}>{variant}</option>)}
                    </select>
                    <textarea value={taskInstructions} onChange={(e) => setTaskInstructions(e.target.value)} placeholder="Task instructions" rows={3} className="sm:col-span-2 text-xs rounded-lg" />
                    <textarea value={taskContext} onChange={(e) => setTaskContext(e.target.value)} placeholder="Optional input or context" rows={2} className="text-xs rounded-lg" />
                    <textarea value={expectedOutput} onChange={(e) => setExpectedOutput(e.target.value)} placeholder="Expected deliverable (optional)" rows={2} className="text-xs rounded-lg" />
                    <div className="sm:col-span-2 flex justify-end">
                      <button onClick={() => void handleDelegate()} disabled={delegating || !employeeRole || !taskTitle.trim() || !taskInstructions.trim() || !taskModel} className="os-btn os-btn-sm">
                        <UserRoundPlus size={13} /> {delegating ? 'Assigning…' : 'Confirm and run task'}
                      </button>
                    </div>
                    {delegationError && <div className="sm:col-span-2 text-xs text-red-400" role="alert">{delegationError}</div>}
                  </div>
                )}
                <div className="flex items-end gap-2.5">
                <textarea
                  value={inputVal}
                  onChange={(e) => setInputVal(e.target.value)}
                  onKeyDown={handleKeyDown}
                  placeholder="Message OpenCode… (Press Enter to send)"
                  rows={2}
                  className="flex-1 resize-none text-xs rounded-xl py-2 px-3 leading-relaxed"
                  aria-label="Message text"
                />
                <button
                  onClick={handleSend}
                  disabled={!inputVal.trim() || !!busy[activeAgent.id]}
                  className="os-btn h-9 px-3.5 flex-none"
                  aria-label="Send message"
                >
                  <Send size={14} />
                  <span>Send</span>
                </button>
                <button onClick={() => setShowDelegation((current) => !current)} className="os-btn os-btn-ghost h-9 px-3 flex-none" title="Create a separate task for one employee">
                  <UserRoundPlus size={14} /><span>Delegate task</span>
                </button>
                </div>
              </div>
            </>
          ) : (
            <div className="text-center py-20 text-xs text-[var(--muted)]">
              No agents found. Add an agent in Company to start a conversation.
            </div>
          )}
        </div>
      </div>
    </div>
  );
};
