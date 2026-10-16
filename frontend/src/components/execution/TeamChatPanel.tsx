import React from 'react';
import {
  MessageSquare,
  Brain,
  BarChart3,
  ShieldCheck,
  Bot,
  Play,
  Flag,
  ArrowRight,
  ExternalLink,
} from 'lucide-react';
import type { TeamMessage } from '../../types';

interface TeamChatPanelProps {
  messages: TeamMessage[];
  onSelectNodeOutput?: (nodeExecutionId: string) => void;
}

const ROLE_BADGES: Record<string, { bg: string; text: string; icon: React.ReactNode }> = {
  start: { bg: 'bg-emerald-950/80 border-emerald-800', text: 'text-emerald-400', icon: <Play className="w-3.5 h-3.5 fill-emerald-400" /> },
  planner: { bg: 'bg-indigo-950/80 border-indigo-800', text: 'text-indigo-400', icon: <Brain className="w-3.5 h-3.5 text-indigo-400" /> },
  analyst: { bg: 'bg-blue-950/80 border-blue-800', text: 'text-blue-400', icon: <BarChart3 className="w-3.5 h-3.5 text-blue-400" /> },
  reviewer: { bg: 'bg-amber-950/80 border-amber-800', text: 'text-amber-400', icon: <ShieldCheck className="w-3.5 h-3.5 text-amber-400" /> },
  output: { bg: 'bg-purple-950/80 border-purple-800', text: 'text-purple-400', icon: <Flag className="w-3.5 h-3.5 text-purple-400" /> },
  custom: { bg: 'bg-violet-950/80 border-violet-800', text: 'text-violet-400', icon: <Bot className="w-3.5 h-3.5 text-violet-400" /> },
};

export const TeamChatPanel: React.FC<TeamChatPanelProps> = ({ messages, onSelectNodeOutput }) => {
  return (
    <div className="flex flex-col h-full bg-gray-950 text-gray-200">
      {/* Subheader */}
      <div className="px-4 py-2.5 bg-gray-900/60 border-b border-gray-800/80 flex items-center justify-between">
        <div className="flex items-center space-x-2">
          <MessageSquare className="w-4 h-4 text-indigo-400" />
          <span className="text-xs font-semibold text-gray-200">Live Team Chat & Handover Stream</span>
        </div>
        <span className="text-[11px] font-mono text-gray-400 bg-gray-800/80 px-2 py-0.5 rounded-full">
          {messages.length} message{messages.length === 1 ? '' : 's'}
        </span>
      </div>

      {/* Messages List */}
      <div className="flex-1 overflow-y-auto p-4 space-y-4">
        {messages.length === 0 ? (
          <div className="h-40 flex flex-col items-center justify-center text-center text-gray-500">
            <MessageSquare className="w-8 h-8 mb-2 opacity-30" />
            <p className="text-xs">No team messages recorded yet.</p>
            <p className="text-[11px] text-gray-600 mt-1">
              Start workflow execution to observe real-time agent handovers.
            </p>
          </div>
        ) : (
          messages.map((msg, idx) => {
            const roleKey = (msg.from_role || 'custom').toLowerCase();
            const badge = ROLE_BADGES[roleKey] || ROLE_BADGES.custom;

            return (
              <div
                key={`${msg.execution_id}-${msg.turn}-${idx}`}
                className="rounded-xl border border-gray-800 bg-gray-900/90 p-3.5 shadow-lg space-y-2.5 transition hover:border-gray-700"
              >
                {/* Header: Turn + Role + Timestamp */}
                <div className="flex items-center justify-between">
                  <div className="flex items-center space-x-2">
                    <span className="text-[10px] font-bold font-mono px-1.5 py-0.5 rounded bg-gray-800 text-gray-300 border border-gray-700">
                      Turn {msg.turn}
                    </span>
                    <div
                      className={`inline-flex items-center space-x-1 px-2 py-0.5 rounded-full border text-xs font-semibold ${badge.bg} ${badge.text}`}
                    >
                      {badge.icon}
                      <span className="capitalize">{msg.from_display_name || msg.from_role}</span>
                    </div>
                  </div>

                  <span className="text-[10px] text-gray-500 font-mono">
                    {new Date(msg.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })}
                  </span>
                </div>

                {/* Message Body / Handover Excerpt */}
                <div className="text-xs text-gray-200 leading-relaxed bg-gray-950/60 p-2.5 rounded-lg border border-gray-800/60">
                  {msg.excerpt}
                </div>

                {/* Footer: Recipients + View Full Output */}
                <div className="flex items-center justify-between pt-1 border-t border-gray-800/40 text-[10px] text-gray-400">
                  <div className="flex items-center space-x-1">
                    <ArrowRight className="w-3 h-3 text-indigo-400" />
                    <span>
                      Handed off to: {msg.to_node_ids && msg.to_node_ids.length > 0 ? msg.to_node_ids.join(', ') : 'Pipeline Complete'}
                    </span>
                  </div>

                  {msg.full_output_ref && onSelectNodeOutput && (
                    <button
                      onClick={() => onSelectNodeOutput(msg.full_output_ref)}
                      className="inline-flex items-center space-x-1 text-indigo-400 hover:text-indigo-300 font-medium"
                    >
                      <span>Inspect JSON</span>
                      <ExternalLink className="w-2.5 h-2.5" />
                    </button>
                  )}
                </div>
              </div>
            );
          })
        )}
      </div>
    </div>
  );
};
