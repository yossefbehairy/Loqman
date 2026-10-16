import React, { useState } from 'react';
import {
  X,
  StopCircle,
  MessageSquare,
  Activity,
  Layers,
  CheckCircle2,
  AlertOctagon,
  Clock,
  Loader2,
  Copy,
  Check,
} from 'lucide-react';
import type { WorkflowExecution, TeamMessage } from '../../types';
import { TeamChatPanel } from './TeamChatPanel';

interface ExecutionDrawerProps {
  execution: WorkflowExecution | null;
  teamMessages: TeamMessage[];
  isOpen: boolean;
  onClose: () => void;
  onCancelExecution: (executionId: string) => void;
  selectedNodeId?: string | null;
}

export const ExecutionDrawer: React.FC<ExecutionDrawerProps> = ({
  execution,
  teamMessages,
  isOpen,
  onClose,
  onCancelExecution,
  selectedNodeId,
}) => {
  const [activeTab, setActiveTab] = useState<'chat' | 'nodes'>('chat');
  const [copied, setCopied] = useState<string | null>(null);
  const [inspectNodeId, setInspectNodeId] = useState<string | null>(selectedNodeId || null);

  if (!isOpen || !execution) return null;

  const isRunning = execution.status === 'running' || execution.status === 'pending';

  const copyToClipboard = (text: string, key: string) => {
    navigator.clipboard.writeText(text);
    setCopied(key);
    setTimeout(() => setCopied(null), 2000);
  };

  const selectedNodeExec = execution.node_executions?.find(
    (n) => n.id === inspectNodeId || n.node_id === inspectNodeId
  ) || execution.node_executions?.[0];

  return (
    <div className="fixed bottom-0 right-0 w-[540px] h-[580px] bg-gray-900/98 backdrop-blur-xl border-l border-t border-gray-800 shadow-2xl flex flex-col z-30 transition-all rounded-tl-2xl overflow-hidden">
      {/* Top Bar */}
      <div className="px-5 py-3.5 bg-gray-950/80 border-b border-gray-800 flex items-center justify-between">
        <div className="flex items-center space-x-3">
          <div className="p-1.5 bg-indigo-500/20 text-indigo-400 rounded-lg">
            <Activity className="w-4 h-4" />
          </div>
          <div>
            <div className="flex items-center space-x-2">
              <h3 className="text-sm font-bold text-gray-100">Workflow Execution</h3>
              <span
                className={`text-[10px] uppercase font-bold px-2 py-0.5 rounded-full border ${
                  execution.status === 'succeeded'
                    ? 'bg-emerald-950 text-emerald-300 border-emerald-800'
                    : execution.status === 'running'
                    ? 'bg-blue-950 text-blue-300 border-blue-800 animate-pulse'
                    : execution.status === 'failed'
                    ? 'bg-rose-950 text-rose-300 border-rose-800'
                    : 'bg-gray-800 text-gray-400 border-gray-700'
                }`}
              >
                {execution.status}
              </span>
            </div>
            <span className="text-[10px] text-gray-500 font-mono">
              ID: {execution.id.slice(0, 18)}...
            </span>
          </div>
        </div>

        <div className="flex items-center space-x-2">
          {isRunning && (
            <button
              onClick={() => onCancelExecution(execution.id)}
              className="inline-flex items-center space-x-1 px-3 py-1.5 rounded-lg bg-rose-600/20 hover:bg-rose-600/30 text-rose-300 border border-rose-500/40 text-xs font-semibold transition"
            >
              <StopCircle className="w-3.5 h-3.5" />
              <span>Cancel Run</span>
            </button>
          )}

          <button
            onClick={onClose}
            className="p-1.5 rounded-lg text-gray-400 hover:text-gray-200 hover:bg-gray-800 transition"
          >
            <X className="w-4 h-4" />
          </button>
        </div>
      </div>

      {/* Tabs */}
      <div className="flex border-b border-gray-800 bg-gray-950/40 px-4 pt-1">
        <button
          onClick={() => setActiveTab('chat')}
          className={`flex items-center space-x-2 px-3 py-2 text-xs font-semibold border-b-2 transition ${
            activeTab === 'chat'
              ? 'border-indigo-500 text-indigo-400'
              : 'border-transparent text-gray-400 hover:text-gray-200'
          }`}
        >
          <MessageSquare className="w-3.5 h-3.5" />
          <span>Team Chat Stream</span>
          <span className="text-[10px] bg-gray-800 px-1.5 py-0.2 rounded-full">
            {teamMessages.length}
          </span>
        </button>

        <button
          onClick={() => setActiveTab('nodes')}
          className={`flex items-center space-x-2 px-3 py-2 text-xs font-semibold border-b-2 transition ${
            activeTab === 'nodes'
              ? 'border-indigo-500 text-indigo-400'
              : 'border-transparent text-gray-400 hover:text-gray-200'
          }`}
        >
          <Layers className="w-3.5 h-3.5" />
          <span>Node Outputs</span>
        </button>
      </div>

      {/* Tab Content */}
      <div className="flex-1 overflow-hidden flex flex-col">
        {activeTab === 'chat' ? (
          <TeamChatPanel
            messages={teamMessages}
            onSelectNodeOutput={(refId) => {
              setInspectNodeId(refId);
              setActiveTab('nodes');
            }}
          />
        ) : (
          <div className="flex h-full">
            {/* Left Node Execution List */}
            <div className="w-48 border-r border-gray-800 overflow-y-auto p-2 space-y-1.5 bg-gray-950/60">
              <span className="text-[10px] font-bold text-gray-500 uppercase px-2">Pipeline Stages</span>
              {execution.node_executions?.map((ne) => {
                const isSelected = (selectedNodeExec?.id === ne.id) || (selectedNodeExec?.node_id === ne.node_id);
                return (
                  <button
                    key={ne.id}
                    onClick={() => setInspectNodeId(ne.id)}
                    className={`w-full text-left px-2.5 py-2 rounded-lg text-xs transition flex items-center justify-between ${
                      isSelected
                        ? 'bg-indigo-600/30 text-indigo-200 border border-indigo-500/50'
                        : 'bg-gray-900/60 hover:bg-gray-800/80 text-gray-300'
                    }`}
                  >
                    <div className="truncate">
                      <div className="font-semibold truncate capitalize">{ne.role_type || ne.node_id}</div>
                      <div className="text-[10px] text-gray-500 truncate font-mono">{ne.node_id}</div>
                    </div>
                    {ne.status === 'succeeded' && <CheckCircle2 className="w-3.5 h-3.5 text-emerald-400" />}
                    {ne.status === 'running' && <Loader2 className="w-3.5 h-3.5 text-blue-400 animate-spin" />}
                    {ne.status === 'failed' && <AlertOctagon className="w-3.5 h-3.5 text-rose-400" />}
                    {ne.status === 'pending' && <Clock className="w-3.5 h-3.5 text-gray-600" />}
                  </button>
                );
              })}
            </div>

            {/* Right Node Output Detail */}
            <div className="flex-1 overflow-y-auto p-4 space-y-3 bg-gray-950">
              {selectedNodeExec ? (
                <>
                  <div className="flex items-center justify-between pb-2 border-b border-gray-800">
                    <div>
                      <h4 className="text-xs font-bold text-gray-200 capitalize">
                        {selectedNodeExec.role_type || selectedNodeExec.node_id} Output
                      </h4>
                      <p className="text-[10px] text-gray-500 font-mono">
                        Status: {selectedNodeExec.status} | Duration: {selectedNodeExec.duration_ms || 0}ms | Attempts: {selectedNodeExec.attempt_count}
                      </p>
                    </div>

                    {selectedNodeExec.output_json && (
                      <button
                        onClick={() => copyToClipboard(selectedNodeExec.output_json || '', 'output')}
                        className="inline-flex items-center space-x-1 px-2 py-1 rounded bg-gray-800 hover:bg-gray-700 text-gray-300 text-[10px] transition"
                      >
                        {copied === 'output' ? <Check className="w-3 h-3 text-emerald-400" /> : <Copy className="w-3 h-3" />}
                        <span>{copied === 'output' ? 'Copied' : 'Copy JSON'}</span>
                      </button>
                    )}
                  </div>

                  {selectedNodeExec.error_message && (
                    <div className="p-2.5 rounded bg-rose-950/40 border border-rose-800 text-xs text-rose-300 font-mono">
                      Error: {selectedNodeExec.error_message}
                    </div>
                  )}

                  {selectedNodeExec.output_json ? (
                    <pre className="p-3 bg-gray-900 border border-gray-800 rounded-lg text-[11px] font-mono text-emerald-300 overflow-x-auto max-h-[360px] leading-relaxed">
                      {(() => {
                        try {
                          return JSON.stringify(JSON.parse(selectedNodeExec.output_json), null, 2);
                        } catch {
                          return selectedNodeExec.output_json;
                        }
                      })()}
                    </pre>
                  ) : (
                    <div className="text-xs text-gray-500 italic py-8 text-center">
                      No output recorded yet for this stage.
                    </div>
                  )}
                </>
              ) : (
                <div className="text-xs text-gray-500 text-center py-10">Select a node to inspect output</div>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
};
