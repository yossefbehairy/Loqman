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

export type EngineStateId = 'ready' | 'needs_login' | 'not_installed' | 'unverified' | 'error';

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
  /** Added by the worker's provider catalog; derive it from install/auth state when absent. */
  state?: EngineStateId;
  title?: string;
  vendor?: string;
  checked_at?: string;
}

/** What engines.check reports: a fresh discovery of the CLI plus (when it is installed) the runtime's own health. */
export interface EngineCheckResult {
  engine_id: string;
  checked_at?: string;
  error_code: string | null;
  discovery?: Pick<EngineStatus, 'install_state' | 'auth_state' | 'version' | 'executable' | 'detail'> | null;
  runtime: { status?: string; default_model_status?: string; models_count?: number } | null;
}

export interface EngineSetupInfo {
  engine_id: string;
  login_method?: string;
  command?: string[] | null;
  instructions: string;
  state?: EngineStateId;
  install?: { command: string[] | null; url: string | null };
  login?: { command: string[] | null };
  docs_url?: string | null;
}

export interface EngineTestResult {
  engine_id: string;
  model_id: string | null;
  ok: boolean;
  duration_ms: number;
  reply_preview: string | null;
  error_code: string | null;
  error_message: string | null;
}

export interface EngineOpenSetupResult {
  launched: boolean;
  reason: string | null;
}

export interface UsageByEngine {
  key: string;
  events: number;
  succeeded: number;
  failed: number;
  total_duration_ms: number;
  avg_duration_ms: number;
  tokens_in: number;
  tokens_out: number;
  tokens_cache_read: number;
  token_usage_unknown_events: number;
}

export interface UsageSummary {
  by_engine?: UsageByEngine[];
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

export const createConversation = (title: string, profileId?: string) =>
  call<Conversation>('conversations.create', { title, ...(profileId ? { profile_id: profileId } : {}) });

/** Bind a conversation that has no agent yet to a profile (the worker then applies that profile's instructions). */
export const bindConversationProfile = (conversationId: string, profileId: string) =>
  call<Conversation>('conversations.bind_profile', { conversation_id: conversationId, profile_id: profileId });

const MESSAGE_PAGE = 500;
const MESSAGE_PAGES_MAX = 100;

/** Every message of a conversation, oldest first, paged with the worker's `after` cursor. */
export async function listMessages(conversationId: string): Promise<ConvMessage[]> {
  const all: ConvMessage[] = [];
  for (let page = 0; page < MESSAGE_PAGES_MAX; page++) {
    const after = all.length ? all[all.length - 1].seq : 0;
    const r = await call<{ messages: ConvMessage[] }>('conversations.messages', {
      conversation_id: conversationId,
      limit: MESSAGE_PAGE,
      ...(after ? { after } : {}),
    });
    all.push(...r.messages);
    if (r.messages.length < MESSAGE_PAGE) break;
  }
  return all;
}

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
/** The newest runs of one conversation (oldest first); the worker is the source of truth for run ids. */
export const listRuns = (conversationId: string, limit = 30) =>
  call<{ runs: RunSnapshot[] }>('runs.list', { conversation_id: conversationId, limit }).then((r) => r.runs);
export const cancelRun = (runId: string) => call<RunSnapshot>('runs.cancel', { run_id: runId });

export const runEvents = (runId: string, afterId = 0) =>
  call<{ events: RunEvent[] }>('runs.events', { run_id: runId, after_id: afterId }).then((r) => r.events);

export const listApprovals = (runId?: string) =>
  call<{ approvals: Approval[] }>('approvals.list', runId ? { run_id: runId } : {}).then((r) => r.approvals);

export const decideApproval = (runId: string, approvalId: string, decision: 'approve' | 'deny') =>
  call<Approval>('approvals.decide', { run_id: runId, approval_id: approvalId, decision });

export const listDrafts = (runId: string) =>
  call<{ drafts: Draft[] }>('deliverables.list', { run_id: runId }).then((r) => r.drafts);

const DRAFT_PAGE = 200;
const DRAFT_PAGES_MAX = 25;

/** Every deliverable draft across runs, newest first, read page by page with the worker's cursor (bounded). */
export async function listAllDrafts(): Promise<Draft[]> {
  const all: Draft[] = [];
  let cursor: string | null = null;
  for (let page = 0; page < DRAFT_PAGES_MAX; page++) {
    const r: { drafts: Draft[]; next_cursor?: string | null } = await call('deliverables.list_all', {
      limit: DRAFT_PAGE,
      ...(cursor ? { cursor } : {}),
    });
    all.push(...r.drafts);
    cursor = r.next_cursor ?? null;
    if (!cursor) break;
  }
  return all;
}

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

export const engineStatuses = (force = false) =>
  call<{ engines: EngineStatus[] }>('engines.status', force ? { force: true } : {}).then((r) => r.engines);
export const engineCapabilities = () => call<Record<string, EngineCapabilities>>('engines.capabilities');
export const engineModels = (engineId: string) =>
  call<{ models: EngineModel[] }>('engines.models', { engine_id: engineId }).then((r) => r.models);
export const engineCheck = (engineId: string) => call<EngineCheckResult>('engines.check', { engine_id: engineId });
export const freeModels = () => call<{ models: EngineModel[] }>('models.free').then((r) => r.models);

/** The worker-side default engine and model (what Loqman, missions and delegation use unless told otherwise). */
export interface DefaultPreference {
  engine: string | null;
  model_id: string | null;
}
export const preferencesGet = () => call<DefaultPreference>('preferences.get');
export const preferencesSetDefault = (engine: string, modelId: string) =>
  call<DefaultPreference>('preferences.set_default', { engine, model_id: modelId });

export const listProfiles = () => call<{ profiles: Profile[] }>('profiles.list').then((r) => r.profiles);
const MISSION_ACTIVE = ['queued', 'planning', 'running'];
/** Missions end as succeeded, partially_succeeded, failed or cancelled; anything else is still moving. */
export const isMissionTerminal = (status: string) => !MISSION_ACTIVE.includes(status);

export const listMissions = () => call<{ missions: Mission[] }>('missions.list').then((r) => r.missions);

/** Cancel a queued, planning or running mission. Returns the mission as the worker now has it. */
export const cancelMission = (missionId: string) => call<Mission>('missions.cancel', { mission_id: missionId });

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
  decision_note?: string | null;
}

export const listCeoActions = () =>
  call<{ actions: CeoAction[] }>('ceo.actions', { status: 'pending' }).then((r) => r.actions);
/** Where a decided manager action ended up, or null if it is not decided (or not found). */
export async function ceoActionOutcome(id: string): Promise<{ status: 'approved' | 'denied' | 'failed'; note: string | null } | null> {
  for (const status of ['approved', 'denied', 'failed'] as const) {
    const { actions } = await call<{ actions: CeoAction[] }>('ceo.actions', { status });
    const hit = actions.find((a) => a.id === id);
    if (hit) return { status, note: hit.decision_note ?? null };
  }
  return null;
}
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

/** Run one assignment under exactly this agent's profile (no CEO planning). The worker resolves engine and model. */
export const delegateToAgent = (profile: Profile, task: string) =>
  call<Mission>('missions.delegate', {
    conversation_id: `team-panel:${profile.id}`,
    employee_role: profile.role_type,
    agent_profile_id: profile.id,
    title: task.slice(0, 80),
    instructions: task,
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

/** `conversationId` makes the worker remember only that chat; without it Loqman uses the company-wide log.
 *  `engine` and `modelId` are the chat's current choice: Loqman runs on exactly that engine. */
export const askCeo = (question: string, engine: string, modelId: string, requestId: string, conversationId?: string) =>
  call<CeoAnswer>('ceo.ask', {
    question,
    engine,
    model_id: modelId,
    request_id: requestId,
    ...(conversationId ? { conversation_id: conversationId } : {}),
  });

export interface CeoProgress {
  stage: string;
  detail: string;
  iteration: number;
  done: boolean;
}
/** Where a running ceo.ask has got to; the id is the request_id that ask was sent with. */
export const ceoAskProgress = (requestId: string) => call<CeoProgress>('ceo.ask_progress', { request_id: requestId });

export const addMessage = (conversationId: string, role: 'user' | 'assistant', content: string) =>
  call<ConvMessage>('conversations.add_message', { conversation_id: conversationId, role, content });

export const engineSetup = (engineId: string) => call<EngineSetupInfo>('engines.setup', { engine_id: engineId });
/** One real generation: spends the user's quota, so call it only from an explicit click. */
export const engineTest = (engineId: string, modelId?: string) =>
  call<EngineTestResult>('engines.test', { engine_id: engineId, ...(modelId ? { model_id: modelId } : {}) });
export const engineOpenSetup = (engineId: string, step: 'install' | 'login') =>
  call<EngineOpenSetupResult>('engines.open_setup', { engine_id: engineId, step });
export const usageSummary = () => call<UsageSummary>('usage.summary');
