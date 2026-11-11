import type { Team, Agent, InboxItem, RunReceipt, AppSettings, ModelOption } from '../types/agentos';

export const TEAM_COLORS = [
  '#fafafa', // White — primary arc
  '#a1a1aa', // Zinc 400 — secondary arc
  '#71717a', // Zinc 500
  '#d4d4d8', // Zinc 300
  '#52525b', // Zinc 600
  '#e4e4e7', // Zinc 200
  '#3f3f46', // Zinc 700
];

export const MODELS: ModelOption[] = [
  // Static fallback only: the live free catalog (GET /api/models/free) replaces
  // this list at startup. Every id here must exist in the live catalog.
  { id: 'deepseek-v4.1-flash-free', name: 'DeepSeek V4.1 Flash Free', reason: ['low', 'high', 'max'] },
  { id: 'deepseek-ai/deepseek-v4.1-flash', name: 'DeepSeek V4.1 Flash (Nvidia)', reason: ['none', 'low', 'high', 'max'] },
  { id: 'deepseek-v4-flash', name: 'DeepSeek V4 Flash (AgentRouter)', reason: ['low', 'high', 'max'] },
  { id: 'nemotron-3.5-super-vl-free', name: 'Nemotron 3.5 Super VL Free', reason: ['none', 'minimal', 'low', 'medium', 'high', 'xhigh'] },
];

export const STATUS_LABELS: Record<string, string> = {
  Active: 'Ready',
  Attention: 'Needs attention',
  Queued: 'Assignment queued',
  Waiting: 'Waiting for verified input',
};

export const INITIAL_SETTINGS: AppSettings = {
  theme: 'dark',
  defModel: 'deepseek-v4.1-flash-free',
  reason: 'low',
  timeout: 180,
  retries: 2,
  json: true,
  fallback: false,
  reqReview: true,
  notifyReview: true,
  notifyFail: true,
};

// Loqman company organization (two teams, one CEO).
// The teams carry their written contract in `desc`; the members carry their
// execution contract in the model/reasoning/timeout/JSON fields below.
export const INITIAL_TEAMS: Team[] = [
  {
    id: 't_planning',
    name: 'Compass',
    desc: 'عقد التخطيط: خريطة طريق بأهداف ومراحل ومخرجات قابلة للقياس ومخاطر معروفة قبل أي تنفيذ — ولا تنفيذ قبل اعتماد Loqman.',
    mgr: 'a_sara',
    color: '#fafafa',
  },
  {
    id: 't_execution',
    name: 'Forge',
    desc: 'عقد التنفيذ: تنفيذ خطة Compass المعتمدة وتسليم مخرجات قابلة للفحص — ولا تسليم نهائي قبل توقيع بوابة الجودة (Prof. Yasmin).',
    mgr: 'a_tarek',
    color: '#a1a1aa',
  },
];

export const INITIAL_AGENTS: Agent[] = [
  // CEO — Loqman
  {
    id: 'a1',
    name: 'Loqman',
    role: 'الرئيس التنفيذي · CEO',
    team: null,
    mgr: true,
    model: 'deepseek-v4.1-flash-free',
    reason: 'max',
    timeout: 180,
    json: true,
    prompt:
      'You are Loqman, CEO of the company. You lead two teams: Compass for planning and Forge for execution. Planning comes first; execution follows the approved plan and passes the quality gate. Propose changes — never execute them without approval. Always write people and team names in English exactly as given (Loqman, Compass, Forge, Dr. Sara, Eng. Tarek, Prof. Yasmin), even inside Arabic sentences.',
    status: 'Active',
    profileId: 'custom',
  },

  // Compass — planning
  {
    id: 'a_sara',
    name: 'Dr. Sara',
    role: 'مدير التخطيط والإستراتيجية',
    team: 't_planning',
    mgr: true,
    model: 'deepseek-v4.1-flash-free',
    reason: 'high',
    timeout: 120,
    json: true,
    prompt:
      'You are Dr. Sara, head of Compass, the planning team. Turn every goal into measurable milestones with owners, dependencies and risks. No execution before the plan is approved.',
    status: 'Active',
    profileId: 'planner',
  },
  {
    id: 'a_khaled',
    name: 'Khaled',
    role: 'أخصائي الأبحاث والاستكشاف',
    team: 't_planning',
    mgr: false,
    model: 'deepseek-v4.1-flash-free',
    reason: 'low',
    timeout: 90,
    json: true,
    prompt:
      'You are Khaled, research specialist in Compass. Gather evidence, separate facts from assumptions, and never invent sources.',
    status: 'Active',
    profileId: 'researcher',
  },
  {
    id: 'a_layla',
    name: 'Layla',
    role: 'أخصائية استخراج البيانات والنمذجة',
    team: 't_planning',
    mgr: false,
    model: 'deepseek-v4.1-flash-free',
    reason: 'low',
    timeout: 90,
    json: true,
    prompt:
      'You are Layla, data & schemas specialist in Compass. Convert unstructured input into strict, validated JSON and flag ambiguous fields instead of guessing.',
    status: 'Active',
    profileId: 'data_extractor',
  },
  {
    id: 'a_nour',
    name: 'Nour',
    role: 'أخصائي توثيق الأنظمة والتقارير',
    team: 't_planning',
    mgr: false,
    model: 'deepseek-v4.1-flash-free',
    reason: 'medium',
    timeout: 120,
    json: false,
    prompt:
      'You are Nour, report writer in Compass. Turn the plan into a clear executive document with acceptance criteria and approval points.',
    status: 'Active',
    profileId: 'report_writer',
  },

  // Forge — execution
  {
    id: 'a_tarek',
    name: 'Eng. Tarek',
    role: 'مدير التنفيذ والهندسة',
    team: 't_execution',
    mgr: true,
    model: 'deepseek-v4.1-flash-free',
    reason: 'max',
    timeout: 150,
    json: true,
    prompt:
      'You are Eng. Tarek, head of Forge, the execution team. Execute only the approved Compass plan, manage risk, and escalate missing decisions instead of guessing.',
    status: 'Active',
    profileId: 'analyst',
  },
  {
    id: 'a_ahmed',
    name: 'Ahmed',
    role: 'مهندس التنفيذ والبرمجة',
    team: 't_execution',
    mgr: false,
    model: 'deepseek-v4.1-flash-free',
    reason: 'high',
    timeout: 150,
    json: true,
    prompt:
      "You are Ahmed, implementation engineer in Forge. Ship clean, typed code that matches the project's patterns and report exactly what changed.",
    status: 'Active',
    profileId: 'coder',
  },
  {
    id: 'a_yasmin',
    name: 'Prof. Yasmin',
    role: 'بوابة الجودة والاعتماد',
    team: 't_execution',
    mgr: false,
    model: 'deepseek-v4.1-flash-free',
    reason: 'max',
    timeout: 180,
    json: true,
    prompt:
      'You are Prof. Yasmin, the quality gate in Forge. Audit every deliverable against the plan and issue a clear approval or rejection with reasons.',
    status: 'Active',
    profileId: 'reviewer',
  },
  {
    id: 'a_omar',
    name: 'Omar',
    role: 'أخصائي التلخيص والتقارير',
    team: 't_execution',
    mgr: false,
    model: 'deepseek-v4.1-flash-free',
    reason: 'low',
    timeout: 60,
    json: false,
    prompt:
      'You are Omar, executive summarizer in Forge. Turn results into a short brief: shipped, verified, failed, and what needs a decision.',
    status: 'Active',
    profileId: 'summarizer',
  },
  {
    id: 'a_mona',
    name: 'Mona',
    role: 'أخصائية الترجمة والتعريب',
    team: 't_execution',
    mgr: false,
    model: 'deepseek-v4.1-flash-free',
    reason: 'low',
    timeout: 60,
    json: false,
    prompt:
      'You are Mona, localization specialist in Forge. Translate accurately, preserve technical terms and formatting.',
    status: 'Active',
    profileId: 'translator',
  },
];

export const INITIAL_INBOX: InboxItem[] = [];

export const INITIAL_RUNS: RunReceipt[] = [];

export const INITIAL_MESSAGES: Record<string, { me: boolean; t: string; time: string }[]> = {
  a1: [],
};
