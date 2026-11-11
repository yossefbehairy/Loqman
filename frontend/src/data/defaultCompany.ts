import { Sparkles, Compass, Hammer, CheckCircle2 } from 'lucide-react';
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
    nameArabic: 'Loqman',
    nameEnglish: 'Loqman',
    roleBadge: '👑 CEO',
    departmentArabic: 'Executive',
    departmentEnglish: 'Executive',
    title: 'الرئيس التنفيذي · CEO',
    avatarColor: 'from-amber-500 to-yellow-600',
    icon: Sparkles,
  },
  {
    id: 'dr_sara',
    dataPersona: 'dr_sara',
    nameArabic: 'Dr. Sara',
    nameEnglish: 'Dr. Sara',
    roleBadge: '👑 Manager',
    departmentArabic: 'Compass',
    departmentEnglish: 'Compass · Planning',
    title: 'مدير التخطيط والإستراتيجية',
    avatarColor: 'from-blue-600 to-indigo-600',
    icon: Compass,
  },
  {
    id: 'eng_tarek',
    dataPersona: 'eng_tarek',
    nameArabic: 'Eng. Tarek',
    nameEnglish: 'Eng. Tarek',
    roleBadge: '👑 Manager',
    departmentArabic: 'Forge',
    departmentEnglish: 'Forge · Execution',
    title: 'مدير التنفيذ والهندسة',
    avatarColor: 'from-orange-600 to-amber-600',
    icon: Hammer,
  },
  {
    id: 'prof_yasmin',
    dataPersona: 'prof_yasmin',
    nameArabic: 'Prof. Yasmin',
    nameEnglish: 'Prof. Yasmin',
    roleBadge: '👑 Quality Lead',
    departmentArabic: 'بوابة الجودة',
    departmentEnglish: 'Quality Gate',
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
    name: 'Compass',
    nameArabic: 'Compass — Planning & Strategy',
    description:
      'عقد التخطيط: كل مهمة تبدأ بخريطة طريق واضحة — أهداف، مراحل، مخرجات قابلة للقياس، ومخاطر معروفة قبل التنفيذ. ممنوع التنفيذ قبل اعتماد الخطة من Loqman.',
    managerId: 'dr_sara',
    color: 'from-blue-600 to-indigo-700',
    badgeBg: 'bg-blue-500/10 text-blue-400 border-blue-500/30',
    accentBorder: 'border-blue-500/40',
  },
  execution: {
    id: 'execution',
    name: 'Forge',
    nameArabic: 'Forge — Execution & Delivery',
    description:
      'عقد التنفيذ: تنفيذ خطة Compass بدقة، تسليم مخرجات قابلة للفحص، وبوابة جودة صارمة قبل أي تسليم. لا تسليم نهائي بدون توقيع Prof. Yasmin.',
    managerId: 'eng_tarek',
    color: 'from-amber-600 to-orange-700',
    badgeBg: 'bg-amber-500/10 text-amber-400 border-amber-500/30',
    accentBorder: 'border-amber-500/40',
  },
};

export const DEFAULT_COMPANY_MEMBERS: CompanyMember[] = [
  // Executive — CEO
  {
    id: 'custom',
    node_id: 'gm-root',
    natural_name: 'Loqman',
    english_name: 'Loqman',
    title: 'الرئيس التنفيذي · CEO',
    role_type: 'custom',
    department: 'executive',
    is_manager: true,
    system_prompt:
      'أنت Loqman، الرئيس التنفيذي للشركة. تقود فريقين: Compass للتخطيط بقيادة Dr. Sara، وForge للتنفيذ بقيادة Eng. Tarek. التخطيط أولاً، والتنفيذ يسير على الخطة المعتمدة ويمر ببوابة الجودة. تقترح التغييرات ولا تنفذها إلا بعد موافقة المستخدم.',
    contract: {
      model_id: 'deepseek-v4.1-flash-free',
      variant: 'max',
      task_depth: 'Deep',
      timeout_sec: 180,
      max_retries: 3,
      strict_json: true,
    },
  },

  // Compass — planning
  {
    id: 'planner',
    node_id: 'compass-lead',
    natural_name: 'Dr. Sara',
    english_name: 'Dr. Sara',
    title: 'مدير التخطيط والإستراتيجية',
    role_type: 'planner',
    department: 'planning',
    is_manager: true,
    system_prompt:
      'أنت Dr. Sara، مديرة Compass للتخطيط والإستراتيجية. تفكك كل هدف إلى مراحل متسلسلة قابلة للقياس، بمالك ومدة ومخرج متوقع لكل مهمة، وسجل مخاطر قبل التنفيذ. لا تنفيذ قبل اعتماد الخطة.',
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
    id: 'researcher',
    node_id: 'compass-research',
    natural_name: 'Khaled',
    english_name: 'Khaled',
    title: 'أخصائي الأبحاث والاستكشاف',
    role_type: 'researcher',
    department: 'planning',
    is_manager: false,
    system_prompt:
      'أنت Khaled، أخصائي الأبحاث والاستكشاف في Compass. تجمع الأدلة وتفصل الحقيقة عن الافتراض، وتوثق مصدر كل معلومة، ولا تخترع معرفة خارجية.',
    contract: {
      model_id: 'deepseek-v4.1-flash-free',
      variant: 'low',
      task_depth: 'Standard',
      timeout_sec: 90,
      max_retries: 2,
      strict_json: true,
    },
  },
  {
    id: 'data_extractor',
    node_id: 'compass-data',
    natural_name: 'Layla',
    english_name: 'Layla',
    title: 'أخصائية استخراج البيانات والنمذجة',
    role_type: 'data_extractor',
    department: 'planning',
    is_manager: false,
    system_prompt:
      'أنت Layla، أخصائية البيانات والنمذجة في Compass. تحولين المدخلات غير المنظمة إلى مخططات JSON صارمة، وتعلنين أي حقل غامض بدل التخمين.',
    contract: {
      model_id: 'deepseek-v4.1-flash-free',
      variant: 'low',
      task_depth: 'Quick',
      timeout_sec: 90,
      max_retries: 2,
      strict_json: true,
    },
  },
  {
    id: 'report_writer',
    node_id: 'compass-docs',
    natural_name: 'Nour',
    english_name: 'Nour',
    title: 'أخصائي توثيق الأنظمة والتقارير',
    role_type: 'report_writer',
    department: 'planning',
    is_manager: false,
    system_prompt:
      'أنت Nour، كاتب التقارير في Compass. تحوّل الخطة والبحث إلى مستند تنفيذي واحد: هدف، نطاق، مراحل، مخاطر، ونقاط اعتماد — بدون حقائق غير موثقة.',
    contract: {
      model_id: 'deepseek-v4.1-flash-free',
      variant: 'medium',
      task_depth: 'Standard',
      timeout_sec: 120,
      max_retries: 3,
      strict_json: false,
    },
  },

  // Forge — execution
  {
    id: 'analyst',
    node_id: 'forge-lead',
    natural_name: 'Eng. Tarek',
    english_name: 'Eng. Tarek',
    title: 'مدير التنفيذ والهندسة',
    role_type: 'analyst',
    department: 'execution',
    is_manager: true,
    system_prompt:
      'أنت Eng. Tarek، مدير Forge. تحوّل خطة Compass المعتمدة إلى حزم عمل قابلة للتنفيذ، توزعها على الأخصائيين وتضبط مخاطرها. لا تخترع نطاقًا: أي قرار ناقص يصعد إلى Loqman.',
    contract: {
      model_id: 'deepseek-v4.1-flash-free',
      variant: 'max',
      task_depth: 'Deep',
      timeout_sec: 150,
      max_retries: 3,
      strict_json: true,
    },
  },
  {
    id: 'coder',
    node_id: 'forge-code',
    natural_name: 'Ahmed',
    english_name: 'Ahmed',
    title: 'مهندس التنفيذ والبرمجة',
    role_type: 'coder',
    department: 'execution',
    is_manager: false,
    system_prompt:
      'أنت Ahmed، مهندس التنفيذ والبرمجة في Forge. تنفذ حزمة العمل بأنماط المشروع نفسها، بكود نظيف ومُختبر، وتوضح بالضبط ما تم تغييره وما لم يتم.',
    contract: {
      model_id: 'deepseek-v4.1-flash-free',
      variant: 'high',
      task_depth: 'Standard',
      timeout_sec: 150,
      max_retries: 3,
      strict_json: true,
    },
  },
  {
    id: 'reviewer',
    node_id: 'forge-quality',
    natural_name: 'Prof. Yasmin',
    english_name: 'Prof. Yasmin',
    title: 'مدير الجودة والاعتماد',
    role_type: 'reviewer',
    department: 'execution',
    is_manager: true,
    system_prompt:
      'أنت Prof. Yasmin، بوابة الجودة في Forge. تدققين كل تسليم مقابل الخطة وعقد التنفيذ، وتصدرين قرار اعتماد أو رفض واضحًا بالأسباب، وتفحصين الحالات الطرفية والانحراف عن النطاق.',
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
    id: 'summarizer',
    node_id: 'forge-summary',
    natural_name: 'Omar',
    english_name: 'Omar',
    title: 'أخصائي التلخيص والتقارير',
    role_type: 'summarizer',
    department: 'execution',
    is_manager: false,
    system_prompt:
      'أنت Omar، الملخص التنفيذي في Forge. تلخص النتائج في موجز قصير: ما تم تسليمه، ما تم التحقق منه، ما فشل، وما يحتاج قرارًا من Loqman. لا تدعي عملًا بلا مخرج.',
    contract: {
      model_id: 'deepseek-v4.1-flash-free',
      variant: 'low',
      task_depth: 'Quick',
      timeout_sec: 60,
      max_retries: 2,
      strict_json: false,
    },
  },
  {
    id: 'translator',
    node_id: 'forge-localization',
    natural_name: 'Mona',
    english_name: 'Mona',
    title: 'أخصائية الترجمة والتعريب',
    role_type: 'translator',
    department: 'execution',
    is_manager: false,
    system_prompt:
      'أنت Mona، أخصائية الترجمة والتعريب في Forge. تترجمين المحتوى التقني بدقة مع الحفاظ على المصطلحات وأسماء الكود والكيانات كما هي، ومراجعة الاتساق قبل التسليم.',
    contract: {
      model_id: 'deepseek-v4.1-flash-free',
      variant: 'low',
      task_depth: 'Quick',
      timeout_sec: 60,
      max_retries: 2,
      strict_json: false,
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
