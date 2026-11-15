/** Shared shapes of the RuseBot UI (agents, teams, reasoning levels). */

export type AgentStatus = 'Active' | 'Attention' | 'Queued' | 'Waiting';

export type ModelReasoning = 'none' | 'minimal' | 'low' | 'medium' | 'high' | 'xhigh' | 'max';

export interface Team {
  id: string;
  name: string;
  desc: string;
  mgr: string | null;
  color: string;
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
  /** Linked backend employee profile (by profile id). Null = resolved by fallback rules. */
  profileId?: string | null;
}
