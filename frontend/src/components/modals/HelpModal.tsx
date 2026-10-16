import React from 'react';
import { X, Check } from 'lucide-react';

interface Props {
  onClose: () => void;
}

export const HelpModal: React.FC<Props> = ({ onClose }) => {
  return (
    <div className="fixed inset-0 bg-black/60 backdrop-blur-sm flex items-center justify-center p-4 z-50">
      <div className="os-card w-full max-w-lg p-5 relative animate-in fade-in zoom-in-95 duration-150">
        <button
          onClick={onClose}
          className="absolute top-4 right-4 p-1 text-[var(--muted)] hover:text-[var(--text)] transition-colors"
          title="Close modal"
        >
          <X size={18} />
        </button>

        <h3 className="text-lg font-bold text-[var(--text)] tracking-tight mb-3">
          How to Read the Company Graph
        </h3>

        <div className="flex flex-col gap-3 text-xs text-[var(--muted)] leading-relaxed">
          <p>
            The central node represents the <b>CEO</b>. Each orbital node represents a department <b>Manager</b>, accompanied by their uppercase department label.
          </p>
          <p>
            Smaller satellite nodes circling a manager are that department's <b>Specialists</b>.
          </p>
          <p>
            <b>Curved lines</b> indicate reporting hierarchy and inter-agent communication channels. Toggle <b>Evidence trails</b> in Options to illuminate all links simultaneously. Hovering over any node dynamically traces its active branch.
          </p>
          <p>
            Clicking any person opens their detailed <b>Contract & Summary</b> card. Nodes highlighted with a dashed ring signify <b>Needs attention</b>.
          </p>
          <p>
            <b>Navigation:</b> Drag canvas to orbit/pan camera, scroll mouse wheel to zoom in/out, and click <b>Reset camera</b> to re-center. Click department focus chips to bring specific teams into close view.
          </p>
        </div>

        <div className="flex justify-end pt-4 mt-2 border-t border-[var(--line)]">
          <button onClick={onClose} className="os-btn os-btn-sm">
            <Check size={14} />
            Got it
          </button>
        </div>
      </div>
    </div>
  );
};
