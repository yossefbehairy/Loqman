import { call, openStream, type StreamHandle, type StreamMessage } from '../../api/transport';

export interface Conversation {
  id: string;
  title: string;
  workspace_id: string | null;
  profile_id: string | null;
  created_at: string;
  updated_at: string;
}

export interface ConvMessage {
  seq: number;
  conversation_id: string;
  role: 'user' | 'assistant';
  content: string;
  client_message_id: string | null;
  created_at: string;
}

export type RunStatus = 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled' | 'interrupted';
export const ACTIVE_STATUSES: RunStatus[] = ['queued', 'running'];

export interface RunSnapshot {
  id: string;
  conversation_id: string;
  engine: string;
  model_id: string;
  status: RunStatus;
  user_message_seq: number | null;
  assistant_message_seq: number | null;
  assistant_content: string | null;
  error: string | null;
  tool_mode: string;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export interface RunEvent {
  id: number;
  run_id: string;
  type: string;
  data: Record<string, unknown>;
}

export interface Approval {
  id: string;
  run_id: string;
  action_summary: string;
  scope: string;
  status: string;
  expires_at: string | null;
}

export interface Draft {
  id: string;
  run_id: string;
  filename: string;
  mime: string;
  status: 'pending_review' | 'approved' | 'rejected' | string;
  content_sha256: string;
  size_bytes: number;
  downloadable: boolean;
  content: string | null;
}

export interface EngineStatus {
  engine_id: string;
  name?: string;
  display_name?: string;
  install_state: string;
  auth_state?: string;
  version?: string | null;
  executable?: string | null;
  detail?: string | null;
  adapter_supported?: boolean;
  [key: string]: unknown;
}

export interface EngineSetupInfo {
  engine_id: string;
  login_method: string;
  command: string[] | null;
  instructions: string;
}

export interface EngineModel {
  id: string;
  name: string;
  provider_id?: string;
  status?: string;
  is_free?: boolean;
  variants?: string[];
}

export interface EngineCapabilities {
  conversation_runs?: boolean;
  [key: string]: unknown;
}

export interface Profile {
  id: string;
  name: string;
  role_type: string;
  description: string;
  instructions: string;
  default_model_id?: string | null;
  engine?: string | null;
  task_depth?: string;
}

export interface MissionTask {
  id: string;
  title?: string;
  status: string;
  profile_id?: string | null;
  [key: string]: unknown;
}

export interface Mission {
  id: string;
  title: string;
  objective: string;
  status: string;
  tasks: MissionTask[];
  created_at?: string;
  [key: string]: unknown;
}

export const listConversations = () =>
  call<{ conversations: Conversation[] }>('conversations.list').then((r) => r.conversations);

export const createConversation = (title: string) => call<Conversation>('conversations.create', { title });

export const listMessages = (conversationId: string) =>
  call<{ messages: ConvMessage[] }>('conversations.messages', { conversation_id: conversationId, limit: 500 }).then(
    (r) => r.messages
  );

export const importLegacyCeo = () =>
  call<{ conversation_id: string; imported: number }>('conversations.import_ceo');

export interface StartTurnInput {
  conversationId: string;
  engine: string;
  modelId: string;
  prompt: string;
  requestKey?: string;
}

export const startTurn = (input: StartTurnInput) =>
  call<RunSnapshot>('runs.start_turn', {
    conversation_id: input.conversationId,
    engine: input.engine,
    model_id: input.modelId,
    prompt: input.prompt,
    ...(input.requestKey ? { request_key: input.requestKey } : {}),
  });

export const getRun = (runId: string) => call<RunSnapshot>('runs.get', { run_id: runId });
export const cancelRun = (runId: string) => call<RunSnapshot>('runs.cancel', { run_id: runId });

export const runEvents = (runId: string, afterId = 0) =>
  call<{ events: RunEvent[] }>('runs.events', { run_id: runId, after_id: afterId }).then((r) => r.events);

export const listApprovals = (runId?: string) =>
  call<{ approvals: Approval[] }>('approvals.list', runId ? { run_id: runId } : {}).then((r) => r.approvals);

export const decideApproval = (runId: string, approvalId: string, decision: 'approve' | 'deny') =>
  call<Approval>('approvals.decide', { run_id: runId, approval_id: approvalId, decision });

export const listDrafts = (runId: string) =>
  call<{ drafts: Draft[] }>('deliverables.list', { run_id: runId }).then((r) => r.drafts);

export const createDraft = (runId: string, filename?: string) =>
  call<Draft>('deliverables.create', { run_id: runId, ...(filename ? { filename } : {}) });

export const getDraft = (runId: string, draftId: string) =>
  call<Draft>('deliverables.get', { run_id: runId, draft_id: draftId });

export const decideDraft = (
  runId: string,
  draftId: string,
  decision: 'approve' | 'reject',
  contentSha256: string
) => call<Draft>('deliverables.decide', { run_id: runId, draft_id: draftId, decision, content_sha256: contentSha256 });

export const draftContent = (runId: string, draftId: string) =>
  call<{ content: string; filename: string; media_type: string }>('deliverables.content', {
    run_id: runId,
    draft_id: draftId,
  });

export const engineStatuses = () => call<{ engines: EngineStatus[] }>('engines.status').then((r) => r.engines);
export const engineCapabilities = () => call<Record<string, EngineCapabilities>>('engines.capabilities');
export const engineModels = (engineId: string) =>
  call<{ models: EngineModel[] }>('engines.models', { engine_id: engineId }).then((r) => r.models);
export const engineCheck = (engineId: string) =>
  call<{ engine_id: string; error_code: string | null; runtime: unknown }>('engines.check', { engine_id: engineId });
export const freeModels = () => call<{ models: EngineModel[] }>('models.free').then((r) => r.models);

export const listProfiles = () => call<{ profiles: Profile[] }>('profiles.list').then((r) => r.profiles);
export const listMissions = () => call<{ missions: Mission[] }>('missions.list').then((r) => r.missions);

export function followRun(
  runId: string,
  onEvent: (type: string, data: Record<string, unknown>, id: number | null | undefined) => void,
  onEnd: () => void,
  lastEventId = 0
): Promise<StreamHandle> {
  return openStream(
    'run',
    runId,
    (msg: StreamMessage) => {
      if (msg.type === 'stream_end') {
        onEnd();
        return;
      }
      onEvent(msg.event ?? '', (msg.data ?? {}) as Record<string, unknown>, msg.id);
    },
    lastEventId
  );
}

export function isTerminal(status: string): boolean {
  return !ACTIVE_STATUSES.includes(status as RunStatus);
}

export interface CeoAction {
  id: string;
  kind: string;
  summary: string;
  status: string;
}

export const listCeoActions = () =>
  call<{ actions: CeoAction[] }>('ceo.actions', { status: 'pending' }).then((r) => r.actions);
export const approveCeoAction = (id: string) => call<CeoAction>('ceo.approve', { action_id: id, confirm: true });
export const denyCeoAction = (id: string) => call<CeoAction>('ceo.deny', { action_id: id, confirm: true });

export const createMission = (objective: string, title: string, employeeRoles: string[], modelId?: string) =>
  call<Mission>('missions.create', {
    objective,
    title,
    employee_roles: employeeRoles,
    ...(modelId ? { model_id: modelId } : {}),
    request_key: `ui-${crypto.randomUUID()}`,
  });

export const updateProfileInstructions = (profileId: string, instructions: string) =>
  call<Profile>('profiles.update', { profile_id: profileId, instructions });

// -- agents (worker profiles) and the CEO --------------------------------------------------

export const CEO_PROFILE_ID = 'custom';

export const createProfile = (input: { name: string; role: string; instructions: string; modelId?: string; engine?: string }) =>
  call<Profile>('profiles.create', {
    name: input.name,
    role_type: 'custom',
    description: input.role,
    instructions: input.instructions,
    ...(input.modelId ? { default_model_id: input.modelId } : {}),
    ...(input.engine ? { engine: input.engine } : {}),
  });

export const deleteProfile = (profileId: string) => call<{ deleted: string }>('profiles.delete', { profile_id: profileId });

export interface CeoAnswer {
  reply: string;
  actions_proposed: Array<{ id: string; kind: string; summary: string }>;
  agents_created: Array<{ id: string; name: string; role_type: string }>;
  iterations: number;
  fast: boolean;
}

export const askCeo = (question: string, modelId: string, requestId: string) =>
  call<CeoAnswer>('ceo.ask', { question, model_id: modelId, request_id: requestId });

export const addMessage = (conversationId: string, role: 'user' | 'assistant', content: string) =>
  call<ConvMessage>('conversations.add_message', { conversation_id: conversationId, role, content });

export const engineSetup = (engineId: string) => call<EngineSetupInfo>('engines.setup', { engine_id: engineId });
