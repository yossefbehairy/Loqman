export type Page = 'ceo' | 'company' | 'inbox' | 'conversations' | 'stream' | 'results' | 'settings';

export type CompanyViewMode = 'graph' | 'org';

export type AgentStatus = 'Active' | 'Attention' | 'Queued' | 'Waiting';

export type ModelReasoning = 'none' | 'minimal' | 'low' | 'medium' | 'high' | 'xhigh' | 'max';

export interface ModelOption {
  id: string;
  name: string;
  reason: ModelReasoning[];
}

export interface Team {
  id: string;
  name: string;
  desc: string;
  mgr: string | null;
  color: string;
}

export interface TeamTurn {
  turn: number;
  from: string;
  to: string;
  role: string;
  text: string;
  time: string;
  fullOutput?: Record<string, any>;
}

export interface TaskDeliverable {
  id: string;
  title: string;
  agentId: string;
  agentName: string;
  department: string;
  status: string;
  summary: string;
  tasksCount?: number;
  heldCount?: number;
  fullJson?: Record<string, any>;
  turns: TeamTurn[];
}

export interface Agent {
  id: string;
  name: string;
  role: string;
  team: string | null;
  mgr: boolean;
  model: string;
  reason: ModelReasoning;
  timeout: number;
  json: boolean;
  prompt: string;
  status: AgentStatus;
  activeTask?: TaskDeliverable;
  /** Linked backend employee profile (by profile id). Null = resolved by fallback rules. */
  profileId?: string | null;
}

export interface Message {
  me: boolean;
  t: string;
  time: string;
  suggestedMission?: { title: string; input_json: Record<string, any> };
}

export interface InboxItem {
  id: string;
  kind: 'review' | 'notice';
  title: string;
  sub: string;
  time: string;
  st?: 'pending' | 'approved' | 'changes';
  read?: boolean;
  target?: string;
  deliverable?: TaskDeliverable;
}

export interface RunReceipt {
  id: string;
  d: number;
  pipe: string;
  st: 'success' | 'failed';
  dur: number;
  tok: number;
}

export interface AppSettings {
  theme: 'dark' | 'light';
  defModel: string;
  reason: ModelReasoning;
  timeout: number;
  retries: number;
  json: boolean;
  fallback: boolean;
  reqReview: boolean;
  notifyReview: boolean;
  notifyFail: boolean;
}

export interface GraphOptions {
  source: 'live' | 'sample';
  trails: boolean;
  motion: boolean;
}

export interface CameraState {
  x: number;
  y: number;
  k: number;
}
