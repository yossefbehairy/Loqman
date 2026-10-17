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

/**
 * API base URL.
 * - Electron desktop shell: preload.cjs exposes window.__AGENTOS_API__ =
 *   "http://127.0.0.1:<port>/api" (dynamic port owned by the shell).
 * - Plain browser (vite dev / web deployment): falls back to the relative
 *   "/api" path (vite dev proxy or same-origin backend).
 */
export const API_BASE: string =
  (typeof window !== 'undefined' && (window as unknown as { __AGENTOS_API__?: string }).__AGENTOS_API__) ||
  '/api';

const BASE_URL = API_BASE;

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      ...(options?.headers || {}),
    },
  });

  if (!res.ok) {
    let errDetail = res.statusText;
    try {
      const errJson = await res.json();
      errDetail = errJson.detail || JSON.stringify(errJson);
    } catch {
      // ignore JSON parse error on non-json response
    }
    throw new Error(typeof errDetail === 'string' ? errDetail : JSON.stringify(errDetail));
  }

  if (res.status === 204) {
    return {} as T;
  }

  return res.json();
}

// Runtime & Health
export const getRuntimeHealth = () => request<RuntimeHealth>('/runtime/health');
export const getFreeModels = () => request<ModelCatalogEntry[]>('/models/free');
export const refreshModels = () => request<ModelCatalogEntry[]>('/models/refresh', { method: 'POST' });
export const getAgentProfiles = () => request<AgentProfile[]>('/agent-profiles');

// Executive Chat & Consultation (R1)
export const consultManager = (
  managerId: string,
  message: string,
  history?: Array<{ role: string; content: string }>,
  agent?: { name: string; role: string; instructions: string; model_id: string; variant: string }
) =>
  request<ManagerConsultationResponse>('/chat', {
    method: 'POST',
    body: JSON.stringify({
      manager_id: managerId,
      message,
      history: history || [],
      ...(agent ? {
        agent_name: agent.name,
        agent_role: agent.role,
        agent_instructions: agent.instructions,
        model_id: agent.model_id,
        variant: agent.variant,
      } : {}),
    }),
  });

export const updateAgentProfile = (id: string, payload: Partial<AgentProfile>) =>
  request<AgentProfile>(`/agent-profiles/${id}`, {
    method: 'PUT',
    body: JSON.stringify(payload),
  });

export const deleteAgentProfile = (id: string) =>
  request<{ deleted: string }>(`/agent-profiles/${encodeURIComponent(id)}`, {
    method: 'DELETE',
  });

// CEO missions — real end-to-end AI execution (backend owns all AI calls)
export const createMission = (payload: {
  objective: string;
  title?: string;
  model_id?: string;
  variant?: string;
}) => request<MissionOut>('/missions', { method: 'POST', body: JSON.stringify(payload) });

export const createAgentProfile = (payload: {
  name: string;
  role_type: string;
  description?: string;
  instructions: string;
  default_model_id?: string;
  default_variant_id?: string;
  task_depth?: string;
  settings_json?: string;
}) => request<AgentProfile>('/agent-profiles', { method: 'POST', body: JSON.stringify(payload) });

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
}) => request<MissionOut>('/missions/delegate', { method: 'POST', body: JSON.stringify(payload) });

export const listMissions = () => request<MissionOut[]>('/missions');

export const getMission = (id: string) => request<MissionOut>(`/missions/${id}`);

export const cancelMission = (id: string) =>
  request<MissionOut>(`/missions/${id}/cancel`, { method: 'POST' });

export const missionEventsUrl = (id: string) => `${API_BASE}/missions/${id}/events`;

// CEO control plane — ask (read-only reasoning loop) + approval-gated actions.
// Decide endpoints REQUIRE the local confirmation header; without it the
// backend refuses with 403 (external callers cannot auto-approve proposals).
export const askCeo = (question: string, modelId?: string, variant?: string, requestId?: string) =>
  request<CeoAskResponse>('/ceo/ask', {
    method: 'POST',
    body: JSON.stringify({
      question,
      ...(modelId ? { model_id: modelId } : {}),
      ...(variant ? { variant } : {}),
      ...(requestId ? { request_id: requestId } : {}),
    }),
  });

/** Live stage of an in-flight ask (polled while POST /ceo/ask is running). */
export const getCeoAskProgress = (requestId: string) =>
  request<import('../types').CeoAskProgress>(`/ceo/ask-progress/${encodeURIComponent(requestId)}`);

export const listCeoActions = (status: CeoActionStatus = 'pending') =>
  request<CeoAction[]>(`/ceo/actions?status=${status}`);

export const approveCeoAction = (id: string) =>
  request<CeoAction>(`/ceo/actions/${encodeURIComponent(id)}/approve`, {
    method: 'POST',
    headers: { 'X-AgentOS-Confirm': 'true' },
  });

export const denyCeoAction = (id: string) =>
  request<CeoAction>(`/ceo/actions/${encodeURIComponent(id)}/deny`, {
    method: 'POST',
    headers: { 'X-AgentOS-Confirm': 'true' },
  });

