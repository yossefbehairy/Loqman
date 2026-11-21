import { useCallback, useRef, useState } from 'react';
import { CEO_PROFILE_ID, listProfiles, type Profile } from '../data/worker';
import type { Agent, ModelReasoning, Team } from '../types';

export interface Company {
  profiles: Profile[];
  agents: Agent[];
  teams: Team[];
  ceo: Agent | null;
  /** True once a current profiles request has settled. */
  loaded: boolean;
  /** Why the current profiles request failed; the last good snapshot remains visible. */
  error: string | null;
  reload: () => Promise<void>;
}

function settingsOf(profile: Profile): Record<string, unknown> {
  try {
    const raw = (profile as unknown as { settings_json?: string | null }).settings_json;
    const parsed = raw ? (JSON.parse(raw) as unknown) : {};
    return parsed && typeof parsed === 'object' ? (parsed as Record<string, unknown>) : {};
  } catch {
    return {};
  }
}

export function agentOf(profile: Profile): Agent {
  const isCeo = profile.id === CEO_PROFILE_ID;
  const cfg = settingsOf(profile);
  const teamName = typeof cfg.team_name === 'string' ? cfg.team_name : null;
  return {
    id: profile.id,
    name: profile.name,
    role: profile.description?.trim() || profile.role_type,
    team: isCeo ? null : teamName ?? 'agents',
    mgr: isCeo,
    model: profile.default_model_id ?? '',
    reason: (profile.task_depth ?? 'Standard') as ModelReasoning,
    timeout: typeof cfg.timeout === 'number' ? cfg.timeout : 180,
    json: false,
    prompt: profile.instructions,
    status: 'Active',
    profileId: profile.id,
  };
}

/** Agents are the worker's profiles: the CEO plus whatever the user or the CEO has created. */
export function useCompany(): Company {
  const [profiles, setProfiles] = useState<Profile[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const profilesRequest = useRef(0);
  const reload = useCallback(async () => {
    const ticket = ++profilesRequest.current;
    try {
      const next = await listProfiles();
      if (ticket !== profilesRequest.current) return;
      setProfiles(next);
      setError(null);
    } catch (e) {
      // A failed read never empties the team: the last agents that loaded stay on screen.
      if (ticket === profilesRequest.current) setError(e instanceof Error ? e.message : String(e));
    } finally {
      if (ticket === profilesRequest.current) setLoaded(true);
    }
  }, []);

  const agents = profiles.map(agentOf);
  const names = new Map<string, string>();
  for (const a of agents) if (a.team && !names.has(a.team)) names.set(a.team, a.team === 'agents' ? 'Agents' : a.team);
  const teams: Team[] = [...names].map(([id, name]) => ({ id, name, desc: '', mgr: null, color: '' }));
  return { profiles, agents, teams, ceo: agents.find((a) => a.mgr) ?? null, loaded, error, reload };
}
