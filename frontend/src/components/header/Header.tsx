import React, { useState } from 'react';
import {
  Play,
  CheckCircle2,
  AlertTriangle,
  Save,
  Sparkles,
  Server,
  Layers,
  History,
  Plus,
} from 'lucide-react';
import type { RuntimeHealth, ValidationResult } from '../../types';

interface HeaderProps {
  workflowName: string;
  onUpdateWorkflowName: (name: string) => void;
  runtimeHealth: RuntimeHealth | null;
  onValidate: () => Promise<void>;
  onRun: () => Promise<void>;
  onSave: () => Promise<void>;
  onLoadDemo: () => void;
  onNewWorkflow: () => void;
  onOpenHistory: () => void;
  isValidating: boolean;
  isRunning: boolean;
  validationResult: ValidationResult | null;
}

export const Header: React.FC<HeaderProps> = ({
  workflowName,
  onUpdateWorkflowName,
  runtimeHealth,
  onValidate,
  onRun,
  onSave,
  onLoadDemo,
  onNewWorkflow,
  onOpenHistory,
  isValidating,
  isRunning,
  validationResult,
}) => {
  const [isEditingName, setIsEditingName] = useState(false);

  return (
    <header className="h-16 border-b border-gray-800 bg-gray-900/90 backdrop-blur-md px-6 flex items-center justify-between z-10">
      {/* Brand & Workflow Name */}
      <div className="flex items-center space-x-6">
        <div className="flex items-center space-x-2.5">
          <div className="w-8 h-8 rounded-xl bg-gradient-to-tr from-indigo-600 via-indigo-500 to-emerald-400 flex items-center justify-center shadow-lg shadow-indigo-500/20">
            <Sparkles className="w-4 h-4 text-white" />
          </div>
          <div>
            <div className="flex items-center space-x-1.5">
              <span className="font-extrabold text-sm tracking-tight text-white">AgentOS</span>
              <span className="text-[10px] font-bold px-1.5 py-0.2 rounded bg-indigo-500/20 text-indigo-400 border border-indigo-500/30">
                Studio
              </span>
            </div>
            <span className="text-[10px] text-gray-400 block -mt-0.5">Local-First Multi-Agent Engine</span>
          </div>
        </div>

        <div className="h-6 w-[1px] bg-gray-800" />

        {/* Workflow Title Editor */}
        <div className="flex items-center space-x-2">
          {isEditingName ? (
            <input
              type="text"
              autoFocus
              className="bg-gray-950 border border-indigo-500/80 rounded-lg px-2.5 py-1 text-sm font-semibold text-white focus:outline-none"
              value={workflowName}
              onChange={(e) => onUpdateWorkflowName(e.target.value)}
              onBlur={() => setIsEditingName(false)}
              onKeyDown={(e) => e.key === 'Enter' && setIsEditingName(false)}
            />
          ) : (
            <button
              onClick={() => setIsEditingName(true)}
              className="text-sm font-semibold text-gray-200 hover:text-white hover:bg-gray-800/80 px-2 py-1 rounded-lg transition"
              title="Click to rename workflow"
            >
              {workflowName}
            </button>
          )}
        </div>
      </div>

      {/* Runtime Health Pill */}
      <div className="hidden lg:flex items-center space-x-3">
        <div className="flex items-center space-x-2 px-3 py-1.5 rounded-full bg-gray-950 border border-gray-800 text-xs">
          <div
            className={`w-2 h-2 rounded-full ${
              runtimeHealth?.status === 'ok' ? 'bg-emerald-400 animate-pulse' : 'bg-rose-400'
            }`}
          />
          <Server className="w-3.5 h-3.5 text-gray-400" />
          <span className="text-gray-300 font-medium">OpenCode:</span>
          <span className="text-gray-200 font-mono text-[11px]">
            {runtimeHealth?.status === 'ok' ? 'Online' : 'Offline'}
          </span>
          <span className="text-gray-600">|</span>
          <span className="text-emerald-400 font-mono text-[11px]">
            {runtimeHealth?.free_models_count || 1} Free Model
          </span>
        </div>
      </div>

      {/* Action Controls */}
      <div className="flex items-center space-x-2.5">
        {/* Load Capstone Demo Button */}
        <button
          onClick={onLoadDemo}
          className="inline-flex items-center space-x-1.5 px-3 py-1.5 rounded-lg bg-gray-800 hover:bg-gray-700 text-gray-200 text-xs font-semibold border border-gray-700/80 transition shadow-sm"
          title="Load University Capstone Demo (Planner -> Analyst -> Reviewer)"
        >
          <Layers className="w-3.5 h-3.5 text-indigo-400" />
          <span>Demo Chain</span>
        </button>

        <button
          onClick={onNewWorkflow}
          className="inline-flex items-center space-x-1 px-2.5 py-1.5 rounded-lg bg-gray-800/60 hover:bg-gray-700 text-gray-300 text-xs font-medium border border-gray-800 transition"
          title="Create New Blank Workflow"
        >
          <Plus className="w-3.5 h-3.5" />
          <span>New</span>
        </button>

        <button
          onClick={onSave}
          className="inline-flex items-center space-x-1.5 px-3 py-1.5 rounded-lg bg-gray-800 hover:bg-gray-700 text-gray-200 text-xs font-semibold border border-gray-700/80 transition"
        >
          <Save className="w-3.5 h-3.5 text-emerald-400" />
          <span>Save</span>
        </button>

        <button
          onClick={onOpenHistory}
          className="inline-flex items-center space-x-1.5 px-3 py-1.5 rounded-lg bg-gray-800 hover:bg-gray-700 text-gray-200 text-xs font-semibold border border-gray-700/80 transition"
        >
          <History className="w-3.5 h-3.5 text-cyan-400" />
          <span>History</span>
        </button>

        <div className="h-5 w-[1px] bg-gray-800 mx-1" />

        {/* Validate Graph Button */}
        <button
          onClick={onValidate}
          disabled={isValidating || isRunning}
          className={`inline-flex items-center space-x-1.5 px-3.5 py-1.5 rounded-lg text-xs font-semibold border transition ${
            validationResult?.valid
              ? 'bg-emerald-950/60 text-emerald-300 border-emerald-700/60 hover:bg-emerald-900/60'
              : validationResult?.errors && validationResult.errors.length > 0
              ? 'bg-rose-950/60 text-rose-300 border-rose-700/60 hover:bg-rose-900/60'
              : 'bg-gray-800 hover:bg-gray-700 text-gray-200 border-gray-700'
          }`}
        >
          {validationResult?.valid ? (
            <CheckCircle2 className="w-3.5 h-3.5 text-emerald-400" />
          ) : validationResult?.errors && validationResult.errors.length > 0 ? (
            <AlertTriangle className="w-3.5 h-3.5 text-rose-400" />
          ) : (
            <CheckCircle2 className="w-3.5 h-3.5 text-indigo-400" />
          )}
          <span>{isValidating ? 'Validating...' : 'Validate DAG'}</span>
        </button>

        {/* Run Workflow Button */}
        <button
          onClick={onRun}
          disabled={isRunning}
          className={`inline-flex items-center space-x-2 px-4 py-1.5 rounded-lg text-xs font-bold shadow-lg transition ${
            isRunning
              ? 'bg-blue-600/50 text-blue-200 cursor-not-allowed shadow-blue-500/10'
              : 'bg-gradient-to-r from-indigo-600 to-indigo-500 hover:from-indigo-500 hover:to-indigo-400 text-white shadow-indigo-500/25'
          }`}
        >
          <Play className={`w-3.5 h-3.5 fill-white ${isRunning ? 'animate-pulse' : ''}`} />
          <span>{isRunning ? 'Running...' : 'Run Pipeline'}</span>
        </button>
      </div>
    </header>
  );
};
