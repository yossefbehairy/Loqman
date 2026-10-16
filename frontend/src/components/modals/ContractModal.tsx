import React, { useState } from 'react';
import type { Agent, Team, ModelReasoning } from '../../types/agentos';
import type { AgentProfile, ModelCatalogEntry } from '../../types';
import { MODELS } from '../../data/agentosDefaults';
import { X, Trash2, Check } from 'lucide-react';

interface Props {
  agent: Agent | null;
  teamIdPrefill?: string | null;
  teams: Team[];
  models: ModelCatalogEntry[];
  profiles: AgentProfile[];
  onClose: () => void;
  onSave: (agentData: Partial<Agent> & { name: string; role: string }) => void;
  onDelete?: (id: string) => void;
}

export const ContractModal: React.FC<Props> = ({
  agent,
  teamIdPrefill,
  teams,
  models,
  profiles,
  onClose,
  onSave,
  onDelete,
}) => {
  const isEditing = Boolean(agent);
  const isCeo = agent?.mgr && !agent?.team;

  const [name, setName] = useState(agent?.name || '');
  const [role, setRole] = useState(agent?.role || '');
  const [team, setTeam] = useState<string>(
    agent ? agent.team || '' : teamIdPrefill || teams[0]?.id || ''
  );
  const [isManager, setIsManager] = useState(agent ? agent.mgr : false);
  const [model, setModel] = useState(agent?.model || models[0]?.id || MODELS[0].id);
  const [reason, setReason] = useState<ModelReasoning>(agent?.reason || 'high');
  const [timeout, setTimeout] = useState<number>(agent?.timeout || 180);
  const [strictJson, setStrictJson] = useState<boolean>(agent ? agent.json : true);
  const [prompt, setPrompt] = useState(agent?.prompt || '');
  const [profileId, setProfileId] = useState<string>(agent?.profileId || '');
  const [errorMsg, setErrorMsg] = useState('');

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim()) {
      setErrorMsg('Name is required');
      return;
    }
    if (!isCeo && !team) {
      setErrorMsg('Please select a team');
      return;
    }

    onSave({
      name: name.trim(),
      role: role.trim() || (isManager ? 'Manager' : 'Specialist'),
      team: isCeo ? null : team || null,
      mgr: isCeo ? true : isManager,
      model,
      reason,
      timeout: timeout || 180,
      json: strictJson,
      prompt: prompt.trim(),
      profileId: profileId || null,
    });
  };

  return (
    <div className="fixed inset-0 bg-black/60 backdrop-blur-sm flex items-center justify-center p-4 z-50">
      <div className="os-card w-full max-w-lg p-5 max-h-[90vh] overflow-y-auto relative animate-in fade-in zoom-in-95 duration-150">
        <button
          onClick={onClose}
          className="absolute top-4 right-4 p-1 text-[var(--muted)] hover:text-[var(--text)] transition-colors"
          title="Close modal"
        >
          <X size={18} />
        </button>

        <h3 className="text-lg font-bold text-[var(--text)] tracking-tight mb-4">
          {isEditing ? 'Edit Contract' : 'New Employee Contract'}
        </h3>

        {errorMsg && (
          <div className="p-2 mb-3 text-xs text-[var(--red)] bg-[var(--red)]/10 border border-[var(--red)]/30 rounded-xl">
            {errorMsg}
          </div>
        )}

        <form onSubmit={handleSubmit} className="flex flex-col gap-3 text-xs">
          <div>
            <label className="block text-[var(--muted)] mb-1">Full Name</label>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g. Layla Haddad..."
              className="py-2 text-xs rounded-xl"
              autoFocus
            />
          </div>

          <div>
            <label className="block text-[var(--muted)] mb-1">Role Title</label>
            <input
              type="text"
              value={role}
              onChange={(e) => setRole(e.target.value)}
              placeholder="e.g. Planning Specialist, Risk Analyst..."
              className="py-2 text-xs rounded-xl"
            />
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div>
              <label className="block text-[var(--muted)] mb-1">Team</label>
              <select
                value={team}
                onChange={(e) => setTeam(e.target.value)}
                disabled={isCeo}
                className="py-2 text-xs rounded-xl"
              >
                {isCeo && <option value="">— None (CEO) —</option>}
                {teams.map((t) => (
                  <option key={t.id} value={t.id}>
                    {t.name}
                  </option>
                ))}
              </select>
            </div>

            <div>
              <label className="block text-[var(--muted)] mb-1">Position</label>
              <select
                value={isManager ? 'mgr' : 'spec'}
                onChange={(e) => setIsManager(e.target.value === 'mgr')}
                disabled={isCeo}
                className="py-2 text-xs rounded-xl"
              >
                <option value="spec">Specialist</option>
                <option value="mgr">Manager</option>
              </select>
            </div>

            <div>
              <label className="block text-[var(--muted)] mb-1">Model</label>
              <select
                value={model}
                onChange={(e) => setModel(e.target.value)}
                className="py-2 text-xs rounded-xl mono"
              >
                {!models.some((entry) => entry.id === model) && (
                  <option value={model}>{model} (not verified)</option>
                )}
                {(models.length ? models : MODELS).map((m) => (
                  <option key={m.id} value={m.id}>
                    {m.name}
                  </option>
                ))}
              </select>
            </div>

            <div>
              <label className="block text-[var(--muted)] mb-1">Reasoning</label>
              <select
                value={reason}
                onChange={(e) => setReason(e.target.value as ModelReasoning)}
                className="py-2 text-xs rounded-xl capitalize"
              >
                <option value="none">none</option>
                <option value="low">low</option>
                <option value="high">high</option>
                <option value="max">max</option>
              </select>
            </div>

            <div>
              <label className="block text-[var(--muted)] mb-1">Timeout (seconds)</label>
              <input
                type="number"
                min={10}
                max={600}
                value={timeout}
                onChange={(e) => setTimeout(Number(e.target.value) || 180)}
                className="py-2 text-xs rounded-xl mono"
              />
            </div>

            <div>
              <label className="block text-[var(--muted)] mb-1">Strict JSON Output</label>
              <select
                value={strictJson ? 'yes' : 'no'}
                onChange={(e) => setStrictJson(e.target.value === 'yes')}
                className="py-2 text-xs rounded-xl"
              >
                <option value="yes">Yes</option>
                <option value="no">No</option>
              </select>
            </div>
          </div>

          <div>
            <label className="block text-[var(--muted)] mb-1">Linked employee profile</label>
            <select
              value={profileId}
              onChange={(e) => setProfileId(e.target.value)}
              className="py-2 text-xs rounded-xl"
              title="Backend execution profile — delegation only offers linked company members"
            >
              <option value="">— None (not delegatable) —</option>
              {profiles.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name} · {p.role_type}
                </option>
              ))}
            </select>
          </div>

          <div>
            <label className="block text-[var(--muted)] mb-1">System Prompt</label>
            <textarea
              rows={5}
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              placeholder="Instructions and operating boundaries for this employee..."
              className="py-2 text-xs rounded-xl leading-relaxed font-sans"
            />
          </div>

          <div className="flex items-center justify-between gap-3 pt-3 mt-1 border-t border-[var(--line)]">
            {isEditing && !isCeo && onDelete && agent && (
              <button
                type="button"
                onClick={() => onDelete(agent.id)}
                className="os-btn os-btn-danger os-btn-sm"
              >
                <Trash2 size={13} />
                Delete
              </button>
            )}

            <div className="flex items-center gap-2 ml-auto">
              <button
                type="button"
                onClick={onClose}
                className="os-btn os-btn-ghost os-btn-sm"
              >
                Cancel
              </button>
              <button type="submit" className="os-btn os-btn-sm">
                <Check size={14} />
                Save contract
              </button>
            </div>
          </div>
        </form>
      </div>
    </div>
  );
};
