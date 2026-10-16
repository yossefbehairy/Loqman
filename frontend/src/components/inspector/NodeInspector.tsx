import React from 'react';
import type { Node } from '@xyflow/react';
import {
  X,
  Sparkles,
  AlertTriangle,
  Sliders,
  Clock,
  ShieldCheck,
  Zap,
} from 'lucide-react';
import type {
  AgentNodeData,
  StartNodeData,
  OutputNodeData,
  ModelCatalogEntry,
  AgentProfile,
} from '../../types';

interface NodeInspectorProps {
  selectedNode: Node | null;
  models: ModelCatalogEntry[];
  profiles: AgentProfile[];
  onClose: () => void;
  onUpdateNodeData: (nodeId: string, updatedData: any) => void;
}

export const NodeInspector: React.FC<NodeInspectorProps> = ({
  selectedNode,
  models,
  profiles,
  onClose,
  onUpdateNodeData,
}) => {
  if (!selectedNode) return null;

  const nodeType = selectedNode.type;

  // Handle role profile switch
  const handleProfileChange = (roleType: string) => {
    const profile = profiles.find((p) => p.role_type === roleType);
    if (!profile) return;

    onUpdateNodeData(selectedNode.id, {
      ...selectedNode.data,
      role_type: profile.role_type,
      display_name: profile.name,
      instructions: profile.instructions,
      task_depth: profile.task_depth,
    });
  };

  const handleFieldChange = (field: string, value: any) => {
    onUpdateNodeData(selectedNode.id, {
      ...selectedNode.data,
      [field]: value,
    });
  };

  const agentData = (selectedNode.data as unknown as AgentNodeData) || {};
  const currentModelId = agentData.model_id || (models[0]?.id || 'muse-spark-1.3-contributor-free');
  const selectedModel = models.find((m) => m.id === currentModelId);
  const supportedVariants = selectedModel?.variants || [];

  return (
    <div className="w-96 border-l border-gray-800 bg-gray-900/95 backdrop-blur-xl flex flex-col h-full shadow-2xl z-20">
      {/* Header */}
      <div className="px-5 py-4 border-b border-gray-800 flex items-center justify-between">
        <div className="flex items-center space-x-2">
          <div className="p-1.5 bg-indigo-500/20 text-indigo-400 rounded-lg">
            <Sliders className="w-4 h-4" />
          </div>
          <div>
            <h3 className="text-sm font-bold text-gray-100">Node Inspector</h3>
            <p className="text-[11px] text-gray-400 font-mono">
              Type: {nodeType} | ID: {selectedNode.id}
            </p>
          </div>
        </div>
        <button
          onClick={onClose}
          className="p-1.5 rounded-lg text-gray-400 hover:text-gray-200 hover:bg-gray-800 transition"
        >
          <X className="w-4 h-4" />
        </button>
      </div>

      {/* Inspector Body */}
      <div className="flex-1 overflow-y-auto p-5 space-y-5">
        {/* === CASE 1: START NODE === */}
        {nodeType === 'start' && (
          <div className="space-y-4">
            <div>
              <label className="block text-xs font-semibold text-gray-300 mb-1.5">
                Initial Workflow Input / Goal
              </label>
              <textarea
                rows={5}
                className="w-full bg-gray-950 border border-gray-800 rounded-lg p-2.5 text-xs text-gray-200 focus:outline-none focus:ring-1 focus:ring-emerald-500"
                placeholder="Enter prompt or problem statement (e.g., Deploy an AI student enrollment system)..."
                value={(selectedNode.data as unknown as StartNodeData).initialInput || ''}
                onChange={(e) => handleFieldChange('initialInput', e.target.value)}
              />
              <p className="text-[11px] text-gray-500 mt-1">
                This initial goal payload will be transmitted as cumulative upstream context to downstream agents.
              </p>
            </div>
          </div>
        )}

        {/* === CASE 2: AGENT NODE === */}
        {nodeType === 'agent' && (
          <div className="space-y-5">
            {/* Role Profile Selector */}
            <div>
              <label className="block text-xs font-semibold text-gray-300 mb-1.5 flex items-center justify-between">
                <span>AI Employee Role Library</span>
                <span className="text-[10px] text-indigo-400 font-normal">10 Built-in Profiles</span>
              </label>
              <select
                className="w-full bg-gray-950 border border-gray-800 rounded-lg p-2.5 text-xs text-gray-200 focus:outline-none focus:ring-1 focus:ring-indigo-500 capitalize"
                value={agentData.role_type || 'custom'}
                onChange={(e) => handleProfileChange(e.target.value)}
              >
                {profiles.map((p) => (
                  <option key={p.id} value={p.role_type}>
                    {p.name} — {p.description.slice(0, 45)}...
                  </option>
                ))}
              </select>
            </div>

            {/* Display Name */}
            <div>
              <label className="block text-xs font-semibold text-gray-300 mb-1.5">
                Display Name
              </label>
              <input
                type="text"
                className="w-full bg-gray-950 border border-gray-800 rounded-lg p-2 text-xs text-gray-200 focus:outline-none focus:ring-1 focus:ring-indigo-500"
                value={agentData.display_name || ''}
                onChange={(e) => handleFieldChange('display_name', e.target.value)}
              />
            </div>

            {/* Free Models Selector */}
            <div>
              <div className="flex items-center justify-between mb-1.5">
                <label className="text-xs font-semibold text-gray-300 flex items-center space-x-1">
                  <Sparkles className="w-3.5 h-3.5 text-emerald-400" />
                  <span>Free AI Model (Verified 0/0 Cost)</span>
                </label>
                <span className="text-[10px] bg-emerald-950/80 text-emerald-300 border border-emerald-800 px-1.5 py-0.5 rounded font-mono">
                  Zero-Cost Only
                </span>
              </div>
              <select
                className="w-full bg-gray-950 border border-gray-800 rounded-lg p-2.5 text-xs text-gray-200 focus:outline-none focus:ring-1 focus:ring-indigo-500 font-mono"
                value={agentData.model_id || currentModelId}
                onChange={(e) => {
                  const newModelId = e.target.value;
                  const newModel = models.find((m) => m.id === newModelId);
                  const firstVariant = newModel && newModel.variants.length > 0 ? newModel.variants[0] : undefined;
                  onUpdateNodeData(selectedNode.id, {
                    ...selectedNode.data,
                    model_id: newModelId,
                    variant: firstVariant,
                  });
                }}
              >
                <optgroup label="🌟 DeepSeek Free Models">
                  {models.filter((m) => m.id.toLowerCase().includes('deepseek')).map((m) => (
                    <option key={m.id} value={m.id}>
                      {m.name} ({m.id})
                    </option>
                  ))}
                </optgroup>
                <optgroup label="⚡ Other Verified Zero-Cost Models">
                  {models.filter((m) => !m.id.toLowerCase().includes('deepseek')).map((m) => (
                    <option key={m.id} value={m.id}>
                      {m.name} ({m.id})
                    </option>
                  ))}
                </optgroup>
              </select>

              {/* Contributor Privacy Notice */}
              <div className="mt-2 p-2.5 rounded-lg bg-amber-950/30 border border-amber-800/40 flex items-start space-x-2">
                <AlertTriangle className="w-4 h-4 text-amber-400 flex-shrink-0 mt-0.5" />
                <p className="text-[11px] text-amber-300/90 leading-relaxed">
                  <strong>Notice:</strong> Free contributor tier models may process anonymized telemetry. Never enter confidential student medical records or secrets.
                </p>
              </div>
            </div>

            {/* Native Reasoning Variant */}
            <div>
              <label className="block text-xs font-semibold text-gray-300 mb-1.5 flex items-center justify-between">
                <span>Native Reasoning Variant</span>
                <span className="text-[10px] text-gray-500 font-mono">Model Metadata</span>
              </label>

              {supportedVariants.length > 0 ? (
                <select
                  className="w-full bg-gray-950 border border-gray-800 rounded-lg p-2 text-xs text-gray-200 focus:outline-none focus:ring-1 focus:ring-indigo-500 font-mono"
                  value={agentData.variant || ''}
                  onChange={(e) => handleFieldChange('variant', e.target.value || undefined)}
                >
                  <option value="">Default / Auto (Model Recommended)</option>
                  {supportedVariants.map((v) => (
                    <option key={v} value={v}>
                      {v}
                    </option>
                  ))}
                </select>
              ) : (
                <div className="text-[11px] text-gray-500 bg-gray-950 p-2 rounded border border-gray-800 italic">
                  Selected model does not declare native reasoning variants.
                </div>
              )}
            </div>

            {/* Task Depth Modifier */}
            <div>
              <label className="block text-xs font-semibold text-gray-300 mb-1.5 flex items-center space-x-1">
                <Zap className="w-3.5 h-3.5 text-amber-400" />
                <span>Task Depth Profile</span>
              </label>
              <div className="grid grid-cols-3 gap-2">
                {(['Quick', 'Standard', 'Deep'] as const).map((depth) => {
                  const isSelected = (agentData.task_depth || 'Standard') === depth;
                  return (
                    <button
                      key={depth}
                      type="button"
                      onClick={() => handleFieldChange('task_depth', depth)}
                      className={`py-1.5 text-xs font-medium rounded-lg border transition ${
                        isSelected
                          ? 'bg-indigo-600/30 border-indigo-500 text-indigo-300 shadow-sm'
                          : 'bg-gray-950 border-gray-800 text-gray-400 hover:border-gray-700'
                      }`}
                    >
                      {depth}
                    </button>
                  );
                })}
              </div>
              <p className="text-[10px] text-gray-500 mt-1 italic">
                AgentOS prompt instruction modifier (does not claim to alter model weights).
              </p>
            </div>

            {/* Instructions Prompt */}
            <div>
              <label className="block text-xs font-semibold text-gray-300 mb-1.5">
                Role Instructions
              </label>
              <textarea
                rows={5}
                className="w-full bg-gray-950 border border-gray-800 rounded-lg p-2.5 text-xs text-gray-200 focus:outline-none focus:ring-1 focus:ring-indigo-500"
                value={agentData.instructions || ''}
                onChange={(e) => handleFieldChange('instructions', e.target.value)}
              />
            </div>

            {/* Timeout & Execution bounds */}
            <div className="grid grid-cols-2 gap-3 pt-2 border-t border-gray-800">
              <div>
                <label className="block text-[11px] text-gray-400 mb-1 flex items-center space-x-1">
                  <Clock className="w-3 h-3" />
                  <span>Timeout (sec)</span>
                </label>
                <input
                  type="number"
                  min={10}
                  max={300}
                  className="w-full bg-gray-950 border border-gray-800 rounded-lg p-1.5 text-xs text-gray-200"
                  value={agentData.timeout || 120}
                  onChange={(e) => handleFieldChange('timeout', parseInt(e.target.value) || 120)}
                />
              </div>

              <div>
                <label className="block text-[11px] text-gray-400 mb-1 flex items-center space-x-1">
                  <ShieldCheck className="w-3 h-3" />
                  <span>Max Retries</span>
                </label>
                <input
                  type="number"
                  disabled
                  className="w-full bg-gray-950/60 border border-gray-800/60 rounded-lg p-1.5 text-xs text-gray-500 cursor-not-allowed"
                  value={2}
                />
              </div>
            </div>
          </div>
        )}

        {/* === CASE 3: OUTPUT NODE === */}
        {nodeType === 'output' && (
          <div className="space-y-4">
            <div>
              <label className="block text-xs font-semibold text-gray-300 mb-1.5">
                Output Label
              </label>
              <input
                type="text"
                className="w-full bg-gray-950 border border-gray-800 rounded-lg p-2 text-xs text-gray-200"
                value={(selectedNode.data as unknown as OutputNodeData).label || 'Workflow Output'}
                onChange={(e) => handleFieldChange('label', e.target.value)}
              />
            </div>
            <p className="text-xs text-gray-400">
              This terminal node collects and combines the verified JSON outputs of all upstream stages into a structured final payload.
            </p>
          </div>
        )}
      </div>
    </div>
  );
};
