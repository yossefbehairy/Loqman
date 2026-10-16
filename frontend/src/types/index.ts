export type NodeExecutionStatus =
  | 'pending'
  | 'ready'
  | 'running'
  | 'succeeded'
  | 'failed'
  | 'skipped'
  | 'cancelled';

export type TaskDepth = 'Quick' | 'Standard' | 'Deep';

export interface AgentNodeData {
  display_name?: string;
  role_type?: string;
  model_id?: string;
  variant?: string;
  task_depth?: TaskDepth;
  instructions?: string;
  timeout?: number;
  // Live execution state injected dynamically
  executionStatus?: NodeExecutionStatus;
  attemptCount?: number;
  durationMs?: number;
  output?: any;
  errorMessage?: string;
}

export interface StartNodeData {
  label?: string;
  initialInput?: string;
  executionStatus?: NodeExecutionStatus;
  output?: any;
}

export interface OutputNodeData {
  label?: string;
  executionStatus?: NodeExecutionStatus;
  output?: any;
}

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
}

export interface ValidationResult {
  valid: boolean;
  errors: string[];
  topological_order: string[];
}

export interface WorkflowDefinition {
  nodes: any[];
  edges: any[];
}

export interface Workflow {
  id: string;
  name: string;
  description?: string;
  definition_json: string;
  version: number;
  created_at: string;
  updated_at: string;
}

export interface NodeExecution {
  id: string;
  execution_id: string;
  node_id: string;
  role_type?: string;
  model_id?: string;
  native_variant_id?: string;
  task_depth: string;
  status: NodeExecutionStatus;
  attempt_count: number;
  input_json?: string;
  output_json?: string;
  error_message?: string;
  started_at?: string;
  finished_at?: string;
  duration_ms?: number;
}

export interface ExecutionEvent {
  id: string;
  execution_id: string;
  node_execution_id?: string;
  event_type: string;
  event_json: string;
  created_at: string;
}

export interface WorkflowExecution {
  id: string;
  workflow_id: string;
  status: string;
  input_json?: string;
  final_output_json?: string;
  workflow_snapshot_json?: string;
  runtime_status?: string;
  started_at?: string;
  finished_at?: string;
  error_summary?: string;
  node_executions: NodeExecution[];
  events: ExecutionEvent[];
}

export interface TeamMessage {
  execution_id: string;
  turn: number;
  from_node_id: string;
  from_display_name: string;
  from_role: string;
  to_node_ids: string[];
  excerpt: string;
  full_output_ref: string;
  timestamp: string;
  created_at?: string;
}

export interface TeamMessageListResponse {
  execution_id: string;
  count: number;
  messages: TeamMessage[];
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

export type DepartmentId = 'planning' | 'research' | 'quality';

export interface ExecutionContract {
  model_id: string;
  variant: string;
  task_depth: TaskDepth;
  timeout_sec: number;
  max_retries: number;
  strict_json: boolean;
}

export interface CompanyMember {
  id: string;
  node_id: string;
  natural_name: string;        // e.g. "د. سارة", "م. طارق", "أ. ياسمين", "أحمد", "خالد", "ليلى"
  english_name: string;        // e.g. "Dr. Sara", "Eng. Tarek"
  title: string;               // e.g. "مدير التخطيط والإستراتيجية"
  role_type: string;           // "planner" | "analyst" | "reviewer" | "general_manager"
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

