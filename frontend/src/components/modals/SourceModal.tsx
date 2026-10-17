import React from 'react';
import { X, ShieldCheck } from 'lucide-react';

interface Props {
  mode: 'live' | 'sample';
  agentsCount: number;
  teamsCount: number;
  runsCount: number;
  onClose: () => void;
}

export const SourceModal: React.FC<Props> = ({
  mode,
  agentsCount,
  teamsCount,
  runsCount,
  onClose,
}) => {
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

        <h3 className="text-lg font-bold text-[var(--text)] tracking-tight mb-3">
          Workspace Source Details
        </h3>

        <div className="flex flex-col gap-3 text-xs text-[var(--muted)] leading-relaxed">
          <div className="p-3 rounded-xl bg-[var(--card2)] border border-[var(--line)]">
            <div className="text-[11px] text-[var(--dim)] mb-1">Execution Mode</div>
            <div className="text-sm font-semibold text-[var(--text)] capitalize">
              {mode === 'sample' ? 'Sample (Demonstration Data)' : 'Live (Local Workspace)'}
            </div>
          </div>

          <p>
            Operating with <b>{agentsCount}</b> people across <b>{teamsCount}</b> teams and <b>{runsCount}</b> logged run receipts.
          </p>

          <div className="flex items-start gap-2.5 p-3 rounded-xl bg-[var(--green)]/10 border border-[var(--green)]/30 text-[var(--green)]">
            <ShieldCheck size={18} className="flex-none mt-0.5" />
            <span className="text-xs">
              Zero cloud telemetry leakage. All agent contracts, LLM calls, and execution receipts remain strictly localized to your workspace instance.
            </span>
          </div>
        </div>

        <div className="flex justify-end pt-4 mt-2 border-t border-[var(--line)]">
          <button onClick={onClose} className="os-btn os-btn-sm">
            Close
          </button>
        </div>
      </div>
    </div>
  );
};
