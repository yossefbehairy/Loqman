import React, { useState, useEffect } from 'react';
import {
  X,
  Shield,
  Cpu,
  Clock,
  RotateCw,
  Save,
  AlertCircle,
  FileCode,
  User,
  Sliders,
} from 'lucide-react';
import type { CompanyMember, TaskDepth } from '../../types';
import { updateAgentProfile } from '../../api/client';

interface ContractEditorModalProps {
  member: CompanyMember | null;
  isOpen: boolean;
  onClose: () => void;
  onSave: (updatedMember: CompanyMember) => void;
}

interface ContractEditorFormProps {
  member: CompanyMember;
  onClose: () => void;
  onSave: (updatedMember: CompanyMember) => void;
}

const ContractEditorForm: React.FC<ContractEditorFormProps> = ({
  member,
  onClose,
  onSave,
}) => {
  const [naturalName, setNaturalName] = useState(member.natural_name || '');
  const [title, setTitle] = useState(member.title || '');
  const [systemPrompt, setSystemPrompt] = useState(member.system_prompt || '');
  const [modelId, setModelId] = useState(member.contract?.model_id || 'deepseek-v4.1-flash-free');
  const [variant, setVariant] = useState(member.contract?.variant || 'high');
  const [taskDepth] = useState<TaskDepth>(member.contract?.task_depth || 'Standard');
  const [timeoutSec, setTimeoutSec] = useState(member.contract?.timeout_sec || 120);
  const [maxRetries, setMaxRetries] = useState(member.contract?.max_retries ?? 3);
  const [strictJson, setStrictJson] = useState(member.contract?.strict_json ?? true);
  const [isSaving, setIsSaving] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setIsSaving(true);
    setErrorMessage(null);

    const boundedRetries = Math.max(1, Math.min(3, Number(maxRetries) || 1));
    const boundedTimeout = Math.max(10, Math.min(300, Number(timeoutSec) || 60));

    try {
      // Persist to backend database via updateAgentProfile
      await updateAgentProfile(member.id, {
        name: naturalName,
        instructions: systemPrompt,
        default_model_id: modelId,
        default_variant_id: variant,
        task_depth: taskDepth,
        settings_json: JSON.stringify({
          department: member.department,
          is_manager: member.is_manager,
          badge: member.is_manager ? '👑 Manager' : '💼 Specialist',
          arabic_name: naturalName,
          title: title,
          timeout: boundedTimeout,
          max_retries: boundedRetries,
          strict_json: strictJson,
        }),
      } as any).catch((err) => {
        console.warn('Backend update note:', err);
      });

      const updatedMember: CompanyMember = {
        ...member,
        natural_name: naturalName,
        title: title,
        system_prompt: systemPrompt,
        contract: {
          model_id: modelId,
          variant: variant,
          task_depth: taskDepth,
          timeout_sec: boundedTimeout,
          max_retries: boundedRetries,
          strict_json: strictJson,
        },
      };

      onSave(updatedMember);
      onClose();
    } catch (err: any) {
      setErrorMessage(err.message || 'Failed to update contract');
    } finally {
      setIsSaving(false);
    }
  };

  return (
    <div className="bg-gray-900 border border-gray-700/80 rounded-2xl w-full max-w-2xl max-h-[90vh] flex flex-col shadow-2xl overflow-hidden">
      {/* Modal Header */}
      <div className="flex items-center justify-between px-6 py-4 border-b border-gray-800 bg-gray-900/90">
        <div className="flex items-center gap-3">
          <div
            className={`w-9 h-9 rounded-xl flex items-center justify-center font-bold text-white shadow-sm ${
              member.is_manager
                ? 'bg-gradient-to-tr from-amber-500 to-yellow-600 ring-2 ring-amber-400/40'
                : 'bg-indigo-600'
            }`}
          >
            {member.is_manager ? '👑' : '💼'}
          </div>
          <div>
            <div className="flex items-center gap-2">
              <h3 id="modal-title" className="text-lg font-bold text-white">
                {naturalName || member.natural_name}
              </h3>
              <span className="text-xs text-gray-400">({member.english_name})</span>
              {member.is_manager ? (
                <span
                  data-badge="manager"
                  className="manager-badge px-2 py-0.5 rounded-full text-[10px] font-bold bg-amber-500/20 text-amber-300 border border-amber-500/40"
                >
                  👑 Manager
                </span>
              ) : (
                <span
                  data-badge="specialist"
                  className="specialist-badge px-2 py-0.5 rounded-full text-[10px] font-medium bg-gray-800 text-gray-300 border border-gray-700"
                >
                  💼 Specialist
                </span>
              )}
            </div>
            <p className="text-xs text-gray-400 font-arabic">{title || member.title}</p>
          </div>
        </div>
        <button
          type="button"
          onClick={onClose}
          className="p-1.5 rounded-lg text-gray-400 hover:text-white hover:bg-gray-800 transition-colors"
          aria-label="Close"
        >
          <X className="w-5 h-5" />
        </button>
      </div>

      {/* Modal Body / Form */}
      <form onSubmit={handleSubmit} className="flex-1 overflow-y-auto p-6 space-y-6">
        {errorMessage && (
          <div className="p-3 rounded-lg bg-rose-500/10 border border-rose-500/30 flex items-center gap-2 text-rose-300 text-xs">
            <AlertCircle className="w-4 h-4 flex-shrink-0" />
            <span>{errorMessage}</span>
          </div>
        )}

        {/* Section 1: Member Identity */}
        <div className="space-y-3">
          <h4 className="text-xs font-semibold text-gray-400 uppercase tracking-wider flex items-center gap-1.5">
            <User className="w-3.5 h-3.5 text-indigo-400" />
            <span>Identity & Natural Designation (الهوية والاسم الطبيعي)</span>
          </h4>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <div>
              <label className="block text-xs font-medium text-gray-300 mb-1">
                Natural Name (الاسم الطبيعي)
              </label>
              <input
                type="text"
                name="natural_name"
                value={naturalName}
                onChange={(e) => setNaturalName(e.target.value)}
                placeholder="e.g. د. سارة, م. طارق, أحمد"
                className="w-full px-3 py-2 bg-gray-950 border border-gray-800 rounded-lg text-sm text-white focus:outline-none focus:border-indigo-500"
                required
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-gray-300 mb-1">
                Title / Designation (المسمى الوظيفي)
              </label>
              <input
                type="text"
                name="title"
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                placeholder="e.g. مدير التخطيط والإستراتيجية"
                className="w-full px-3 py-2 bg-gray-950 border border-gray-800 rounded-lg text-sm text-white focus:outline-none focus:border-indigo-500"
                required
              />
            </div>
          </div>
        </div>

        {/* Section 2: System Prompt */}
        <div className="space-y-2">
          <h4 className="text-xs font-semibold text-gray-400 uppercase tracking-wider flex items-center gap-1.5">
            <FileCode className="w-3.5 h-3.5 text-blue-400" />
            <span>System Prompt & Persona Instructions (موجهات النظام والخبرة)</span>
          </h4>
          <p className="text-[11px] text-gray-400 leading-relaxed">
            Defines the agent's persona, domain expertise, strict guidelines, and decision boundaries.
          </p>
          <textarea
            name="system_prompt"
            rows={4}
            value={systemPrompt}
            onChange={(e) => setSystemPrompt(e.target.value)}
            placeholder="Define agent persona, instructions, reasoning style, and task guidelines..."
            className="w-full px-3 py-2.5 bg-gray-950 border border-gray-800 rounded-lg text-sm text-gray-200 font-mono leading-relaxed focus:outline-none focus:border-indigo-500"
            required
          />
        </div>

        {/* Section 3: Verified Zero-Cost Model & Reasoning Variants */}
        <div className="space-y-3">
          <h4 className="text-xs font-semibold text-gray-400 uppercase tracking-wider flex items-center gap-1.5">
            <Cpu className="w-3.5 h-3.5 text-emerald-400" />
            <span>Zero-Cost Models & Reasoning Variants (الموديلات المجانية والمنطق)</span>
          </h4>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <div>
              <label className="block text-xs font-medium text-gray-300 mb-1">
                Verified Zero-Cost Model (موديل معتمد مجاني)
              </label>
              <select
                name="model_id"
                value={modelId}
                onChange={(e) => setModelId(e.target.value)}
                className="w-full px-3 py-2 bg-gray-950 border border-gray-800 rounded-lg text-sm text-white focus:outline-none focus:border-indigo-500"
              >
                <optgroup label="DeepSeek Free Models">
                  <option value="deepseek-v4.1-flash-free">
                    DeepSeek V4.1 Flash Free (Zero Cost)
                  </option>
                  <option value="deepseek-ai/deepseek-v4.1-flash">
                    DeepSeek V4.1 Flash (Nvidia Free)
                  </option>
                  <option value="deepseek-v4-flash">
                    DeepSeek V4 Flash (AgentRouter Free)
                  </option>
                </optgroup>
                <optgroup label="Muse Contributor Free">
                  <option value="muse-spark-1.3-contributor-free">
                    Muse Spark 1.3 Contributor Free
                  </option>
                </optgroup>
                <optgroup label="Nemotron Multimodal Free">
                  <option value="nemotron-3.5-super-vl-free">
                    Nemotron 3.5 Super VL Free
                  </option>
                </optgroup>
              </select>
            </div>

            <div>
              <label className="block text-xs font-medium text-gray-300 mb-1">
                Reasoning Variant (مستوى التفكير المنطقي)
              </label>
              <select
                name="variant"
                value={variant}
                onChange={(e) => setVariant(e.target.value)}
                className="w-full px-3 py-2 bg-gray-950 border border-gray-800 rounded-lg text-sm text-white focus:outline-none focus:border-indigo-500"
              >
                <option value="low">low (Fast response / low tokens)</option>
                <option value="high">high (Standard reasoning depth)</option>
                <option value="max">max (Maximum reasoning depth)</option>
              </select>
            </div>
          </div>
        </div>

        {/* Section 4: Execution Contract Constraints */}
        <div className="space-y-3">
          <h4 className="text-xs font-semibold text-gray-400 uppercase tracking-wider flex items-center gap-1.5">
            <Sliders className="w-3.5 h-3.5 text-amber-400" />
            <span>Execution Contract Constraints (شروط العقد التنفيذي الصارم)</span>
          </h4>

          {/* Strict JSON Toggle */}
          <div className="flex items-center justify-between p-3 rounded-xl bg-gray-950/70 border border-gray-800/80">
            <div className="flex items-center gap-3">
              <div className="p-2 rounded-lg bg-emerald-500/10 text-emerald-400">
                <Shield className="w-4 h-4" />
              </div>
              <div>
                <div className="text-xs font-semibold text-gray-200">
                  Strict JSON Format (تنسيق JSON الصارم)
                </div>
                <div className="text-[11px] text-gray-400">
                  Forces output to conform strictly to JSON schema without conversational markdown.
                </div>
              </div>
            </div>
            <label className="relative inline-flex items-center cursor-pointer">
              <input
                type="checkbox"
                data-testid="strict-json-toggle"
                checked={strictJson}
                onChange={(e) => setStrictJson(e.target.checked)}
                className="sr-only peer"
              />
              <div className="w-11 h-6 bg-gray-800 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-emerald-600"></div>
              <span className="sr-only">Strict JSON</span>
            </label>
          </div>

          {/* Bounds: Max Retries and Timeout */}
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <div>
              <label className="block text-xs font-medium text-gray-300 mb-1 flex items-center gap-1">
                <RotateCw className="w-3 h-3 text-indigo-400" />
                <span>Max Retries Bound (1 - 3 attempts)</span>
              </label>
              <input
                type="number"
                name="max_retries"
                min="1"
                max="3"
                value={maxRetries}
                onChange={(e) => setMaxRetries(Number(e.target.value))}
                className="w-full px-3 py-2 bg-gray-950 border border-gray-800 rounded-lg text-sm text-white focus:outline-none focus:border-indigo-500"
                required
              />
              <span className="text-[10px] text-gray-500 mt-0.5 block">
                Bounded between 1 and 3 retry attempts
              </span>
            </div>

            <div>
              <label className="block text-xs font-medium text-gray-300 mb-1 flex items-center gap-1">
                <Clock className="w-3 h-3 text-amber-400" />
                <span>Timeout Limit (10 - 300 seconds)</span>
              </label>
              <input
                type="number"
                name="timeout"
                min="10"
                max="300"
                value={timeoutSec}
                onChange={(e) => setTimeoutSec(Number(e.target.value))}
                className="w-full px-3 py-2 bg-gray-950 border border-gray-800 rounded-lg text-sm text-white focus:outline-none focus:border-indigo-500"
                required
              />
              <span className="text-[10px] text-gray-500 mt-0.5 block">
                Bounded between 10s and 300s timeout
              </span>
            </div>
          </div>
        </div>

        {/* Footer Actions */}
        <div className="flex items-center justify-end gap-3 pt-4 border-t border-gray-800">
          <button
            type="button"
            onClick={onClose}
            disabled={isSaving}
            className="px-4 py-2 rounded-xl text-sm font-medium text-gray-300 hover:text-white bg-gray-800 hover:bg-gray-750 transition-colors"
          >
            Cancel
          </button>
          <button
            type="submit"
            disabled={isSaving}
            className="px-5 py-2 rounded-xl text-sm font-semibold text-[#18181b] bg-[var(--acc)] hover:brightness-105 shadow-md flex items-center gap-2 transition-all disabled:opacity-50"
          >
            {isSaving ? (
              <>
                <div className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                <span>Saving...</span>
              </>
            ) : (
              <>
                <Save className="w-4 h-4" />
                <span>Save Contract</span>
              </>
            )}
          </button>
        </div>
      </form>
    </div>
  );
};

export const ContractEditorModal: React.FC<ContractEditorModalProps> = ({
  member,
  isOpen,
  onClose,
  onSave,
}) => {
  // Handle ESC key press to close modal
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && isOpen) {
        onClose();
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [isOpen, onClose]);

  if (!isOpen || !member) return null;

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="modal-title"
      data-testid="contract-editor-modal"
      className="contract-modal fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/80 backdrop-blur-sm animate-in fade-in duration-200"
    >
      <ContractEditorForm
        key={member.id}
        member={member}
        onClose={onClose}
        onSave={onSave}
      />
    </div>
  );
};
