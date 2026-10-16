import type { Agent } from '../types/agentos';
import type { AgentProfile } from '../types';

export interface CompanyEmployee {
  agent: Agent;
  profile: AgentProfile;
}

function norm(value: string): string {
  return (value || '')
    .toLowerCase()
    .replace(/[^a-z\u0600-\u06ff0-9]+/g, ' ')
    .trim();
}

function findProfile(profiles: AgentProfile[], key: string): AgentProfile | undefined {
  const k = (key || '').toLowerCase().trim();
  if (!k) return undefined;
  return (
    profiles.find((p) => p.id.toLowerCase() === k) ||
    profiles.find((p) => p.role_type.toLowerCase() === k)
  );
}

/**
 * Suggest a backend role_type for a company agent from its free-text role/name.
 * Falls back to 'custom' (explicit delegation resolves by profile id anyway).
 */
export function suggestRoleType(agent: Agent): string {
  if (agent.mgr && !agent.team) return 'custom';
  const hay = `${agent.role} ${agent.name}`.toLowerCase();
  const table: Array<[string, RegExp]> = [
    ['planner', /تخطيط|استراتيجي|plann|strategy/],
    ['analyst', /تحليل|مخاطر|analy|risk/],
    ['reviewer', /جودة|اعتماد|review|quality|qa/],
    ['researcher', /بحث|استكشاف|research|intelligence/],
    ['coder', /كود|برمج|مطور|code|dev|software|engineer(?!.*risk)/],
    ['translator', /ترجم|translat/],
    ['summarizer', /ملخص|تلخيص|summar/],
    ['report_writer', /تقرير|report|writer/],
    ['data_extractor', /استخراج|بيانات|data|extract/],
  ];
  for (const [role, re] of table) {
    if (re.test(hay)) return role;
  }
  return 'custom';
}

/**
 * Company employees = frontend agents linked to a real backend execution profile.
 *
 * Link order per agent:
 * 1. Explicit `agent.profileId` (chosen in the contract modal, or auto-created).
 * 2. Company head (mgr without team) -> the General Manager profile (role `custom`).
 * 3. Name match (either direction contains) against profile names.
 */
export function resolveCompanyEmployees(
  agents: Agent[],
  profiles: AgentProfile[]
): CompanyEmployee[] {
  const used = new Set<string>();
  const out: CompanyEmployee[] = [];
  for (const agent of agents) {
    let profile: AgentProfile | undefined;
    if (agent.profileId) profile = findProfile(profiles, agent.profileId);
    if (!profile && agent.mgr && !agent.team) {
      profile = findProfile(profiles, 'custom');
    }
    if (!profile) {
      const n = norm(agent.name);
      if (n) {
        profile = profiles.find((p) => {
          const pn = norm(p.name);
          return pn.includes(n) || n.includes(pn);
        });
      }
    }
    if (profile && !used.has(profile.id)) {
      used.add(profile.id);
      out.push({ agent, profile });
    }
  }
  return out;
}
