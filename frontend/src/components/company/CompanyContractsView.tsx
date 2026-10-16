import React, { useState, useEffect } from 'react';
import {
  Building2,
  Crown,
  Briefcase,
  Shield,
  Cpu,
  Clock,
  Edit3,
  Sparkles,
  Layers,
} from 'lucide-react';
import { DEPARTMENTS, DEFAULT_COMPANY_MEMBERS } from '../../data/defaultCompany';
import type { CompanyMember, DepartmentId } from '../../types';
import { ContractEditorModal } from './ContractEditorModal';
import { getAgentProfiles } from '../../api/client';

export const CompanyContractsView: React.FC = () => {
  const [selectedDept, setSelectedDept] = useState<'all' | DepartmentId | 'qa'>('all');
  const [members, setMembers] = useState<CompanyMember[]>(DEFAULT_COMPANY_MEMBERS);
  const [editingMember, setEditingMember] = useState<CompanyMember | null>(null);
  const [isModalOpen, setIsModalOpen] = useState(false);

  // Sync with backend profiles on mount if available
  useEffect(() => {
    getAgentProfiles()
      .then((profiles) => {
        if (profiles && profiles.length > 0) {
          setMembers((prevMembers) =>
            prevMembers.map((member) => {
              const matchedProfile = profiles.find(
                (p) => p.id === member.id || p.role_type === member.role_type
              );
              if (matchedProfile) {
                let parsedSettings: any = {};
                try {
                  parsedSettings = JSON.parse(
                    (matchedProfile as any).settings_json || '{}'
                  );
                } catch {
                  // ignore malformed settings_json
                }

                return {
                  ...member,
                  natural_name:
                    parsedSettings.arabic_name ||
                    matchedProfile.name ||
                    member.natural_name,
                  title: parsedSettings.title || member.title,
                  system_prompt:
                    matchedProfile.instructions || member.system_prompt,
                  contract: {
                    ...member.contract,
                    model_id:
                      (matchedProfile as any).default_model_id ||
                      member.contract.model_id,
                    variant:
                      (matchedProfile as any).default_variant_id ||
                      member.contract.variant,
                    task_depth:
                      matchedProfile.task_depth || member.contract.task_depth,
                    timeout_sec:
                      parsedSettings.timeout || member.contract.timeout_sec,
                    max_retries:
                      parsedSettings.max_retries ?? member.contract.max_retries,
                    strict_json:
                      parsedSettings.strict_json ?? member.contract.strict_json,
                  },
                };
              }
              return member;
            })
          );
        }
      })
      .catch((err) => {
        console.warn('Backend profiles load skipped (offline mode):', err);
      });
  }, []);

  const handleEdit = (member: CompanyMember) => {
    setEditingMember(member);
    setIsModalOpen(true);
  };

  const handleSaveMember = (updatedMember: CompanyMember) => {
    setMembers((prev) =>
      prev.map((m) => (m.id === updatedMember.id ? updatedMember : m))
    );
  };

  const departmentKeys: DepartmentId[] = ['planning', 'research', 'quality'];
  const normalizedDept = selectedDept === 'qa' ? 'quality' : selectedDept;
  const activeDepartments =
    normalizedDept === 'all'
      ? departmentKeys
      : departmentKeys.filter((id) => id === normalizedDept);

  const executiveMembers = members.filter((m) => m.department === 'executive');

  return (
    <div className="flex-1 flex flex-col h-full bg-gray-950 text-gray-100 overflow-y-auto p-6 md:p-8 font-sans">
      <div className="max-w-6xl w-full mx-auto space-y-8">
        {/* Header */}
        <div className="pb-6 border-b border-gray-800">
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
            <div>
              <div className="flex items-center gap-2">
                <Building2 className="w-6 h-6 text-indigo-400" />
                <h1 className="text-2xl font-bold tracking-tight text-white flex items-center gap-2">
                  <span>Company & Contracts</span>
                  <span className="text-gray-500 font-normal">|</span>
                  <span className="text-gray-300 font-arabic text-xl">
                    إدارات الشركة وموظفوها وعقودهم
                  </span>
                </h1>
              </div>
              <p className="text-sm text-gray-400 mt-1 font-arabic">
                الهيكل التنظيمي للشركة: الإدارات، تعيين المدراء والأخصائيين بأسماء طبيعية، وتخصيص عقود التنفيذ الصارمة وموجهات النظام.
              </p>
            </div>

            {/* Department Filter Tabs */}
            <div className="flex items-center gap-1.5 p-1 bg-gray-900 border border-gray-800 rounded-xl overflow-x-auto">
              <button
                type="button"
                data-dept-tab="all"
                onClick={() => setSelectedDept('all')}
                className={`px-3 py-1.5 rounded-lg text-xs font-semibold transition-all whitespace-nowrap flex items-center gap-1.5 ${
                  selectedDept === 'all'
                    ? 'bg-indigo-600 text-white shadow-md shadow-indigo-600/30'
                    : 'text-gray-400 hover:text-gray-200 hover:bg-gray-800'
                }`}
              >
                <Layers className="w-3.5 h-3.5" />
                <span>All (الكل)</span>
              </button>

              <button
                type="button"
                data-dept-tab="planning"
                onClick={() => setSelectedDept('planning')}
                className={`px-3 py-1.5 rounded-lg text-xs font-semibold transition-all whitespace-nowrap flex items-center gap-1.5 ${
                  selectedDept === 'planning'
                    ? 'bg-blue-600 text-white shadow-md shadow-blue-600/30'
                    : 'text-gray-400 hover:text-gray-200 hover:bg-gray-800'
                }`}
              >
                <span>Planning (التخطيط)</span>
              </button>

              <button
                type="button"
                data-dept-tab="research"
                onClick={() => setSelectedDept('research')}
                className={`px-3 py-1.5 rounded-lg text-xs font-semibold transition-all whitespace-nowrap flex items-center gap-1.5 ${
                  selectedDept === 'research'
                    ? 'bg-amber-600 text-white shadow-md shadow-amber-600/30'
                    : 'text-gray-400 hover:text-gray-200 hover:bg-gray-800'
                }`}
              >
                <span>Research (التحليل)</span>
              </button>

              <button
                type="button"
                data-dept-tab="qa"
                onClick={() => setSelectedDept('qa')}
                className={`px-3 py-1.5 rounded-lg text-xs font-semibold transition-all whitespace-nowrap flex items-center gap-1.5 ${
                  selectedDept === 'qa'
                    ? 'bg-emerald-600 text-white shadow-md shadow-emerald-600/30'
                    : 'text-gray-400 hover:text-gray-200 hover:bg-gray-800'
                }`}
              >
                <span>Quality (الجودة)</span>
              </button>
            </div>
          </div>
        </div>

        {/* Executive Leadership Section (Visible when 'all' is selected) */}
        {selectedDept === 'all' && executiveMembers.length > 0 && (
          <div className="bg-gradient-to-r from-amber-950/20 via-gray-900 to-gray-900 border border-amber-500/30 rounded-2xl p-6 shadow-xl relative overflow-hidden">
            <div className="flex items-center justify-between pb-4 mb-5 border-b border-gray-800">
              <div className="flex items-center gap-3">
                <span className="px-2.5 py-1 text-xs rounded-lg font-bold bg-amber-500/20 text-amber-300 border border-amber-500/40 flex items-center gap-1">
                  <Crown className="w-3.5 h-3.5" />
                  <span>Executive Leadership</span>
                </span>
                <h3 className="text-lg font-bold text-white font-arabic">
                  القيادة التنفيذية والمشرف العام
                </h3>
              </div>
            </div>

            <div className="grid grid-cols-1 gap-4">
              {executiveMembers.map((member) => (
                <div
                  key={member.id}
                  className="p-5 rounded-xl border bg-gradient-to-b from-gray-850 to-gray-900 border-amber-500/50 shadow-lg shadow-amber-500/5 ring-1 ring-amber-500/30"
                >
                  <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 mb-4">
                    <div className="flex items-center gap-3">
                      <div className="w-11 h-11 rounded-xl flex items-center justify-center text-white bg-gradient-to-tr from-amber-500 to-yellow-600 ring-2 ring-amber-400/60 shadow-md">
                        <Crown className="w-6 h-6 text-white" />
                      </div>
                      <div>
                        <div className="flex items-center gap-2">
                          <h4 className="text-lg font-bold text-white">
                            {member.natural_name}
                          </h4>
                          <span className="text-xs text-gray-400">
                            ({member.english_name})
                          </span>
                        </div>
                        <p className="text-xs text-gray-300 font-arabic">
                          {member.title}
                        </p>
                      </div>
                    </div>

                    <div className="flex items-center gap-3">
                      <span
                        data-badge="manager"
                        className="manager-badge px-3 py-1 rounded-full text-xs font-bold bg-amber-500/20 text-amber-300 border border-amber-500/40 flex items-center gap-1.5 shadow-sm"
                      >
                        <Crown className="w-3.5 h-3.5" />
                        <span>👑 Manager</span>
                      </span>
                      <button
                        type="button"
                        data-action="edit-contract"
                        onClick={() => handleEdit(member)}
                        className="px-3 py-1.5 rounded-lg text-xs font-semibold text-white bg-indigo-600 hover:bg-indigo-500 border border-indigo-500 flex items-center gap-1.5 shadow-sm transition-all"
                      >
                        <Edit3 className="w-3.5 h-3.5" />
                        <span>Edit Contract</span>
                      </button>
                    </div>
                  </div>

                  {/* System Prompt Preview */}
                  <div className="p-3 rounded-lg bg-gray-950/80 border border-gray-800 text-xs text-gray-300 mb-3 leading-relaxed font-arabic">
                    <div className="text-[10px] text-gray-500 mb-1 font-sans font-semibold">
                      System Prompt & Persona Instructions:
                    </div>
                    {member.system_prompt}
                  </div>

                  {/* Contract Badges */}
                  <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 pt-3 border-t border-gray-800 text-xs text-gray-300">
                    <div className="flex items-center gap-1.5">
                      <Cpu className="w-3.5 h-3.5 text-indigo-400" />
                      <span className="truncate">{member.contract.model_id}</span>
                    </div>
                    <div className="flex items-center gap-1.5">
                      <Sparkles className="w-3.5 h-3.5 text-blue-400" />
                      <span>Reasoning: {member.contract.variant}</span>
                    </div>
                    <div className="flex items-center gap-1.5">
                      <Clock className="w-3.5 h-3.5 text-amber-400" />
                      <span>{member.contract.timeout_sec}s timeout</span>
                    </div>
                    <div className="flex items-center gap-1.5">
                      <Shield className="w-3.5 h-3.5 text-emerald-400" />
                      <span>
                        {member.contract.strict_json ? 'Strict JSON' : 'Flexible'}
                      </span>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Departments & Members Grid */}
        <div className="space-y-8">
          {activeDepartments.map((deptId) => {
            const dept = DEPARTMENTS[deptId];
            const deptMembers = members.filter((m) => m.department === deptId);

            return (
              <div
                key={deptId}
                className="bg-gray-900/80 rounded-2xl border border-gray-800 p-6 shadow-xl"
              >
                {/* Department Header */}
                <div className="flex flex-col sm:flex-row sm:items-center justify-between pb-4 mb-6 border-b border-gray-800/80 gap-2">
                  <div>
                    <div className="flex items-center gap-3">
                      <span
                        className={`px-2.5 py-1 text-xs rounded-lg font-bold border ${dept.badgeBg}`}
                      >
                        {dept.name}
                      </span>
                      <h3 className="text-lg font-bold text-white font-arabic">
                        {dept.nameArabic}
                      </h3>
                    </div>
                    <p className="text-xs text-gray-400 mt-1 leading-relaxed">
                      {dept.description}
                    </p>
                  </div>
                </div>

                {/* Members Grid */}
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                  {deptMembers.map((member) => (
                    <div
                      key={member.id}
                      className={`p-4 rounded-xl border transition-all flex flex-col justify-between ${
                        member.is_manager
                          ? 'bg-gradient-to-b from-gray-850 to-gray-900 border-amber-500/40 shadow-lg shadow-amber-500/5 ring-1 ring-amber-500/20'
                          : 'bg-gray-950/60 border-gray-800 hover:border-gray-700'
                      }`}
                    >
                      <div>
                        {/* Member Card Header */}
                        <div className="flex items-start justify-between gap-3 mb-3">
                          <div className="flex items-center gap-3">
                            <div
                              className={`w-10 h-10 rounded-xl flex items-center justify-center text-white shadow-sm flex-shrink-0 ${
                                member.is_manager
                                  ? 'bg-gradient-to-tr from-amber-500 to-yellow-600 ring-2 ring-amber-400/50'
                                  : 'bg-gray-800 text-gray-300'
                              }`}
                            >
                              {member.is_manager ? (
                                <Crown className="w-5 h-5 text-white" />
                              ) : (
                                <Briefcase className="w-5 h-5" />
                              )}
                            </div>
                            <div>
                              <div className="flex items-center gap-2">
                                <h4 className="text-base font-bold text-white">
                                  {member.natural_name}
                                </h4>
                                <span className="text-xs text-gray-400">
                                  ({member.english_name})
                                </span>
                              </div>
                              <p className="text-xs text-gray-400 font-arabic">
                                {member.title}
                              </p>
                            </div>
                          </div>

                          <div className="flex flex-col items-end gap-1.5">
                            {member.is_manager ? (
                              <span
                                data-badge="manager"
                                className="manager-badge px-2.5 py-0.5 rounded-full text-[11px] font-bold bg-amber-500/20 text-amber-300 border border-amber-500/40 flex items-center gap-1 shadow-sm"
                              >
                                <Crown className="w-3 h-3" />
                                <span>👑 Manager</span>
                              </span>
                            ) : (
                              <span
                                data-badge="specialist"
                                className="specialist-badge px-2.5 py-0.5 rounded-full text-[11px] font-medium bg-gray-800 text-gray-400 border border-gray-700 flex items-center gap-1"
                              >
                                <Briefcase className="w-3 h-3" />
                                <span>💼 Specialist</span>
                              </span>
                            )}
                            <button
                              type="button"
                              data-action="edit-contract"
                              onClick={() => handleEdit(member)}
                              className="px-2.5 py-1 rounded-lg text-xs font-semibold text-gray-200 hover:text-white bg-gray-800 hover:bg-indigo-600 border border-gray-700 hover:border-indigo-500 flex items-center gap-1 transition-all"
                            >
                              <Edit3 className="w-3 h-3" />
                              <span>Edit Contract</span>
                            </button>
                          </div>
                        </div>

                        {/* System Prompt Preview */}
                        <div className="p-2.5 rounded-lg bg-gray-950/80 border border-gray-850 text-xs text-gray-300 mb-3 leading-relaxed font-arabic">
                          <div className="text-[10px] text-gray-500 mb-1 font-sans font-semibold">
                            System Prompt:
                          </div>
                          <div className="line-clamp-3">{member.system_prompt}</div>
                        </div>
                      </div>

                      {/* Contract Badges */}
                      <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 pt-2 border-t border-gray-800/80 text-[11px] text-gray-400">
                        <div className="flex items-center gap-1" title="Model">
                          <Cpu className="w-3.5 h-3.5 text-indigo-400 flex-shrink-0" />
                          <span className="truncate">{member.contract.model_id}</span>
                        </div>
                        <div className="flex items-center gap-1" title="Reasoning Variant">
                          <Sparkles className="w-3.5 h-3.5 text-blue-400 flex-shrink-0" />
                          <span className="truncate">{member.contract.variant}</span>
                        </div>
                        <div className="flex items-center gap-1" title="Timeout">
                          <Clock className="w-3.5 h-3.5 text-amber-400 flex-shrink-0" />
                          <span>{member.contract.timeout_sec}s</span>
                        </div>
                        <div className="flex items-center gap-1" title="Output format">
                          <Shield className="w-3.5 h-3.5 text-emerald-400 flex-shrink-0" />
                          <span>
                            {member.contract.strict_json ? 'Strict JSON' : 'Flexible'}
                          </span>
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            );
          })}
        </div>
      </div>

      {/* Contract Editor Modal */}
      <ContractEditorModal
        member={editingMember}
        isOpen={isModalOpen}
        onClose={() => {
          setIsModalOpen(false);
          setEditingMember(null);
        }}
        onSave={handleSaveMember}
      />
    </div>
  );
};
