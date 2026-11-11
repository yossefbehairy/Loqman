import type {
  ModelCatalogEntry,
  RuntimeHealth,
  AgentProfile,
  ManagerConsultationResponse,
  MissionOut,
  CeoAskResponse,
  CeoAction,
  CeoActionStatus,
} from '../types';
import { call } from './transport';

/**
 * Backend API. There is no HTTP server any more: every call is a method on the
 * Python worker (see docs/PYWORKER.md), reached through ./transport.
 * Function names/signatures are unchanged from the REST client.
 */

const PROFILE_FIELDS = [
  'name', 'role_type', 'description', 'instructions', 'input_schema_json', 'output_schema_json',
  'default_model_id', 'default_variant_id', 'task_depth', 'settings_json', 'engine',
] as const;

/** The worker validates strictly: send only fields a profile actually has. */
function profileFields(payload: Record<string, unknown>): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const key of PROFILE_FIELDS) if (key in payload) out[key] = payload[key];
  return out;
}

// Runtime & Health
export const getRuntimeHealth = () => call<RuntimeHealth>('runtime.health');
export const getFreeModels = () =>
  call<{ models: ModelCatalogEntry[] }>('models.free').then((r) => r.models);
export const refreshModels = () =>
  call<{ models: ModelCatalogEntry[] }>('models.refresh').then((r) => r.models);
export const getAgentProfiles = () =>
  call<{ profiles: AgentProfile[] }>('profiles.list').then((r) => r.profiles);

// Executive Chat & Consultation (R1)
export const consultManager = (
  managerId: string,
  message: string,
  history?: Array<{ role: string; content: string }>,
  agent?: { name: string; role: string; instructions: string; model_id: string; variant: string }
) =>
  call<ManagerConsultationResponse>('chat.consult', {
    manager_id: managerId,
    message,
    history: history || [],
    ...(agent
      ? {
          agent_name: agent.name,
          agent_role: agent.role,
          agent_instructions: agent.instructions,
          model_id: agent.model_id,
          variant: agent.variant,
        }
      : {}),
  });

export const updateAgentProfile = (id: string, payload: Partial<AgentProfile>) =>
  call<AgentProfile>('profiles.update', { profile_id: id, ...profileFields(payload as Record<string, unknown>) });

export const deleteAgentProfile = (id: string) =>
  call<{ deleted: string }>('profiles.delete', { profile_id: id });

// CEO missions — real end-to-end AI execution (backend owns all AI calls)
export const createMission = (payload: {
  objective: string;
  title?: string;
  model_id?: string;
  variant?: string;
}) => call<MissionOut>('missions.create', payload);

export const createAgentProfile = (payload: {
  name: string;
  role_type: string;
  description?: string;
  instructions: string;
  default_model_id?: string;
  default_variant_id?: string;
  task_depth?: string;
  settings_json?: string;
}) => call<AgentProfile>('profiles.create', profileFields(payload));

export const createDelegatedMission = (payload: {
  conversation_id: string;
  employee_role: string;
  agent_profile_id?: string;
  title: string;
  instructions: string;
  input_context?: string;
  expected_output?: string;
  model_id: string;
  variant?: string;
}) => call<MissionOut>('missions.delegate', payload);

export const listMissions = () =>
  call<{ missions: MissionOut[] }>('missions.list').then((r) => r.missions);

export const getMission = (id: string) => call<MissionOut>('missions.get', { mission_id: id });

export const cancelMission = (id: string) => call<MissionOut>('missions.cancel', { mission_id: id });

// CEO control plane — ask (read-only reasoning loop) + approval-gated actions.
// Decide calls REQUIRE `confirm: true` (replaces the old X-AgentOS-Confirm header);
// the worker refuses with 403 otherwise, so nothing can auto-approve a proposal.
export const askCeo = (question: string, modelId?: string, variant?: string, requestId?: string) =>
  call<CeoAskResponse>('ceo.ask', {
    question,
    ...(modelId ? { model_id: modelId } : {}),
    ...(variant ? { variant } : {}),
    ...(requestId ? { request_id: requestId } : {}),
  });

/** Live stage of an in-flight ask (polled while `ceo.ask` is running). */
export const getCeoAskProgress = (requestId: string) =>
  call<import('../types').CeoAskProgress>('ceo.ask_progress', { request_id: requestId });

export const listCeoActions = (status: CeoActionStatus = 'pending') =>
  call<{ actions: CeoAction[] }>('ceo.actions', { status }).then((r) => r.actions);

export const approveCeoAction = (id: string) => call<CeoAction>('ceo.approve', { action_id: id, confirm: true });

export const denyCeoAction = (id: string) => call<CeoAction>('ceo.deny', { action_id: id, confirm: true });
