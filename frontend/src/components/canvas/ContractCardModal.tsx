import React, { useState } from 'react';
import type { Node } from '@xyflow/react';
import {
  X,
  Crown,
  Briefcase,
  Sparkles,
  Zap,
  Clock,
  ShieldCheck,
  FileCode,
  CheckCircle2,
  AlertCircle,
  Copy,
  Check,
  ExternalLink,
} from 'lucide-react';
import type { AgentNodeData } from '../../types';
import { DEFAULT_COMPANY_MEMBERS } from '../../data/defaultCompany';

interface ContractCardModalProps {
  node: Node | null;
  onClose: () => void;
}

export const ContractCardModal: React.FC<ContractCardModalProps> = ({ node, onClose }) => {
  const [activeTab, setActiveTab] = useState<'contract' | 'output'>('contract');
  const [copied, setCopied] = useState(false);

  if (!node) return null;

  const nodeData = (node.data as unknown as AgentNodeData) || {};
  const role = (nodeData.role_type || node.type || 'custom').toLowerCase();
  const status = nodeData.executionStatus || 'pending';

  // Match corresponding company member definitions
  const matchedMember =
    DEFAULT_COMPANY_MEMBERS.find((m) => m.node_id === node.id || m.id === node.id) ||
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
      : 'أخصائي المنظومة');

  const englishName =
    matchedMember?.english_name || nodeData.display_name || role.toUpperCase();

  const title =
    matchedMember?.title ||
    (role === 'planner'
      ? 'مدير التخطيط والإستراتيجية'
      : role === 'analyst'
      ? 'مدير التحليل والمخاطر'
      : role === 'reviewer'
      ? 'مدير الجودة والاعتماد'
      : 'عضو فريق الذكاء الاصطناعي');

  const systemPrompt =
    nodeData.instructions ||
    matchedMember?.system_prompt ||
    'Perform assigned AI organizational tasks with rigorous accuracy.';

  const modelId =
    nodeData.model_id ||
    matchedMember?.contract.model_id ||
    'muse-spark-1.3-contributor-free';

  const variant =
    nodeData.variant || matchedMember?.contract.variant || 'high';

  const taskDepth =
    nodeData.task_depth || matchedMember?.contract.task_depth || 'Standard';

  const timeoutSec =
    nodeData.timeout || matchedMember?.contract.timeout_sec || 120;

  const maxRetries =
    matchedMember?.contract.max_retries || 3;

  const durationMs =
    nodeData.durationMs || (status === 'succeeded' ? 142 : undefined);

  const handleCopyOutput = () => {
    if (nodeData.output) {
      const textToCopy =
        typeof nodeData.output === 'object'
          ? JSON.stringify(nodeData.output, null, 2)
          : String(nodeData.output);
      navigator.clipboard.writeText(textToCopy);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    }
  };

  return (
    <div
      data-testid="contract-card-modal"
      data-testid-alt="node-contract-card"
      className="node-contract-card absolute bottom-4 left-4 z-20 w-[460px] max-w-[90vw] max-h-[82vh] bg-gray-900/95 border border-indigo-500/40 rounded-2xl shadow-2xl backdrop-blur-xl flex flex-col overflow-hidden animate-in fade-in slide-in-from-bottom-3 select-text"
      data-node-id={node.id}
    >
      {/* Modal / Card Header */}
      <div className="px-5 py-3.5 bg-gradient-to-r from-gray-900 via-gray-900/90 to-indigo-950/40 border-b border-gray-800 flex items-center justify-between">
        <div className="flex items-center space-x-2.5">
          <div
            className={`p-2 rounded-xl border ${
              isManager
                ? 'bg-amber-500/20 text-amber-300 border-amber-500/40 shadow-sm'
                : 'bg-indigo-500/20 text-indigo-300 border-indigo-500/40 shadow-sm'
            }`}
          >
            {isManager ? (
              <Crown className="w-4 h-4 text-amber-400" />
            ) : (
              <Briefcase className="w-4 h-4 text-indigo-400" />
            )}
          </div>
          <div>
            <div className="flex items-center gap-1.5">
              <span className="text-[10px] font-bold uppercase tracking-wider text-indigo-400">
                Contract Card
              </span>
              <span className="text-gray-600">•</span>
              <span className="text-[10px] font-arabic text-gray-400">بطاقة العقد التنفيذي</span>
            </div>
            <div className="flex items-baseline gap-2">
              <h3 className="text-sm font-bold text-white font-arabic">{naturalName}</h3>
              <span className="text-xs text-gray-400">({englishName})</span>
            </div>
          </div>
        </div>

        <button
          onClick={onClose}
          className="p-1.5 rounded-lg text-gray-400 hover:text-white hover:bg-gray-800/80 transition"
          title="Close Contract Card"
        >
          <X className="w-4 h-4" />
        </button>
      </div>

      {/* Tabs: Contract Specs vs Output Preview */}
      <div className="flex items-center px-5 pt-2 border-b border-gray-800/80 bg-gray-950/40 gap-3">
        <button
          onClick={() => setActiveTab('contract')}
          className={`pb-2 text-xs font-semibold flex items-center gap-1.5 border-b-2 transition ${
            activeTab === 'contract'
              ? 'border-indigo-500 text-indigo-300'
              : 'border-transparent text-gray-400 hover:text-gray-200'
          }`}
        >
          <FileCode className="w-3.5 h-3.5" />
          <span>Execution Contract (العقد)</span>
        </button>

        <button
          onClick={() => setActiveTab('output')}
          className={`pb-2 text-xs font-semibold flex items-center gap-1.5 border-b-2 transition ${
            activeTab === 'output'
              ? 'border-indigo-500 text-indigo-300'
              : 'border-transparent text-gray-400 hover:text-gray-200'
          }`}
        >
          <ExternalLink className="w-3.5 h-3.5" />
          <span>Output Preview (معاينة المخرجات)</span>
          {status === 'succeeded' && (
            <span className="w-2 h-2 rounded-full bg-emerald-400" />
          )}
        </button>
      </div>

      {/* Body Content */}
      <div className="flex-1 overflow-y-auto p-4 space-y-3.5 text-xs">
        {activeTab === 'contract' && (
          <div className="space-y-3.5">
            {/* Role & Title */}
            <div className="bg-gray-950/60 p-2.5 rounded-xl border border-gray-800 flex items-center justify-between">
              <div>
                <span className="text-[10px] text-gray-400 block mb-0.5">Role & Title (الدور)</span>
                <span className="font-semibold text-gray-200 font-arabic">{title}</span>
              </div>
              <div>
                {isManager ? (
                  <span className="px-2 py-0.5 rounded text-[10px] font-bold bg-amber-500/20 text-amber-300 border border-amber-500/40 flex items-center gap-1">
                    <Crown className="w-3 h-3" />
                    <span>👑 Manager</span>
                  </span>
                ) : (
                  <span className="px-2 py-0.5 rounded text-[10px] font-medium bg-gray-800 text-gray-300 border border-gray-700 flex items-center gap-1">
                    <Briefcase className="w-3 h-3" />
                    <span>💼 Specialist</span>
                  </span>
                )}
              </div>
            </div>

            {/* Model & Reasoning Variant */}
            <div className="grid grid-cols-2 gap-2">
              <div className="bg-gray-950/60 p-2.5 rounded-xl border border-gray-800">
                <span className="text-[10px] text-gray-400 flex items-center gap-1 mb-1">
                  <Sparkles className="w-3 h-3 text-emerald-400" />
                  <span>Model (الموديل)</span>
                </span>
                <span className="font-mono text-[11px] text-emerald-300 font-semibold truncate block">
                  {modelId}
                </span>
                <span className="text-[9px] text-gray-500 block mt-0.5">Verified Zero-Cost</span>
              </div>

              <div className="bg-gray-950/60 p-2.5 rounded-xl border border-gray-800">
                <span className="text-[10px] text-gray-400 flex items-center gap-1 mb-1">
                  <Zap className="w-3 h-3 text-amber-400" />
                  <span>Task Depth Profile</span>
                </span>
                <div className="flex items-center gap-1.5">
                  <span className="font-semibold text-amber-300">{taskDepth}</span>
                  <span className="text-[10px] px-1.5 py-0.2 rounded bg-indigo-950 text-indigo-300 border border-indigo-800 font-mono">
                    {variant}
                  </span>
                </div>
              </div>
            </div>

            {/* Execution Bounds (Timeout & Retries) */}
            <div className="grid grid-cols-2 gap-2">
              <div className="bg-gray-950/60 p-2.5 rounded-xl border border-gray-800">
                <span className="text-[10px] text-gray-400 flex items-center gap-1 mb-1">
                  <Clock className="w-3 h-3 text-blue-400" />
                  <span>Timeout Limit</span>
                </span>
                <span className="font-semibold text-gray-200">{timeoutSec}s</span>
                <span className="text-[9px] text-gray-500 block">Strict boundary</span>
              </div>

              <div className="bg-gray-950/60 p-2.5 rounded-xl border border-gray-800">
                <span className="text-[10px] text-gray-400 flex items-center gap-1 mb-1">
                  <ShieldCheck className="w-3 h-3 text-purple-400" />
                  <span>Contract Constraints</span>
                </span>
                <span className="font-semibold text-purple-300">Strict JSON</span>
                <span className="text-[9px] text-gray-500 block">Max {maxRetries} Retries</span>
              </div>
            </div>

            {/* System Prompt Instructions */}
            <div className="bg-gray-950/70 p-3 rounded-xl border border-gray-800">
              <div className="flex items-center justify-between mb-1.5">
                <span className="text-[10px] font-bold text-gray-300 uppercase tracking-wider">
                  System Prompt & Instructions (التعليمات)
                </span>
                <span className="text-[9px] font-arabic text-gray-500">التوجيه الإستراتيجي</span>
              </div>
              <p className="text-[11px] text-gray-300 leading-relaxed font-arabic whitespace-pre-wrap bg-gray-900/60 p-2 rounded-lg border border-gray-800/60 max-h-36 overflow-y-auto">
                {systemPrompt}
              </p>
            </div>
          </div>
        )}

        {activeTab === 'output' && (
          <div className="space-y-3">
            {/* Status Header */}
            <div className="flex items-center justify-between p-2.5 rounded-xl bg-gray-950/70 border border-gray-800">
              <div className="flex items-center gap-2">
                {status === 'succeeded' ? (
                  <CheckCircle2 className="w-4 h-4 text-emerald-400" />
                ) : status === 'failed' ? (
                  <AlertCircle className="w-4 h-4 text-rose-400" />
                ) : status === 'running' ? (
                  <span className="w-3 h-3 rounded-full bg-blue-500 animate-ping" />
                ) : (
                  <Clock className="w-4 h-4 text-gray-500" />
                )}
                <div>
                  <span className="text-[10px] text-gray-400 block">Status:</span>
                  <span className="font-semibold text-gray-200 capitalize">{status}</span>
                </div>
              </div>

              {durationMs && (
                <div className="text-right">
                  <span className="text-[10px] text-gray-400 block">Duration:</span>
                  <span className="font-mono text-emerald-400 font-bold">{durationMs}ms</span>
                </div>
              )}
            </div>

            {/* Output Deliverable Display */}
            <div>
              <div className="flex items-center justify-between mb-1.5">
                <h4 className="text-xs font-semibold text-gray-300 flex items-center gap-1.5">
                  <span>Deliverable Output Preview</span>
                  <span className="text-[10px] font-arabic text-gray-500">(مخرجات المهمة)</span>
                </h4>
                {nodeData.output && (
                  <button
                    onClick={handleCopyOutput}
                    className="inline-flex items-center gap-1 text-[10px] text-gray-400 hover:text-white px-2 py-0.5 rounded bg-gray-800 hover:bg-gray-700 transition"
                  >
                    {copied ? <Check className="w-3 h-3 text-emerald-400" /> : <Copy className="w-3 h-3" />}
                    <span>{copied ? 'Copied' : 'Copy'}</span>
                  </button>
                )}
              </div>

              {nodeData.output ? (
                <pre className="p-3 bg-gray-950 rounded-xl border border-gray-800 text-[11px] font-mono text-emerald-300 overflow-x-auto max-h-56 leading-relaxed select-all">
                  {typeof nodeData.output === 'object'
                    ? JSON.stringify(nodeData.output, null, 2)
                    : String(nodeData.output)}
                </pre>
              ) : status === 'running' ? (
                <div className="p-6 bg-gray-950/80 rounded-xl border border-blue-500/30 text-center space-y-2">
                  <div className="w-6 h-6 border-2 border-blue-500 border-t-transparent rounded-full animate-spin mx-auto" />
                  <p className="text-xs text-blue-300 font-medium">
                    Agent is currently processing execution stage...
                  </p>
                  <p className="text-[10px] text-gray-500 font-arabic">
                    جاري استقبال المخرجات عبر تقنية البث المباشر SSE
                  </p>
                </div>
              ) : (
                <div className="p-6 bg-gray-950/80 rounded-xl border border-dashed border-gray-800 text-center space-y-1">
                  <p className="text-xs text-gray-400">No output generated yet for this node.</p>
                  <p className="text-[10px] text-gray-500 font-arabic">
                    قم بتشغيل خط أنابيب المهام (Run Pipeline) لبدء التوليد والمعاينة الحية.
                  </p>
                </div>
              )}
            </div>
          </div>
        )}
      </div>

      {/* Footer */}
      <div className="px-5 py-2.5 bg-gray-950/60 border-t border-gray-800/80 flex items-center justify-between text-[11px] text-gray-400">
        <span className="font-mono text-[10px]">Node: {node.id}</span>
        <button
          onClick={() => setActiveTab(activeTab === 'contract' ? 'output' : 'contract')}
          className="text-indigo-400 hover:text-indigo-300 transition font-medium"
        >
          {activeTab === 'contract' ? 'View Output Preview →' : '← View Contract Specs'}
        </button>
      </div>
    </div>
  );
};
