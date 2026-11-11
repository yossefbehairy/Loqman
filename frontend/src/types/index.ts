export type TaskDepth = 'Quick' | 'Standard' | 'Deep';

export interface ModelCost {
  input: number;
  output: number;
}

export interface ModelCatalogEntry {
  id: string;
  name: string;
  provider_id: string;
  cost: ModelCost;
  variants: string[];
  is_free: boolean;
  status: string;
}

export interface AgentProfile {
  id: string;
  name: string;
  role_type: string;
  description: string;
  instructions: string;
  task_depth: TaskDepth;
  default_model_id?: string | null;
  default_variant_id?: string | null;
}

export interface RuntimeHealth {
  status: string;
  service_url: string;
  default_model_status: string;
  models_count: number;
  free_models_count: number;
}

// ==========================================
// Virtual AI Organization & Executive Types (R1 - R5)
// ==========================================

export type ActiveView = 'chat' | 'company' | 'map' | 'stream' | 'settings';

export type DepartmentId = 'planning' | 'execution';

export interface ExecutionContract {
  model_id: string;
  variant: string;
  task_depth: TaskDepth;
  timeout_sec: number;
  max_retries: number;
  strict_json: boolean;
}

export interface CompanyMember {
  id: string;                  // backend AgentProfile id (stable)
  node_id: string;
  natural_name: string;        // e.g. "Dr. Sara", "Eng. Tarek", "Loqman"
  english_name: string;        // e.g. "Dr. Sara", "Eng. Tarek", "Loqman"
  title: string;               // e.g. "مدير التخطيط والإستراتيجية"
  role_type: string;           // "planner" | "analyst" | "reviewer" | "custom"
  department: DepartmentId | 'executive';
  is_manager: boolean;         // true = 👑 Manager, false = 💼 Specialist
  avatar?: string;
  system_prompt: string;
  contract: ExecutionContract;
}

export interface SuggestedMission {
  title: string;
  input_json: Record<string, any>;
}

export interface ManagerConsultationRequest {
  manager_id?: string;
  message: string;
  history?: Array<{ role: string; content: string }>;
}

export interface ManagerConsultationResponse {
  reply: string;
  manager_id: string;
  manager_name: string;
  title: string;
  suggested_mission?: SuggestedMission;
}

// ==========================================
// CEO missions — real end-to-end AI execution
// ==========================================

export type MissionStatus =
  | 'queued'
  | 'planning'
  | 'running'
  | 'succeeded'
  | 'partially_succeeded'
  | 'failed'
  | 'cancelled';

export type MissionTaskStatus =
  | 'queued'
  | 'running'
  | 'succeeded'
  | 'failed'
  | 'skipped'
  | 'cancelled';

export interface MissionTaskOut {
  id: string;
  task_key: string;
  employee_role: string;
  title: string;
  status: MissionTaskStatus;
  model_id?: string;
  variant?: string;
  attempt_count: number;
  output?: any;
  error_message?: string;
  duration_ms?: number;
  session_id?: string;
  started_at?: string;
  finished_at?: string;
}

export interface MissionOut {
  id: string;
  title: string;
  objective: string;
  status: MissionStatus;
  model_id?: string;
  variant?: string;
  plan?: any;
  final_summary?: any;
  error_summary?: string;
  tasks: MissionTaskOut[];
  created_at?: string;
  started_at?: string;
  finished_at?: string;
}

// ==========================================
// CEO control plane — ask the CEO, approve proposals
// ==========================================

export type CeoActionKind = 'update_contract' | 'new_profile' | 'new_team';

export type CeoActionStatus = 'pending' | 'approved' | 'denied' | 'failed';

export interface CeoProposedAction {
  id: string;
  kind: CeoActionKind;
  summary: string;
}

export interface CeoAskResponse {
  reply: string;
  actions_proposed: CeoProposedAction[];
  iterations: number;
}

/** Live stage of an in-flight CEO ask (GET /api/ceo/ask-progress/{id}). */
export interface CeoAskProgress {
  stage: string;
  detail: string;
  iteration: number;
  started_at?: string | null;
  updated_at?: string | null;
  done: boolean;
}

export interface CeoCreatedProfile {
  id: string;
  name: string;
  role_type: string;
}

export interface CeoAction {
  id: string;
  kind: CeoActionKind;
  summary: string;
  status: CeoActionStatus;
  created_by?: string | null;
  decided_at?: string | null;
  decision_note?: string | null;
  /** Profiles really created by an approved new_profile/new_team action. */
  created_profiles?: CeoCreatedProfile[];
  /** Proposed team name for an approved new_team action. */
  team_name?: string | null;
}

// ==========================================
// CEO chat threads — the local sidebar history
// ==========================================

/**
 * One CEO question + its recorded answer. Threads live in localStorage only
 * (the ask endpoint is stateless); the sidebar lists them newest-first.
 */
export interface CeoTurn {
  question: string;
  reply: string;
  /** Set when the ask failed — rendered as an error card in the thread. */
  error?: string;
  /** Actions the answer proposed (still awaiting the user's decision). */
  proposed: CeoProposedAction[];
  /** Creation time (ms) — drives the per-turn clock. */
  ts: number;
}

/**
 * A CEO conversation (one selectable item in the CEO sidebar). Since the
 * backend owns the conversation memory, a thread is purely organizational:
 * follow-up questions asked with the thread open are APPENDED as turns, so
 * one ongoing chat instead of a new entry per message.
 */
export interface CeoThread {
  id: string;
  /** First question — the sidebar label and the thread heading. */
  question: string;
  /** Latest turn's reply (kept for search + the sidebar meta line). */
  reply: string;
  /** Latest turn's error, if any. */
  error?: string;
  proposed: CeoProposedAction[];
  ts: number;
  /** Chronological Q&A turns (one entry per exchange in this thread). */
  turns: CeoTurn[];
}

