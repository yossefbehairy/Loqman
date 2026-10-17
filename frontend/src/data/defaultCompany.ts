import { Sparkles, Compass, ShieldAlert, CheckCircle2 } from 'lucide-react';
import type { CompanyMember, DepartmentId } from '../types';

export interface ManagerPersonaOption {
  id: string;
  dataPersona: string;
  nameArabic: string;
  nameEnglish: string;
  roleBadge: string;
  departmentArabic: string;
  departmentEnglish: string;
  title: string;
  avatarColor: string;
  icon: React.ComponentType<{ className?: string }>;
}

export const MANAGER_OPTIONS: ManagerPersonaOption[] = [
  {
    id: 'general_manager',
    dataPersona: 'general_manager',
    nameArabic: 'المدير العام',
    nameEnglish: 'General Manager',
    roleBadge: '👑 Manager',
    departmentArabic: 'القيادة التنفيذية',
    departmentEnglish: 'Orchestrator & Strategy',
    title: 'المدير العام والتنسيق التنفيذي',
    avatarColor: 'from-amber-500 to-yellow-600',
    icon: Sparkles,
  },
  {
    id: 'dr_sara',
    dataPersona: 'dr_sara',
    nameArabic: 'د. سارة',
    nameEnglish: 'Dr. Sara',
    roleBadge: '👑 Manager',
    departmentArabic: 'إدارة التخطيط',
    departmentEnglish: 'Planning & Strategy',
    title: 'مدير التخطيط والإستراتيجية',
    avatarColor: 'from-blue-600 to-indigo-600',
    icon: Compass,
  },
  {
    id: 'eng_tarek',
    dataPersona: 'eng_tarek',
    nameArabic: 'م. طارق',
    nameEnglish: 'Eng. Tarek',
    roleBadge: '👑 Manager',
    departmentArabic: 'إدارة التحليل والمخاطر',
    departmentEnglish: 'Analysis & Risk',
    title: 'مدير التحليل والمخاطر',
    avatarColor: 'from-orange-600 to-amber-600',
    icon: ShieldAlert,
  },
  {
    id: 'prof_yasmin',
    dataPersona: 'prof_yasmin',
    nameArabic: 'أ. ياسمين',
    nameEnglish: 'Prof. Yasmin',
    roleBadge: '👑 Manager',
    departmentArabic: 'إدارة الجودة والاعتماد',
    departmentEnglish: 'Quality & Review',
    title: 'مدير الجودة والاعتماد',
    avatarColor: 'from-emerald-600 to-teal-600',
    icon: CheckCircle2,
  },
];

export interface DepartmentInfo {
  id: DepartmentId;
  name: string;
  nameArabic: string;
  description: string;
  managerId: string;
  color: string;
  badgeBg: string;
  accentBorder: string;
}

export const DEPARTMENTS: Record<DepartmentId, DepartmentInfo> = {
  planning: {
    id: 'planning',
    name: 'Planning & Strategy',
    nameArabic: 'إدارة التخطيط والإستراتيجية',
    description: 'Decomposes complex enterprise goals into architectural blueprints, milestone breakdowns, and structured phases.',
    managerId: 'dr_sara',
    color: 'from-blue-600 to-indigo-700',
    badgeBg: 'bg-blue-500/10 text-blue-400 border-blue-500/30',
    accentBorder: 'border-blue-500/40',
  },
  research: {
    id: 'research',
    name: 'Research & Feasibility',
    nameArabic: 'إدارة التحليل والمخاطر',
    description: 'Conducts deep technical feasibility studies, concurrency failure analysis, risk modeling, and vulnerability assessments.',
    managerId: 'eng_tarek',
    color: 'from-amber-600 to-orange-700',
    badgeBg: 'bg-amber-500/10 text-amber-400 border-amber-500/30',
    accentBorder: 'border-amber-500/40',
  },
  quality: {
    id: 'quality',
    name: 'Quality Assurance & Review',
    nameArabic: 'إدارة الجودة والاعتماد',
    description: 'Enforces strict JSON schema compliance, audit checklists, boundary condition validation, and final executive sign-off.',
    managerId: 'prof_yasmin',
    color: 'from-emerald-600 to-teal-700',
    badgeBg: 'bg-emerald-500/10 text-emerald-400 border-emerald-500/30',
    accentBorder: 'border-emerald-500/40',
  },
};

export const DEFAULT_COMPANY_MEMBERS: CompanyMember[] = [
  // General Manager (Orchestrator)
  {
    id: 'general_manager',
    node_id: 'gm-root',
    natural_name: 'المدير العام',
    english_name: 'General Manager',
    title: 'المدير العام والتنسيق التنفيذي',
    role_type: 'general_manager',
    department: 'executive',
    is_manager: true,
    system_prompt:
      'أنت المدير العام (General Manager & Chief Orchestrator) لمنصة AgentOS. مهمتك توجيه الإدارات الثلاث، مواءمة الأهداف الإستراتيجية، تفويض المهام التنفيذية، وضمان تسليم مشاريع الذكاء الاصطناعي بكفاءة وجودة عالية.',
    contract: {
      model_id: 'deepseek-v4.1-flash-free',
      variant: 'max',
      task_depth: 'Deep',
      timeout_sec: 180,
      max_retries: 3,
      strict_json: true,
    },
  },

  // Planning & Strategy Department
  {
    id: 'dr_sara',
    node_id: 'planner-1',
    natural_name: 'د. سارة',
    english_name: 'Dr. Sara',
    title: 'مدير التخطيط والإستراتيجية',
    role_type: 'planner',
    department: 'planning',
    is_manager: true,
    system_prompt:
      'أنت د. سارة، مديرة إدارة التخطيط والإستراتيجية. خبرتك في الهندسة المعمارية للنظم البرمجية، تفكيك المتطلبات الكبيرة إلى مراحل واضحة (Phases & Milestones)، وتحديد المخرجات المتوقعة بدقة رياضية صارمة.',
    contract: {
      model_id: 'deepseek-v4.1-flash-free',
      variant: 'high',
      task_depth: 'Standard',
      timeout_sec: 120,
      max_retries: 3,
      strict_json: true,
    },
  },
  {
    id: 'ahmed',
    node_id: 'planner-spec-1',
    natural_name: 'أحمد',
    english_name: 'Ahmed',
    title: 'أخصائي التخطيط وهندسة النظم',
    role_type: 'planner',
    department: 'planning',
    is_manager: false,
    system_prompt:
      'أنت أحمد، مهندس التخطيط المعماري المتخصص تحت إشراف د. سارة. تتولى صياغة مخططات التسلسل، وهيكلة واجهات البيانات، وحساب تقديرات زمن التنفيذ.',
    contract: {
      model_id: 'deepseek-v4.1-flash-free',
      variant: 'high',
      task_depth: 'Standard',
      timeout_sec: 90,
      max_retries: 2,
      strict_json: true,
    },
  },

  // Research & Feasibility Department
  {
    id: 'eng_tarek',
    node_id: 'analyst-1',
    natural_name: 'م. طارق',
    english_name: 'Eng. Tarek',
    title: 'مدير التحليل والمخاطر',
    role_type: 'analyst',
    department: 'research',
    is_manager: true,
    system_prompt:
      'أنت المهندس طارق، مدير إدارة التحليل والمخاطر الفنية. مهمتك مراجعة مقترحات التخطيط وتحديد نقاط الضعف، ونقاط الاختناق المتزامنة، ومخاطر الأمان والاعتماديات قبل التنفيذ.',
    contract: {
      model_id: 'deepseek-v4.1-flash-free',
      variant: 'high',
      task_depth: 'Deep',
      timeout_sec: 150,
      max_retries: 3,
      strict_json: true,
    },
  },
  {
    id: 'khaled',
    node_id: 'analyst-spec-1',
    natural_name: 'خالد',
    english_name: 'Khaled',
    title: 'أخصائي أمان العمليات والجدوى',
    role_type: 'analyst',
    department: 'research',
    is_manager: false,
    system_prompt:
      'أنت خالد، أخصائي دراسات الجدوى والأمان السيبراني تحت إشراف م. طارق. تركز على محاكاة السيناريوهات السلبية، وفحص متانة بنية API، وحماية البيانات.',
    contract: {
      model_id: 'deepseek-v4.1-flash-free',
      variant: 'high',
      task_depth: 'Standard',
      timeout_sec: 90,
      max_retries: 2,
      strict_json: true,
    },
  },

  // Quality Assurance & Review Department
  {
    id: 'prof_yasmin',
    node_id: 'reviewer-1',
    natural_name: 'أ. ياسمين',
    english_name: 'Prof. Yasmin',
    title: 'مدير الجودة والاعتماد',
    role_type: 'reviewer',
    department: 'quality',
    is_manager: true,
    system_prompt:
      'أنت الأستاذة ياسمين، مديرة إدارة الجودة والحوكمة والاعتماد النهائي. مهمتك فحص كافة مخرجات الإدارات السابقة، والتأكد من مطابقة العقود التنفيذية الصارمة، وإعطاء قرار الاعتماد النهائي (Approval / Rejection).',
    contract: {
      model_id: 'deepseek-v4.1-flash-free',
      variant: 'max',
      task_depth: 'Deep',
      timeout_sec: 180,
      max_retries: 3,
      strict_json: true,
    },
  },
  {
    id: 'layla',
    node_id: 'reviewer-spec-1',
    natural_name: 'ليلى',
    english_name: 'Layla',
    title: 'أخصائية التدقيق والامتثال',
    role_type: 'reviewer',
    department: 'quality',
    is_manager: false,
    system_prompt:
      'أنت ليلى، أخصائية التدقيق وفحص المخططات تحت إشراف أ. ياسمين. تدققين في صحة ملفات JSON، وتتحققين من تغطية الشروط الطرفية وسلامة الهياكل التكرارية.',
    contract: {
      model_id: 'deepseek-v4.1-flash-free',
      variant: 'high',
      task_depth: 'Standard',
      timeout_sec: 90,
      max_retries: 2,
      strict_json: true,
    },
  },
];

export const MANAGERS = DEFAULT_COMPANY_MEMBERS.filter((m) => m.is_manager);

export function getMemberById(id: string): CompanyMember | undefined {
  return DEFAULT_COMPANY_MEMBERS.find((m) => m.id === id);
}

export function getMembersByDepartment(department: DepartmentId): CompanyMember[] {
  return DEFAULT_COMPANY_MEMBERS.filter((m) => m.department === department);
}
