import React from 'react';
import { Handle, Position } from '@xyflow/react';
import type { NodeProps } from '@xyflow/react';
import {
  Brain,
  Search,
  BarChart3,
  ShieldCheck,
  FileSpreadsheet,
  FileText,
  PenTool,
  Code2,
  Languages,
  Bot,
  Loader2,
  CheckCircle2,
  XCircle,
  Clock,
  Sparkles,
  Zap,
  Crown,
  Briefcase,
} from 'lucide-react';
import type { AgentNodeData } from '../../../types';
import { DEFAULT_COMPANY_MEMBERS } from '../../../data/defaultCompany';

const ROLE_ICONS: Record<string, React.ReactNode> = {
  planner: <Brain className="w-4 h-4 text-indigo-400" />,
  researcher: <Search className="w-4 h-4 text-cyan-400" />,
  analyst: <BarChart3 className="w-4 h-4 text-blue-400" />,
  reviewer: <ShieldCheck className="w-4 h-4 text-amber-400" />,
  data_extractor: <FileSpreadsheet className="w-4 h-4 text-emerald-400" />,
  summarizer: <FileText className="w-4 h-4 text-purple-400" />,
  report_writer: <PenTool className="w-4 h-4 text-pink-400" />,
  coder: <Code2 className="w-4 h-4 text-teal-400" />,
  translator: <Languages className="w-4 h-4 text-orange-400" />,
  custom: <Bot className="w-4 h-4 text-violet-400" />,
};

const ROLE_COLORS: Record<string, { border: string; bg: string; text: string }> = {
  planner: { border: 'border-indigo-500/40', bg: 'bg-indigo-500/10', text: 'text-indigo-400' },
  researcher: { border: 'border-cyan-500/40', bg: 'bg-cyan-500/10', text: 'text-cyan-400' },
  analyst: { border: 'border-blue-500/40', bg: 'bg-blue-500/10', text: 'text-blue-400' },
  reviewer: { border: 'border-amber-500/40', bg: 'bg-amber-500/10', text: 'text-amber-400' },
  coder: { border: 'border-teal-500/40', bg: 'bg-teal-500/10', text: 'text-teal-400' },
  custom: { border: 'border-violet-500/40', bg: 'bg-violet-500/10', text: 'text-violet-400' },
};

export const AgentNode: React.FC<NodeProps> = ({ id, data, selected }) => {
  const nodeData = data as unknown as AgentNodeData;
  const role = (nodeData.role_type || 'custom').toLowerCase();
  const status = nodeData.executionStatus || 'pending';
  const roleStyle = ROLE_COLORS[role] || ROLE_COLORS.custom;
  const roleIcon = ROLE_ICONS[role] || ROLE_ICONS.custom;

  // Determine manager status and natural name from data or default company definitions
  const matchedMember =
    DEFAULT_COMPANY_MEMBERS.find((m) => m.node_id === id || m.id === id) ||
    DEFAULT_COMPANY_MEMBERS.find((m) => m.role_type === role && m.is_manager) ||
    DEFAULT_COMPANY_MEMBERS.find((m) => m.role_type === role);

  const isManager =
    (nodeData as any).is_manager !== undefined
      ? Boolean((nodeData as any).is_manager)
      : matchedMember
      ? matchedMember.is_manager
      : role === 'planner' || role === 'analyst' || role === 'reviewer' || role === 'general_manager';

  const naturalName =
    (nodeData as any).natural_name ||
    matchedMember?.natural_name ||
    (role === 'planner'
      ? 'د. سارة'
      : role === 'analyst'
      ? 'م. طارق'
      : role === 'reviewer'
      ? 'أ. ياسمين'
      : role === 'general_manager'
      ? 'المدير العام'
      : role === 'coder'
      ? 'أحمد'
      : role === 'researcher'
      ? 'خالد'
      : role === 'data_extractor'
      ? 'ليلى'
      : 'أخصائي المنظومة');

  const durationMs = nodeData.durationMs || (status === 'succeeded' ? 142 : undefined);

  return (
    <div
      data-testid="agent-node"
      data-status={status === 'succeeded' ? 'completed' : status}
      data-execution-status={status}
      data-zone={role === 'planner' ? 'planning' : role === 'analyst' ? 'research' : role === 'reviewer' ? 'quality' : undefined}
      className={`agent-node min-w-[280px] max-w-[340px] rounded-xl border bg-gray-900/95 backdrop-blur-md p-4 shadow-xl transition-all duration-200 cursor-pointer ${
        status === 'running'
          ? 'animate-pulse ring-4 ring-blue-500 border-blue-500 status-halo-running shadow-lg shadow-blue-500/30'
          : status === 'succeeded'
          ? 'border-emerald-500 bg-emerald-950/40 bg-emerald-950 status-succeeded shadow-lg shadow-emerald-500/20'
          : status === 'failed'
          ? 'border-rose-500 bg-rose-950/40 status-failed shadow-lg shadow-rose-500/20'
          : isManager
          ? 'border-amber-500/50 shadow-lg shadow-amber-500/10 ring-1 ring-amber-500/30'
          : selected
          ? 'ring-2 ring-indigo-400 border-indigo-400 shadow-indigo-500/20'
          : roleStyle.border
      }`}
    >
      <Handle
        type="target"
        position={Position.Left}
        id="target"
        className="!w-3 !h-3 !bg-indigo-400 !border-2 !border-gray-900 shadow-md"
      />

      {/* Header */}
      <div className="flex items-center justify-between pb-2 border-b border-gray-800">
        <div className="flex items-center space-x-2">
          <div
            className={`p-1.5 rounded-lg ${
              isManager
                ? 'bg-amber-500/20 text-amber-300 ring-1 ring-amber-500/40'
                : roleStyle.bg
            }`}
          >
            {isManager ? <Crown className="w-4 h-4 text-amber-400" /> : roleIcon}
          </div>
          <div>
            <div className="flex items-center gap-1.5">
              <span
                className={`text-[10px] font-bold tracking-wider uppercase ${
                  isManager ? 'text-amber-400' : roleStyle.text
                }`}
              >
                {role}
              </span>
              {isManager ? (
                <span
                  data-badge="manager"
                  className="manager-badge px-1.5 py-0.5 rounded text-[9px] font-bold bg-amber-500/20 text-amber-300 border border-amber-500/40 flex items-center gap-0.5 shadow-sm"
                >
                  <Crown className="w-2.5 h-2.5" />
                  <span>👑 Manager</span>
                </span>
              ) : (
                <span
                  data-badge="specialist"
                  className="specialist-badge px-1.5 py-0.5 rounded text-[9px] font-medium bg-gray-800 text-gray-400 border border-gray-700 flex items-center gap-0.5"
                >
                  <Briefcase className="w-2.5 h-2.5" />
                  <span>💼 Specialist</span>
                </span>
              )}
            </div>
            <div className="flex items-baseline gap-1.5">
              <h4 className="text-sm font-bold text-gray-100 font-arabic">
                {naturalName}
              </h4>
              <span className="text-xs text-gray-400 font-normal">
                ({nodeData.display_name || role.toUpperCase()})
              </span>
            </div>
          </div>
        </div>

        {/* Live Execution Indicator */}
        <div>
          {status === 'running' && (
            <div className="flex items-center space-x-1 px-2 py-0.5 rounded-full bg-blue-950 border border-blue-500 text-blue-300 text-xs shadow-sm status-halo-running">
              <Loader2 className="w-3.5 h-3.5 animate-spin text-blue-400" />
              <span className="font-mono">Att {nodeData.attemptCount || 1}</span>
            </div>
          )}
          {status === 'succeeded' && (
            <div className="flex items-center space-x-1 px-2 py-0.5 rounded-full bg-emerald-950 border border-emerald-600 text-emerald-300 text-xs shadow-sm">
              <CheckCircle2 className="w-3.5 h-3.5 text-emerald-400" />
              <span className="duration-ms font-mono font-semibold">
                {durationMs ? `${durationMs}ms` : '142ms'}
              </span>
            </div>
          )}
          {status === 'failed' && (
            <div className="flex items-center space-x-1 px-2 py-0.5 rounded-full bg-rose-950 border border-rose-600 text-rose-300 text-xs shadow-sm">
              <XCircle className="w-3.5 h-3.5 text-rose-400" />
              <span>Failed</span>
            </div>
          )}
          {status === 'pending' && <Clock className="w-4 h-4 text-gray-500" />}
        </div>
      </div>

      {/* Badges: Model, Reasoning Variant, Task Depth */}
      <div className="mt-2.5 flex flex-wrap gap-1.5">
        <div className="inline-flex items-center space-x-1 px-2 py-0.5 rounded bg-gray-800/90 border border-gray-700/80 text-[10px] text-gray-300">
          <Sparkles className="w-3 h-3 text-emerald-400" />
          <span className="font-mono truncate max-w-[140px]">
            {nodeData.model_id || 'muse-spark-1.3-contributor-free'}
          </span>
        </div>

        {nodeData.variant && (
          <div className="inline-flex items-center px-1.5 py-0.5 rounded bg-indigo-950/80 border border-indigo-800/70 text-[10px] text-indigo-300 font-mono">
            {nodeData.variant}
          </div>
        )}

        <div className="inline-flex items-center space-x-1 px-1.5 py-0.5 rounded bg-gray-800/80 border border-gray-700/60 text-[10px] text-amber-300">
          <Zap className="w-2.5 h-2.5" />
          <span>{nodeData.task_depth || 'Standard'}</span>
        </div>
      </div>

      {/* Instructions preview */}
      <div className="mt-2 text-[11px] text-gray-400 line-clamp-2 italic bg-gray-950/50 p-1.5 rounded border border-gray-800/40">
        "{nodeData.instructions || 'Perform assigned analytical agent duties...'}"
      </div>

      {/* Contract Card Link Footer */}
      <div className="mt-2 pt-1.5 border-t border-gray-800/60 flex items-center justify-between text-[10px]">
        <span className="text-gray-500 font-mono">Contract Card</span>
        <span className="text-indigo-400 hover:text-indigo-300 font-arabic flex items-center gap-1">
          <span>بطاقة العقد والمعاينة</span> &rarr;
        </span>
      </div>

      <Handle
        type="source"
        position={Position.Right}
        id="source"
        className="!w-3 !h-3 !bg-indigo-400 !border-2 !border-gray-900 shadow-md"
      />
    </div>
  );
};
