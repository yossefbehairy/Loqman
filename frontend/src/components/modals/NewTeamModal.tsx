import React, { useState } from 'react';
import { X, Plus } from 'lucide-react';

interface Props {
  onClose: () => void;
  onSave: (teamData: { name: string; desc: string; managerName: string; managerRole: string }) => void;
}

export const NewTeamModal: React.FC<Props> = ({ onClose, onSave }) => {
  const [name, setName] = useState('');
  const [desc, setDesc] = useState('');
  const [managerName, setManagerName] = useState('');
  const [managerRole, setManagerRole] = useState('Manager');
  const [errorMsg, setErrorMsg] = useState('');

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim() || !managerName.trim()) {
      setErrorMsg('Team name and manager name are required');
      return;
    }
    onSave({
      name: name.trim(),
      desc: desc.trim(),
      managerName: managerName.trim(),
      managerRole: managerRole.trim() || 'Manager',
    });
  };

  return (
    <div className="fixed inset-0 bg-black/60 backdrop-blur-sm flex items-center justify-center p-4 z-50">
      <div className="os-card w-full max-w-md p-5 relative animate-in fade-in zoom-in-95 duration-150">
        <button
          onClick={onClose}
          className="absolute top-4 right-4 p-1 text-[var(--muted)] hover:text-[var(--text)] transition-colors"
          title="Close modal"
        >
          <X size={18} />
        </button>

        <h3 className="text-lg font-bold text-[var(--text)] tracking-tight mb-2">New Department Team</h3>
        <p className="text-xs text-[var(--muted)] mb-4">
          Establish a new department desk with its designated manager.
        </p>

        {errorMsg && (
          <div className="p-2 mb-3 text-xs text-[var(--red)] bg-[var(--red)]/10 border border-[var(--red)]/30 rounded-xl">
            {errorMsg}
          </div>
        )}

        <form onSubmit={handleSubmit} className="flex flex-col gap-3 text-xs">
          <div>
            <label className="block text-[var(--muted)] mb-1">Team Name</label>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g. Marketing & Growth, DevOps & Infra..."
              className="py-2 text-xs rounded-xl"
              autoFocus
            />
          </div>

          <div>
            <label className="block text-[var(--muted)] mb-1">Department Purpose</label>
            <textarea
              rows={2}
              value={desc}
              onChange={(e) => setDesc(e.target.value)}
              placeholder="One line explaining the core objectives of this desk..."
              className="py-2 text-xs rounded-xl"
            />
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div>
              <label className="block text-[var(--muted)] mb-1">Manager Name</label>
              <input
                type="text"
                value={managerName}
                onChange={(e) => setManagerName(e.target.value)}
                placeholder="e.g. Layla, Dr. Omar..."
                className="py-2 text-xs rounded-xl"
              />
            </div>

            <div>
              <label className="block text-[var(--muted)] mb-1">Manager Role Title</label>
              <input
                type="text"
                value={managerRole}
                onChange={(e) => setManagerRole(e.target.value)}
                placeholder="e.g. Head of Growth..."
                className="py-2 text-xs rounded-xl"
              />
            </div>
          </div>

          <p className="text-[11px] text-[var(--dim)] mt-1">
            After creating the department, add at least one specialist so the desk can execute delegated tasks.
          </p>

          <div className="flex items-center justify-end gap-2 pt-3 mt-1 border-t border-[var(--line)]">
            <button
              type="button"
              onClick={onClose}
              className="os-btn os-btn-ghost os-btn-sm"
            >
              Cancel
            </button>
            <button type="submit" className="os-btn os-btn-sm">
              <Plus size={14} />
              Create team
            </button>
          </div>
        </form>
      </div>
    </div>
  );
};
