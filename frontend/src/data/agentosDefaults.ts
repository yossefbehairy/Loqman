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

// Clean-slate workspace: no sample teams, employees, inbox items, runs,
// or messages. Everything is created by the user (or arrives from real
// backend missions) after first launch.
export const INITIAL_TEAMS: Team[] = [];

export const INITIAL_AGENTS: Agent[] = [
  {
    id: 'a1',
    name: 'Adel Mansour',
    role: 'CEO · Company briefing',
    team: null,
    mgr: true,
    model: 'deepseek-v4.1-flash-free',
    reason: 'low',
    timeout: 180,
    json: true,
    prompt: 'You are the General Manager & Chief Orchestrator. Coordinate company goals and ensure quality delivery.',
    status: 'Active',
    profileId: 'custom',
  },
];

export const INITIAL_INBOX: InboxItem[] = [];

export const INITIAL_RUNS: RunReceipt[] = [];

export const INITIAL_MESSAGES: Record<string, { me: boolean; t: string; time: string }[]> = {
  a1: [],
};
