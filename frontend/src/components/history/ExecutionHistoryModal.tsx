import React from 'react';
import { X, ArrowRight, Layers } from 'lucide-react';
import type { WorkflowExecution } from '../../types';

interface ExecutionHistoryModalProps {
  isOpen: boolean;
  onClose: () => void;
  executions: WorkflowExecution[];
  onSelectExecution: (execution: WorkflowExecution) => void;
}

export const ExecutionHistoryModal: React.FC<ExecutionHistoryModalProps> = ({
  isOpen,
  onClose,
  executions,
  onSelectExecution,
}) => {
  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 bg-black/75 backdrop-blur-sm flex items-center justify-center p-4">
      <div className="bg-gray-900 border border-gray-800 rounded-2xl w-full max-w-2xl max-h-[80vh] flex flex-col shadow-2xl overflow-hidden">
        {/* Header */}
        <div className="px-6 py-4 border-b border-gray-800 flex items-center justify-between bg-gray-950/60">
          <div className="flex items-center space-x-2">
            <Layers className="w-5 h-5 text-indigo-400" />
            <h3 className="text-base font-bold text-white">Execution History (SQLite)</h3>
          </div>
          <button
            onClick={onClose}
            className="p-1.5 rounded-lg text-gray-400 hover:text-gray-200 hover:bg-gray-800 transition"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* List */}
        <div className="flex-1 overflow-y-auto p-6 space-y-3">
          {executions.length === 0 ? (
            <div className="text-center py-12 text-gray-500 text-sm">
              No executions recorded yet. Run a workflow to view history.
            </div>
          ) : (
            executions.map((ex) => (
              <div
                key={ex.id}
                className="p-4 rounded-xl border border-gray-800/80 bg-gray-950/60 hover:border-gray-700 transition flex items-center justify-between"
              >
                <div className="space-y-1">
                  <div className="flex items-center space-x-2">
                    <span
                      className={`text-[10px] uppercase font-bold px-2 py-0.5 rounded-full border ${
                        ex.status === 'succeeded'
                          ? 'bg-emerald-950 text-emerald-300 border-emerald-800'
                          : ex.status === 'running'
                          ? 'bg-blue-950 text-blue-300 border-blue-800'
                          : ex.status === 'failed'
                          ? 'bg-rose-950 text-rose-300 border-rose-800'
                          : 'bg-gray-800 text-gray-400 border-gray-700'
                      }`}
                    >
                      {ex.status}
                    </span>
                    <span className="text-xs font-mono text-gray-400 font-semibold">
                      {ex.id.slice(0, 18)}...
                    </span>
                  </div>

                  <p className="text-xs text-gray-400">
                    Started:{' '}
                    {ex.started_at ? new Date(ex.started_at).toLocaleString() : 'N/A'}
                  </p>

                  <div className="text-[11px] text-gray-500 font-mono">
                    Stages completed: {ex.node_executions?.filter((n) => n.status === 'succeeded').length || 0} / {ex.node_executions?.length || 0}
                  </div>
                </div>

                <button
                  onClick={() => {
                    onSelectExecution(ex);
                    onClose();
                  }}
                  className="inline-flex items-center space-x-1.5 px-3 py-1.5 rounded-lg bg-indigo-600/20 hover:bg-indigo-600/30 text-indigo-300 border border-indigo-500/30 text-xs font-semibold transition"
                >
                  <span>Replay / Inspect</span>
                  <ArrowRight className="w-3.5 h-3.5" />
                </button>
              </div>
            ))
          )}
        </div>
      </div>
    </div>
  );
};
