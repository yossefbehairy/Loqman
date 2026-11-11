import React, { useState, useEffect, useCallback, useMemo } from 'react';
import type {
  Page,
  CompanyViewMode,
  Team,
  Agent,
  InboxItem,
  RunReceipt,
  AppSettings,
  GraphOptions,
  CameraState,
  Message,
  TaskDeliverable,
  TeamTurn,
  ModelReasoning,
} from './types/agentos';
import {
  INITIAL_TEAMS,
  INITIAL_AGENTS,
  INITIAL_INBOX,
  INITIAL_RUNS,
  INITIAL_MESSAGES,
  INITIAL_SETTINGS,
  TEAM_COLORS,
} from './data/agentosDefaults';
import { CompanyView } from './components/company/CompanyView';
import { CeoChatView } from './components/ceo/CeoChatView';
import { InboxView } from './components/inbox/InboxView';
import { ConversationsView } from './components/conversations/ConversationsView';
import { TeamStreamView } from './components/stream/TeamStreamView';
import { ResultsView } from './components/results/ResultsView';
import { SettingsView } from './components/settings/SettingsView';
import { ContractModal } from './components/modals/ContractModal';
import { NewTeamModal } from './components/modals/NewTeamModal';
import { HelpModal } from './components/modals/HelpModal';
import { SourceModal } from './components/modals/SourceModal';
import { TaskInspectionModal } from './components/modals/TaskInspectionModal';
import {
  BarChart3,
  Bell,
  Building2,
  ChevronRight,
  CircleHelp,
  Crown,
  Inbox,
  MessageCircle,
  MessageSquare,
  MoreHorizontal,
  PanelLeftClose,
  PanelRight,
  Plus,
  Radio,
  Search,
  Settings,
  Sparkles,
  X,
  Zap,
} from 'lucide-react';
import { createDelegatedMission, createMission, getAgentProfiles, getFreeModels, getMission, getRuntimeHealth, createAgentProfile, deleteAgentProfile, listCeoActions } from './api/client';
import { openMissionEvents } from './api/transport';
import type { AgentProfile, CeoThread, MissionOut, ModelCatalogEntry, RuntimeHealth, SuggestedMission } from './types';
import { sendOpenCodeMessage } from './api/opencode';
import { resolveCompanyEmployees, suggestRoleType } from './utils/employeeLink';

const ORG_SEED_KEY = 'agentos_org_seed_v1';
const ORG_NAMES_KEY = 'agentos_org_names_v2';

// Display names are English; the Arabic names remain in the contracts copy.
const ORG_NAME_MAP: Record<string, string> = {
  'Loqman (لقمان)': 'Loqman',
  'د. سارة': 'Dr. Sara',
  'م. طارق': 'Eng. Tarek',
  'أ. ياسمين': 'Prof. Yasmin',
  'خالد': 'Khaled',
  'ليلى': 'Layla',
  'نور': 'Nour',
  'أحمد': 'Ahmed',
  'عمر': 'Omar',
  'منى': 'Mona',
};

function englishifyOrgText(value: string): string {
  return value
    .replace(/فريق البوصلة\s*·\s*Compass/g, 'Compass')
    .replace(/فريق الإنجاز\s*·\s*Forge/g, 'Forge')
    .replace(/فريق البوصلة\s*\(Compass\)/g, 'Compass')
    .replace(/فريق الإنجاز\s*\(Forge\)/g, 'Forge')
    .replace(/Compass\s*\(البوصلة\)/g, 'Compass')
    .replace(/Forge\s*\(الإنجاز\)/g, 'Forge')
    .replace(/فريق البوصلة/g, 'Compass')
    .replace(/فريق الإنجاز/g, 'Forge')
    .replace(/للبوصلة/g, 'لـ Compass')
    .replace(/بالبوصلة/g, 'بـ Compass')
    .replace(/والبوصلة/g, 'و Compass')
    .replace(/البوصلة/g, 'Compass')
    .replace(/للإنجاز/g, 'لـ Forge')
    .replace(/بالإنجاز/g, 'بـ Forge')
    .replace(/والإنجاز/g, 'و Forge')
    .replace(/الإنجاز/g, 'Forge')
    .replace(/Loqman\s*\(لقمان\)/g, 'Loqman')
    .replace(/Dr\. Sara\s*\(د\. سارة\)/g, 'Dr. Sara')
    .replace(/Eng\. Tarek\s*\(م\. طارق\)/g, 'Eng. Tarek')
    .replace(/Prof\. Yasmin\s*\(أ\. ياسمين\)/g, 'Prof. Yasmin')
    .replace(/د\. سارة/g, 'Dr. Sara')
    .replace(/م\. طارق/g, 'Eng. Tarek')
    .replace(/أ\. ياسمين/g, 'Prof. Yasmin')
    .replace(/ياسمين/g, 'Prof. Yasmin')
    .replace(/لقمان/g, 'Loqman');
}

function safeParse<T>(key: string, fallback: T): T {
  try {
    const saved = localStorage.getItem(key);
    return saved ? (JSON.parse(saved) as T) : fallback;
  } catch {
    try {
      localStorage.removeItem(key);
    } catch {
      /* ignore */
    }
    return fallback;
  }
}

const REASONING_VALUES: ModelReasoning[] = [
  'none',
  'minimal',
  'low',
  'medium',
  'high',
  'xhigh',
  'max',
];

const isReasoning = (value: string | null | undefined): value is ModelReasoning =>
  !!value && (REASONING_VALUES as string[]).includes(value);

/** 'security_auditor' -> 'Security Auditor'; unknown values stay readable. */
function humanizeRoleType(roleType: string): string {
  const cleaned = roleType.replace(/[_-]+/g, ' ').trim();
  if (!cleaned) return 'Specialist';
  return cleaned.replace(/\b\w/g, (char) => char.toUpperCase());
}

/**
 * R5: the manager-side synthesis text of a finished mission, derived only from
 * real mission data. `final_summary` is a plain string for CEO missions and
 * `{ result }` for explicit delegations; anything else is serialized as-is.
 * Returns null when the mission carries no summary (failed / cancelled).
 */
function missionSynthesisText(mission: MissionOut): string | null {
  const summary = mission.final_summary;
  if (summary === null || summary === undefined) return null;
  if (typeof summary === 'string') {
    return summary.trim() ? summary.slice(0, 1200) : null;
  }
  const result = (summary as { result?: unknown }).result;
  const text = typeof result === 'string' ? result : JSON.stringify(summary);
  return text.trim() ? text.slice(0, 1200) : null;
}

/** Sidebar sections for the CEO ask history (newest first, filtered by query). */
function buildThreadGroups(
  threads: CeoThread[],
  query: string
): { label: string; items: CeoThread[] }[] {
  const day = 24 * 60 * 60 * 1000;
  const now = Date.now();
  const q = query.trim().toLowerCase();
  const visible = [...threads]
    .sort((a, b) => b.ts - a.ts)
    .filter((t) => !q || t.question.toLowerCase().includes(q));
  const groups: { label: string; items: CeoThread[] }[] = [
    { label: 'Today', items: [] },
    { label: 'Previous 7 days', items: [] },
    { label: 'Previous 30 days', items: [] },
  ];
  visible.forEach((thread) => {
    const age = now - thread.ts;
    if (age < day) groups[0].items.push(thread);
    else if (age < 7 * day) groups[1].items.push(thread);
    else groups[2].items.push(thread);
  });
  return groups.filter((group) => group.items.length > 0);
}

export function App() {
  // Navigation State
  const [page, setPage] = useState<Page>(() => {
    const hash = window.location.hash.replace('#', '') as Page;
    const valid: Page[] = ['ceo', 'company', 'inbox', 'conversations', 'stream', 'results', 'settings'];
    return valid.includes(hash) ? hash : 'company';
  });

  // Company View State
  const [viewMode, setViewMode] = useState<CompanyViewMode>('graph');
  const [selectedAgentId, setSelectedAgentId] = useState<string | null>(null);
  const [focusTeamId, setFocusTeamId] = useState<string | null>(null);
  const [camera, setCamera] = useState<CameraState>({ x: 0, y: 0, k: 0.95 });
  const [graphOptions, setGraphOptions] = useState<GraphOptions>({
    source: 'live',
    trails: false,
    motion: true,
  });
  const [armedDeleteTeamId, setArmedDeleteTeamId] = useState<string | null>(null);

  // Stream Mode (full canvas/view without sidebar)
  const [isStreamMode, setIsStreamMode] = useState<boolean>(false);

  // Entities State (v2 keys: clean slate — v1 keys held the old sample data
  // and are removed once on first launch, see the cleanup effect below).
  const [teams, setTeams] = useState<Team[]>(() => safeParse('agentos_teams_v2', INITIAL_TEAMS));

  const [agents, setAgents] = useState<Agent[]>(() => safeParse('agentos_agents_v2', INITIAL_AGENTS));

  const [inbox, setInbox] = useState<InboxItem[]>(() => safeParse('agentos_inbox_v2', INITIAL_INBOX));

  const [runs, setRuns] = useState<RunReceipt[]>(() => safeParse('agentos_runs_v2', INITIAL_RUNS));

  const [messages, setMessages] = useState<Record<string, Message[]>>(() =>
    safeParse('agentos_msgs_v2', INITIAL_MESSAGES)
  );

  const [settings, setSettings] = useState<AppSettings>(() =>
    safeParse('agentos_settings', INITIAL_SETTINGS)
  );
  const [freeModels, setFreeModels] = useState<ModelCatalogEntry[]>([]);
  const [employeeProfiles, setEmployeeProfiles] = useState<AgentProfile[]>([]);
  const [busyConversations, setBusyConversations] = useState<Record<string, boolean>>({});

  // Shell & CEO console UI state (local-only)
  const [sidebarCollapsed, setSidebarCollapsed] = useState<boolean>(() =>
    safeParse('agentos_sidebar_collapsed', false)
  );
  const [ceoThreads, setCeoThreads] = useState<CeoThread[]>(() =>
    safeParse('agentos_ceo_threads_v1', [] as CeoThread[]).map((t: CeoThread) => ({
      // v1 threads stored a single Q&A; normalize into turns so the same
      // thread can collect follow-up questions from here on.
      ...t,
      turns:
        t.turns && t.turns.length > 0
          ? t.turns
          : [{ question: t.question, reply: t.reply, error: t.error, proposed: t.proposed ?? [], ts: t.ts }],
    }))
  );
  const [activeThreadId, setActiveThreadId] = useState<string | null>(null);
  const [threadSearch, setThreadSearch] = useState('');
  const [runtimeInfo, setRuntimeInfo] = useState<RuntimeHealth | null>(null);
  const [pendingApprovals, setPendingApprovals] = useState(0);
  // The CEO approvals inbox dialog — opened by the topbar bell.
  const [approvalsOpen, setApprovalsOpen] = useState(false);

  const refreshRuntime = useCallback(() => {
    getRuntimeHealth()
      .then(setRuntimeInfo)
      .catch(() => setRuntimeInfo(null));
  }, []);

  useEffect(() => {
    refreshRuntime();
  }, [refreshRuntime]);

  // Topbar bell: best-effort count of the CEO's pending proposals.
  useEffect(() => {
    let ignore = false;
    const pull = () => {
      listCeoActions('pending')
        .then((actions) => {
          if (!ignore) setPendingApprovals(actions.length);
        })
        .catch(() => undefined);
    };
    pull();
    const id = window.setInterval(pull, 20000);
    return () => {
      ignore = true;
      window.clearInterval(id);
    };
  }, []);

  useEffect(() => {
    let ignore = false;
    getFreeModels().then((models) => {
      if (ignore || models.length === 0) return;
      setFreeModels(models);
      setSettings((current) => {
        const selected = models.find((model) => model.id === current.defModel);
        const defaultModel = selected || models[0];
        const reason = defaultModel.variants.includes(current.reason)
          ? current.reason
          : (defaultModel.variants[0] || 'none');
        if (selected && reason === current.reason) return current;
        return { ...current, defModel: defaultModel.id, reason: reason as AppSettings['reason'] };
      });
    }).catch(() => undefined);
    return () => { ignore = true; };
  }, []);

  useEffect(() => {
    getAgentProfiles().then(setEmployeeProfiles).catch(() => setEmployeeProfiles([]));
  }, []);

  // Backfill: every company agent owns exactly one backend execution profile.
  // Agents without an explicit link get one auto-created (once per agent id),
  // so adding a member surfaces them in delegation directly, and deleting the
  // agent drops them (the dropdown is company-driven; orphan profiles stay invisible).
  const backfilledRef = React.useRef<Set<string>>(new Set());
  useEffect(() => {
    if (employeeProfiles.length === 0) return;
    const missing = agents.filter((a) => !a.profileId && !backfilledRef.current.has(a.id));
    if (missing.length === 0) return;
    missing.forEach((a) => backfilledRef.current.add(a.id));
    (async () => {
      for (const a of missing) {
        try {
          const created = await createAgentProfile({
            name: a.name,
            role_type: suggestRoleType(a),
            description: a.role,
            instructions:
              a.prompt.trim() ||
              `You are ${a.name}, ${a.role}. Complete assigned tasks carefully and return clear deliverables.`,
            default_model_id: freeModels.find((m) => m.id === a.model)?.id || a.model,
            task_depth: 'Standard',
            settings_json: JSON.stringify({ company_agent_id: a.id, auto_created: true }),
          });
          setAgents((prev) => prev.map((x) => (x.id === a.id ? { ...x, profileId: created.id } : x)));
        } catch {
          backfilledRef.current.delete(a.id);
        }
      }
    })();
  }, [agents, employeeProfiles, freeModels]);

  // Conversations active agent
  const [activeConvoId, setActiveConvoId] = useState<string>('a1');

  // Modals state
  const [modalState, setModalState] = useState<
    | { type: 'contract'; agentId: string | null; teamIdPrefill?: string | null }
    | { type: 'team' }
    | { type: 'help' }
    | { type: 'source' }
    | null
  >(null);

  // Task & Deliverable Inspection State
  const [inspectDeliverable, setInspectDeliverable] = useState<{
    deliverable: TaskDeliverable;
    reviewId?: string;
    reviewStatus?: 'pending' | 'approved' | 'changes';
  } | null>(null);

  // Toast notifications
  const [toastMessage, setToastMessage] = useState<string | null>(null);
  const toastTimeoutRef = React.useRef<number | undefined>(undefined);

  const showToast = useCallback((msg: string) => {
    setToastMessage(msg);
    if (toastTimeoutRef.current) window.clearTimeout(toastTimeoutRef.current);
    toastTimeoutRef.current = window.setTimeout(() => setToastMessage(null), 2400);
  }, []);

  // Sync state to localStorage
  // One-time cleanup: drop v1 keys that held the old sample workspace.
  useEffect(() => {
    [
      'agentos_teams',
      'agentos_agents',
      'agentos_inbox',
      'agentos_runs',
      'agentos_pipes',
      'agentos_sched',
      'agentos_msgs',
    ].forEach((k) => {
      try {
        localStorage.removeItem(k);
      } catch {
        /* ignore */
      }
    });
  }, []);

  useEffect(() => {
    localStorage.setItem('agentos_teams_v2', JSON.stringify(teams));
  }, [teams]);

  useEffect(() => {
    localStorage.setItem('agentos_agents_v2', JSON.stringify(agents));
  }, [agents]);

  useEffect(() => {
    localStorage.setItem('agentos_inbox_v2', JSON.stringify(inbox));
  }, [inbox]);

  useEffect(() => {
    localStorage.setItem('agentos_runs_v2', JSON.stringify(runs));
  }, [runs]);

  useEffect(() => {
    localStorage.setItem('agentos_msgs_v2', JSON.stringify(messages));
  }, [messages]);

  useEffect(() => {
    localStorage.setItem('agentos_settings', JSON.stringify(settings));
    document.documentElement.dataset.theme = settings.theme;
  }, [settings]);

  useEffect(() => {
    localStorage.setItem('agentos_sidebar_collapsed', JSON.stringify(sidebarCollapsed));
  }, [sidebarCollapsed]);

  useEffect(() => {
    localStorage.setItem('agentos_ceo_threads_v1', JSON.stringify(ceoThreads));
  }, [ceoThreads]);

  // One-time organization migration: existing workspaces move onto the Loqman
  // company (CEO Loqman + Compass planning + Forge execution). Missing teams
  // and members are added once; user-created work is never overwritten.
  useEffect(() => {
    try {
      if (localStorage.getItem(ORG_SEED_KEY)) return;
      const norm = (value: string) =>
        (value || '').toLowerCase().replace(/[^a-z\u0600-\u06ff0-9]+/g, ' ').trim();
      const seedCeo = INITIAL_AGENTS.find((a) => a.mgr && !a.team) || null;

      setAgents((prev) => {
        const hasCeo = prev.some((a) => a.mgr && !a.team);
        let next = prev.map((agent) => {
          if (!hasCeo || !agent.mgr || agent.team) return agent;
          const legacyName = ['adel mansour', 'general manager', 'المدير العام', 'ceo'].includes(
            norm(agent.name)
          );
          if (!legacyName) return agent;
          return {
            ...agent,
            name: seedCeo?.name || 'Loqman',
            role: seedCeo?.role || 'الرئيس التنفيذي · CEO',
            profileId: agent.profileId || seedCeo?.profileId || 'custom',
            prompt: seedCeo?.prompt || agent.prompt,
          };
        });
        if (!next.some((a) => a.mgr && !a.team) && seedCeo) {
          next = [seedCeo, ...next];
        }
        const ids = new Set(next.map((a) => a.id));
        const profileIds = new Set(next.map((a) => a.profileId).filter(Boolean) as string[]);
        const names = new Set(next.map((a) => norm(a.name)));
        const missing = INITIAL_AGENTS.filter(
          (a) =>
            !ids.has(a.id) &&
            !(a.profileId && profileIds.has(a.profileId)) &&
            !names.has(norm(a.name)) &&
            !(a.mgr && !a.team) // never add a second CEO
        );
        return [...next, ...missing];
      });

      setTeams((prev) => {
        const ids = new Set(prev.map((t) => t.id));
        const names = new Set(prev.map((t) => norm(t.name)));
        const missing = INITIAL_TEAMS.filter(
          (t) => !ids.has(t.id) && !names.has(norm(t.name))
        );
        return [...prev, ...missing];
      });

      localStorage.setItem(ORG_SEED_KEY, '1');
    } catch {
      /* storage unavailable — INITIAL_* defaults still apply */
    }
    // Runs once on mount; the flag keeps it idempotent.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // One-time name pass: earlier Loqman workspaces used Arabic/bilingual display
  // names. Translate only the shipped names; a user's custom name is untouched.
  useEffect(() => {
    try {
      if (localStorage.getItem(ORG_NAMES_KEY)) return;
      setAgents((prev) =>
        prev.map((agent) => ({
          ...agent,
          name: ORG_NAME_MAP[agent.name] ?? englishifyOrgText(agent.name),
          prompt: englishifyOrgText(agent.prompt),
        }))
      );
      setTeams((prev) =>
        prev.map((team) => ({
          ...team,
          name: englishifyOrgText(team.name),
          desc: englishifyOrgText(team.desc),
        }))
      );
      localStorage.setItem(ORG_NAMES_KEY, '1');
    } catch {
      /* storage unavailable — defaults already use English names */
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Sync Page to Hash
  const navigateTo = useCallback((newPage: Page) => {
    setPage(newPage);
    window.location.hash = newPage;
  }, []);

  useEffect(() => {
    const onHashChange = () => {
      const hash = window.location.hash.replace('#', '') as Page;
      const valid: Page[] = ['ceo', 'company', 'inbox', 'conversations', 'stream', 'results', 'settings'];
      if (valid.includes(hash)) {
        setPage(hash);
      }
    };
    window.addEventListener('hashchange', onHashChange);
    return () => window.removeEventListener('hashchange', onHashChange);
  }, []);

  // Global Keybindings
  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement)?.tagName || '';
      const isInput = /INPUT|TEXTAREA|SELECT/.test(tag) || (e.target as HTMLElement)?.isContentEditable;

      if (e.key === 'Escape') {
        if (inspectDeliverable) {
          setInspectDeliverable(null);
        } else if (modalState) {
          setModalState(null);
        } else if (isStreamMode) {
          setIsStreamMode(false);
        }
        return;
      }

      // Ignore shortcuts while typing or when focus is on a button (avoid Space/Enter conflicts,
      // but allow F/H since buttons don't consume them — guard only for editable + modal open)
      if (!isInput && page === 'company' && !modalState && !inspectDeliverable) {
        if (tag === 'BUTTON' && (e.key === ' ' || e.key === 'Enter')) return;
        if (e.key === 'f' || e.key === 'F') {
          setIsStreamMode((prev) => !prev);
        }
        if (e.key === 'h' || e.key === 'H') {
          setModalState({ type: 'help' });
        }
      }
    };

    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [page, modalState, inspectDeliverable, isStreamMode]);

  // Dynamic Inbox Badge
  const pendingInboxCount = useMemo(() => {
    return inbox.filter(
      (item) => (item.kind === 'review' && item.st === 'pending') || (item.kind === 'notice' && !item.read)
    ).length;
  }, [inbox]);

  // Focus team handler
  const handleFocusTeam = useCallback(
    (teamId: string) => {
      const targetTeam = teams.find((t) => t.id === teamId);
      const mgrAgent = agents.find((a) => a.id === targetTeam?.mgr);
      setFocusTeamId((prev) => (prev === teamId ? null : teamId));
      if (mgrAgent && focusTeamId !== teamId) {
        setCamera((prevCam) => ({ ...prevCam, k: 1.15 }));
      }
    },
    [teams, agents, focusTeamId]
  );

  // The main conversation uses a persistent OpenCode SDK session. Mission creation is explicit.
  // PR4: missions stream over SSE (replay + live) instead of 2.5s polling.
  const missionWatchersRef = React.useRef<Record<string, ReturnType<typeof openMissionEvents>>>({});
  useEffect(() => {
    const watchers = missionWatchersRef.current;
    return () => {
      Object.values(watchers).forEach((es) => es.close());
    };
  }, []);

  const nowTime = () =>
    new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });

  const appendMessage = useCallback((agentId: string, text: string) => {
    setMessages((prev) => ({
      ...prev,
      [agentId]: [...(prev[agentId] || []), { me: false, t: text, time: nowTime() }],
    }));
  }, []);

  const appendAgentReply = useCallback((agentId: string, text: string, suggestedMission?: SuggestedMission) => {
    setMessages((prev) => ({
      ...prev,
      [agentId]: [...(prev[agentId] || []), { me: false, t: text, time: nowTime(), suggestedMission }],
    }));
  }, []);

  const deliverMissionToInbox = useCallback(
    (mission: MissionOut) => {
      const time = nowTime();
      const items: InboxItem[] = mission.tasks.map((t) => {
        const result = (t.output as any)?.result ?? t.output;
        const summary =
          typeof result === 'object'
            ? JSON.stringify(result).slice(0, 220)
            : String(result ?? t.error_message ?? 'No output').slice(0, 220);
        const deliverable: TaskDeliverable = {
          id: `deliv_${mission.id.slice(0, 8)}_${t.task_key}`,
          title: t.title,
          agentId: t.id,
          agentName: t.employee_role,
          department: t.employee_role,
          status: t.status,
          summary,
          fullJson: (result && typeof result === 'object' ? result : { output: result }) as Record<string, any>,
          turns: [
            {
              turn: 1,
              from: t.employee_role,
              to: 'CEO',
              role: t.employee_role,
              time,
              text: summary,
              fullOutput: (result && typeof result === 'object' ? result : undefined) as Record<string, any> | undefined,
            },
          ],
        };
        const failed = t.status !== 'succeeded';
        return {
          id: `i_${mission.id.slice(0, 8)}_${t.task_key}`,
          kind: 'review' as const,
          title: `${mission.title} — ${t.title}`,
          sub: `${t.employee_role} · ${t.status} · ${t.model_id || 'unknown model'}${t.duration_ms ? ` · ${t.duration_ms}ms` : ''}${failed && t.error_message ? ` · ${t.error_message.slice(0, 120)}` : ''}`,
          time: `Today ${time}`,
          st: (failed ? 'changes' : 'pending') as 'pending' | 'changes',
          deliverable,
        };
      });
      if (items.length > 0) {
        setInbox((prev) => {
          const known = new Set(prev.map((i) => i.id));
          return [...items.filter((i) => !known.has(i.id)), ...prev];
        });
      }
    },
    []
  );

  // ---- R5: real handover recording on the employee agents' `activeTask` ----
  // Team Stream and the active-task inspector read `agent.activeTask.turns`;
  // every recorded turn traces back to the live mission payload (no seeded data).

  /** Turn 1 — record the delegation the moment it is dispatched to an employee. */
  const recordDelegationStart = useCallback(
    (
      employeeAgentId: string,
      manager: { name: string; role: string },
      mission: { id: string; title: string },
      instructions: string
    ) => {
      const time = nowTime();
      setAgents((prev) =>
        prev.map((agent) => {
          if (agent.id !== employeeAgentId) return agent;
          const team = teams.find((t) => t.id === agent.team);
          const text = (instructions.trim() || mission.title).slice(0, 400);
          const activeTask: TaskDeliverable = {
            id: `deliv_${mission.id.slice(0, 8)}`,
            title: mission.title.slice(0, 160),
            agentId: agent.id,
            agentName: agent.name,
            department: team ? team.name : agent.role,
            status: 'running',
            summary: text,
            fullJson: { mission_id: mission.id, delegated_by: manager.name, title: mission.title },
            turns: [
              {
                turn: 1,
                from: manager.name,
                to: agent.name,
                role: manager.role,
                time,
                text,
                fullOutput: {
                  mission_id: mission.id,
                  delegated_by: manager.name,
                  title: mission.title,
                  instructions,
                },
              },
            ],
          };
          return { ...agent, activeTask };
        })
      );
    },
    [teams]
  );

  /**
   * Turns 1-3 — finalize each employee agent's recorded handover from the real
   * terminal mission payload: the deliverable on success, the error text on
   * failure/cancel, and the manager's synthesis as turn 3. One mission can fill
   * several employees' ledgers (task `employee_role` → company employee agent).
   */
  const recordMissionHandover = useCallback(
    (mission: MissionOut, managerName: string, managerRole: string, employeeAgentId?: string) => {
      const time = nowTime();
      const synthesis = missionSynthesisText(mission);
      setAgents((prevAgents) => {
        const employees = resolveCompanyEmployees(prevAgents, employeeProfiles);
        const byRole = new Map<string, Agent>();
        employees.forEach(({ agent, profile }) => {
          byRole.set(profile.role_type.toLowerCase(), agent);
          byRole.set(profile.id.toLowerCase(), agent);
        });

        const updated = new Map<string, TaskDeliverable>();
        const currentTask = (agentId: string) =>
          updated.get(agentId) ?? prevAgents.find((a) => a.id === agentId)?.activeTask;

        for (const task of mission.tasks) {
          const employee =
            byRole.get((task.employee_role || '').toLowerCase()) ||
            (employeeAgentId && mission.tasks.length === 1
              ? prevAgents.find((a) => a.id === employeeAgentId)
              : undefined);
          if (!employee) continue;

          const result = (task.output as any)?.result ?? task.output;
          const succeeded = task.status === 'succeeded';
          const errorText = String(task.error_message || mission.error_summary || '').trim();
          const deliverableText = succeeded
            ? String(
                typeof result === 'object' && result !== null ? JSON.stringify(result) : result ?? 'No output'
              ).slice(0, 220)
            : String(errorText || `Task ${task.task_key} ended: ${task.status}`).slice(0, 300);

          // Preserve the start-time Turn 1 when this mission already recorded its delegation.
          const previous = currentTask(employee.id);
          const sameMission =
            !!previous &&
            (previous.fullJson?.mission_id === mission.id ||
              previous.id.startsWith(`deliv_${mission.id.slice(0, 8)}`));
          const turns: TeamTurn[] = sameMission && previous ? [...previous.turns] : [];
          const base = turns.length;
          const deliverableId = `deliv_${mission.id.slice(0, 8)}_${task.task_key}`;

          if (base === 0) {
            turns.push({
              turn: 1,
              from: managerName,
              to: employee.name,
              role: managerRole,
              time,
              text: task.title,
              fullOutput: { mission_id: mission.id, delegated_by: managerName, title: task.title },
            });
          }

          const pairStart = Math.max(base, 1) + 1;
          turns.push({
            turn: pairStart,
            from: employee.name,
            to: managerName,
            role: employee.role,
            time,
            text: deliverableText,
            fullOutput:
              succeeded && result && typeof result === 'object'
                ? (result as Record<string, any>)
                : undefined,
          });
          turns.push({
            turn: pairStart + 1,
            from: managerName,
            to: employee.name,
            role: managerRole,
            time,
            text:
              synthesis ??
              `Reviewed deliverable ${deliverableId}: ${task.status}${errorText ? ` — ${errorText}` : ''}`,
            fullOutput:
              mission.final_summary && typeof mission.final_summary === 'object'
                ? (mission.final_summary as Record<string, any>)
                : undefined,
          });

          const team = teams.find((t) => t.id === employee.team);
          updated.set(employee.id, {
            id: deliverableId,
            title: task.title,
            agentId: employee.id,
            agentName: employee.name,
            department: team ? team.name : employee.role,
            status: task.status,
            summary: deliverableText,
            fullJson: succeeded
              ? result && typeof result === 'object'
                ? (result as Record<string, any>)
                : { output: result ?? null }
              : { status: task.status, error: errorText || null },
            turns,
          });
        }

        if (updated.size === 0) return prevAgents;
        return prevAgents.map((agent) =>
          updated.has(agent.id) ? { ...agent, activeTask: updated.get(agent.id)! } : agent
        );
      });
    },
    [employeeProfiles, teams]
  );

  const watchMission = useCallback(
    (
      missionId: string,
      agentId: string,
      handover?: { managerName: string; managerRole: string; employeeAgentId?: string }
    ) => {
      if (missionWatchersRef.current[missionId]) return;
      const managerName = handover?.managerName || 'CEO';
      const managerRole = handover?.managerRole || 'CEO';
      // Live stream: the backend replays the mission state, then pushes every
      // status change. No polling loop; one snapshot fetch per event burst.
      const es = openMissionEvents(missionId);
      missionWatchersRef.current[missionId] = es;

      let lastProgressKey = '';
      let snapTimer: number | null = null;

      const finalize = (m: MissionOut) => {
        es.close();
        delete missionWatchersRef.current[missionId];
        if (snapTimer !== null) {
          window.clearTimeout(snapTimer);
          snapTimer = null;
        }
        // R5: finalize the employees' recorded handover with the real outcome.
        recordMissionHandover(m, managerName, managerRole, handover?.employeeAgentId);
        if (m.status === 'succeeded' || m.status === 'partially_succeeded') {
          const summaryText =
            typeof m.final_summary === 'string'
              ? m.final_summary.slice(0, 1200)
              : JSON.stringify(m.final_summary ?? {}).slice(0, 1200);
          appendMessage(
            agentId,
            `✅ Employee task ${missionId.slice(0, 8)} ${m.status}. Its real output is available in Inbox.\n${summaryText}`
          );
          deliverMissionToInbox(m);
        } else if (m.status === 'cancelled') {
          appendMessage(agentId, `🛑 Employee task ${missionId.slice(0, 8)} was cancelled. Completed output (if any) is in Inbox.`);
          deliverMissionToInbox(m);
        } else {
          appendMessage(
            agentId,
            `❌ Employee task ${missionId.slice(0, 8)} failed: ${(m.error_summary || 'unknown error').slice(0, 500)}`
          );
        }
      };

      const handleSnapshot = async (reason: 'event' | 'catch-up') => {
        let m: MissionOut;
        try {
          m = await getMission(missionId);
        } catch (e: any) {
          if (reason === 'catch-up' && missionWatchersRef.current[missionId] === es) {
            es.close();
            delete missionWatchersRef.current[missionId];
            appendMessage(agentId, `⚠️ Lost contact with mission ${missionId.slice(0, 8)}: ${e?.message || e}. It keeps running on the server; check Inbox later.`);
          }
          return;
        }
        if (!missionWatchersRef.current[missionId]) return; // finalized meanwhile
        if (m.status === 'planning' || m.status === 'running' || m.status === 'queued') {
          const done = m.tasks.filter((t) => t.status === 'succeeded' || t.status === 'failed').length;
          const progressKey = `${m.status}:${done}/${m.tasks.length}`;
          if (done > 0 && m.status === 'running' && progressKey !== lastProgressKey) {
            lastProgressKey = progressKey;
            appendMessage(agentId, `⏳ Mission ${missionId.slice(0, 8)} running: ${done}/${m.tasks.length} employee tasks finished.`);
          }
          return;
        }
        finalize(m);
      };

      const scheduleSnapshot = (reason: 'event' | 'catch-up') => {
        if (!missionWatchersRef.current[missionId]) return;
        if (reason === 'event') {
          if (snapTimer !== null) return; // debounce bursts of task events
          snapTimer = window.setTimeout(() => {
            snapTimer = null;
            void handleSnapshot('event');
          }, 300);
          return;
        }
        void handleSnapshot(reason);
      };

      const onLiveEvent = () => scheduleSnapshot('event');
      ['mission.status.changed', 'task.status.changed'].forEach((name) =>
        es.addEventListener(name, onLiveEvent)
      );
      // EventSource reconnects on its own; catch up on anything missed while
      // the stream was down so no status change is ever dropped silently.
      es.onerror = () => scheduleSnapshot('catch-up');
      es.onopen = () => scheduleSnapshot('catch-up');
    },
    [appendMessage, deliverMissionToInbox, recordMissionHandover]
  );

  const handleSendMessage = useCallback(
    (agentId: string, text: string) => {
      setMessages((prev) => ({
        ...prev,
        [agentId]: [...(prev[agentId] || []), { me: true, t: text, time: nowTime() }],
      }));
      const explicitRole = /(?:delegate|assign|create\s+(?:a\s+)?task|make\s+this\s+a\s+task|خل[ّيى]|حوّل|اعمل\s+task)[^\n]*(planner|researcher|analyst|reviewer|quality|coder|developer)/i.exec(text)?.[1];
      const normalizedRole = ({ quality: 'reviewer', developer: 'coder' } as Record<string, string>)[(explicitRole || '').toLowerCase()] || (explicitRole || '').toLowerCase();
      // Delegation targets company members only — never the raw backend catalog.
      const companyEmployees = resolveCompanyEmployees(agents, employeeProfiles);
      const roleEntry = explicitRole && companyEmployees.find(({ profile }) =>
        profile.role_type.toLowerCase() === normalizedRole || profile.id.toLowerCase() === normalizedRole
      );
      if (explicitRole && !roleEntry) {
        appendMessage(agentId, `⚠️ No company member matches '${explicitRole}'. Add them in Company first, then delegate.`);
        return;
      }
      if (roleEntry) {
        const manager = agents.find((a) => a.id === agentId);
        const managerName = manager?.name || 'CEO';
        const managerRole = manager?.role || 'CEO';
        (async () => {
          try {
            const model = freeModels.find((item) => item.id === settings.defModel);
            if (!model) throw new Error('The selected model is not in the verified free-model list.');
            const variant = model.variants.includes(settings.reason) ? settings.reason : model.variants[0];
            const mission = await createDelegatedMission({
              conversation_id: agentId,
              employee_role: roleEntry.profile.role_type,
              agent_profile_id: roleEntry.profile.id,
              title: text.slice(0, 120),
              instructions: text,
              model_id: model.id,
              variant,
            });
            appendMessage(agentId, `📋 Task ${mission.id.slice(0, 8)} assigned to ${roleEntry.agent.name}. Its result will appear in Inbox.`);
            recordDelegationStart(
              roleEntry.agent.id,
              { name: managerName, role: managerRole },
              mission,
              text
            );
            watchMission(mission.id, agentId, {
              managerName,
              managerRole,
              employeeAgentId: roleEntry.agent.id,
            });
          } catch (e: any) {
            appendMessage(agentId, `❌ Could not delegate task: ${e?.message || e}`);
          }
        })();
        return;
      }
      (async () => {
        setBusyConversations((current) => ({ ...current, [agentId]: true }));
        try {
          const selectedModel = freeModels.find((model) => model.id === settings.defModel);
          if (!selectedModel) throw new Error('No verified free OpenCode model is available.');
          const selectedVariant = selectedModel.variants.includes(settings.reason) ? settings.reason : selectedModel.variants[0];
          const response = await sendOpenCodeMessage({
            conversationId: agentId,
            text,
            modelId: selectedModel.id,
            providerId: selectedModel.provider_id,
            variant: selectedVariant,
          });
          appendAgentReply(agentId, response);
        } catch (e: any) {
          appendMessage(agentId, `❌ OpenCode error: ${e?.message || e}`);
        } finally {
          setBusyConversations((current) => ({ ...current, [agentId]: false }));
        }
      })();
    },
    [appendAgentReply, appendMessage, agents, employeeProfiles, freeModels, watchMission, recordDelegationStart, settings.defModel, settings.reason]
  );

  const handleDelegateTask = useCallback(async (payload: {
    employee_role: string; agent_profile_id?: string; title: string; instructions: string; input_context?: string;
    expected_output?: string; model_id: string; variant?: string;
  }) => {
    const mission = await createDelegatedMission({ ...payload, conversation_id: activeConvoId });
    appendMessage(activeConvoId, `📋 Employee task ${mission.id.slice(0, 8)} assigned to ${payload.employee_role}. The result will be available in Inbox.`);
    const manager = agents.find((a) => a.id === activeConvoId);
    const managerName = manager?.name || 'CEO';
    const managerRole = manager?.role || 'CEO';
    const employee = resolveCompanyEmployees(agents, employeeProfiles).find(
      ({ profile }) =>
        (payload.agent_profile_id && profile.id === payload.agent_profile_id) ||
        (!payload.agent_profile_id && profile.role_type.toLowerCase() === payload.employee_role.toLowerCase())
    )?.agent;
    if (employee) {
      recordDelegationStart(
        employee.id,
        { name: managerName, role: managerRole },
        mission,
        payload.instructions || payload.title
      );
    }
    watchMission(mission.id, activeConvoId, {
      managerName,
      managerRole,
      employeeAgentId: employee?.id,
    });
  }, [activeConvoId, agents, appendMessage, employeeProfiles, watchMission, recordDelegationStart]);

  const handleLaunchMission = useCallback((agentId: string, suggested: SuggestedMission) => {
    const goal = suggested.input_json?.goal;
    const objective = typeof goal === 'string' && goal.trim()
      ? goal
      : JSON.stringify(suggested.input_json || suggested.title);
    (async () => {
      try {
        const mission = await createMission({
          objective,
          title: suggested.title,
          model_id: settings.defModel,
          variant: freeModels.find((model) => model.id === settings.defModel)?.variants.includes(settings.reason)
            ? settings.reason
            : (freeModels.find((model) => model.id === settings.defModel)?.variants[0] || undefined),
        });
        appendMessage(agentId, `📋 Mission ${mission.id.slice(0, 8)} started. The AI team is planning with ${mission.model_id || settings.defModel}.`);
        // Employees are only known per task here — turns are recorded on completion.
        watchMission(mission.id, agentId, { managerName: 'CEO', managerRole: 'CEO' });
      } catch (e: any) {
        appendMessage(agentId, `❌ Could not start mission: ${e?.message || e}`);
      }
    })();
  }, [appendMessage, freeModels, watchMission, settings.defModel, settings.reason]);

  // Contract Saving & Editing
  const handleSaveContract = useCallback(
    (contractData: Partial<Agent> & { name: string; role: string }) => {
      const currentModal = modalState;
      if (currentModal?.type !== 'contract') return;

      const editId = currentModal.agentId;

      if (editId) {
        // Edit existing
        setAgents((prev) =>
          prev.map((a) => (a.id === editId ? ({ ...a, ...contractData } as Agent) : a))
        );
        showToast('Contract updated successfully');
      } else {
        // Create new
        const newId = `a_${Date.now().toString(36)}`;
        const newAgent: Agent = {
          id: newId,
          name: contractData.name,
          role: contractData.role,
          team: contractData.team || null,
          mgr: contractData.mgr || false,
          model: contractData.model || settings.defModel,
          reason: contractData.reason || settings.reason,
          timeout: contractData.timeout || settings.timeout,
          json: contractData.json !== undefined ? contractData.json : settings.json,
          prompt: contractData.prompt || '',
          status: 'Active',
        };

        setAgents((prev) => {
          // If marked manager, demote the previous manager of the same team
          if (newAgent.mgr && newAgent.team) {
            return [
              ...prev.map((a) =>
                a.team === newAgent.team && a.mgr ? { ...a, mgr: false } : a
              ),
              newAgent,
            ];
          }
          return [...prev, newAgent];
        });

        // If marked manager, assign as manager of team
        if (newAgent.mgr && newAgent.team) {
          setTeams((prev) =>
            prev.map((t) => (t.id === newAgent.team ? { ...t, mgr: newAgent.id } : t))
          );
        }

        showToast('Employee added to organization');
      }

      setModalState(null);
    },
    [modalState, settings, showToast]
  );

  // Delete Agent (also removes its backend execution profile, if linked —
  // the seeder never resurrects deleted profiles, so removal is permanent).
  const handleDeleteAgent = useCallback(
    (agentId: string) => {
      if (agentId === 'a1') {
        showToast('CEO cannot be removed');
        return;
      }
      const doomed = agents.find((a) => a.id === agentId);
      if (doomed?.profileId) {
        deleteAgentProfile(doomed.profileId).catch(() => undefined);
      }
      setAgents((prev) => prev.filter((a) => a.id !== agentId));
      setTeams((prev) =>
        prev.map((t) => (t.mgr === agentId ? { ...t, mgr: null } : t))
      );
      setMessages((prev) => {
        if (!(agentId in prev)) return prev;
        const next = { ...prev };
        delete next[agentId];
        return next;
      });
      if (selectedAgentId === agentId) setSelectedAgentId(null);
      if (activeConvoId === agentId) setActiveConvoId('a1');
      setModalState(null);
      showToast('Employee removed');
    },
    [agents, selectedAgentId, activeConvoId, showToast]
  );

  // New Team Creation
  const handleSaveTeam = useCallback(
    (teamData: { name: string; desc: string; managerName: string; managerRole: string }) => {
      const newTeamId = `t_${Date.now().toString(36)}`;
      const newMgrId = `a_${Date.now().toString(36)}`;

      const newTeam: Team = {
        id: newTeamId,
        name: teamData.name,
        desc: teamData.desc,
        mgr: newMgrId,
        color: TEAM_COLORS[teams.length % TEAM_COLORS.length],
      };

      const newManager: Agent = {
        id: newMgrId,
        name: teamData.managerName,
        role: teamData.managerRole,
        team: newTeamId,
        mgr: true,
        model: settings.defModel,
        reason: settings.reason,
        timeout: settings.timeout,
        json: settings.json,
        prompt: `You are ${teamData.managerName}, ${teamData.managerRole} of ${teamData.name}. ${teamData.desc}`.trim(),
        status: 'Active',
      };

      setTeams((prev) => [...prev, newTeam]);
      setAgents((prev) => [...prev, newManager]);
      setFocusTeamId(newTeamId);
      setSelectedAgentId(newMgrId);
      setModalState(null);
      showToast('Team created · Add a specialist to activate desk');
    },
    [teams.length, settings, showToast]
  );

  // P3: an approved CEO team/hire is materialized in the company (agentos teams
  // + agents state) using the profiles the approve response reports as created.
  // Full profiles are re-fetched for instructions/model/variant; if that fetch
  // fails the shallow approve payload is enough to still show the team.
  const handleCeoProfilesCreated = useCallback(
    (
      profiles: { id: string; name: string; role_type: string }[],
      teamName: string | null
    ) => {
      if (profiles.length === 0) return;
      void (async () => {
        let fullProfiles = employeeProfiles;
        try {
          const fetched = await getAgentProfiles();
          fullProfiles = fetched;
          setEmployeeProfiles(fetched);
        } catch {
          // Keep the shallow data from the approve response.
        }
        const byId = new Map(fullProfiles.map((profile) => [profile.id, profile]));
        const teamId = `t_ceo_${Date.now().toString(36)}`;
        const newTeam: Team = {
          id: teamId,
          name: teamName?.trim() || 'فريق من CEO',
          desc: 'Created by CEO approval',
          mgr: null,
          color: TEAM_COLORS[teams.length % TEAM_COLORS.length],
        };
        const newAgents: Agent[] = profiles.map((profile) => {
          const full = byId.get(profile.id);
          const role = humanizeRoleType(profile.role_type);
          const model = full?.default_model_id || settings.defModel;
          const candidate = full?.default_variant_id;
          const supported = freeModels.find((entry) => entry.id === model);
          const reason: ModelReasoning =
            isReasoning(candidate) && (!supported || supported.variants.includes(candidate))
              ? candidate
              : settings.reason;
          return {
            id: `a_ceo_${profile.id}`,
            name: profile.name,
            role,
            team: teamId,
            mgr: false,
            model,
            reason,
            timeout: settings.timeout,
            json: settings.json,
            prompt: full?.instructions || `You are ${profile.name}, ${role}.`,
            status: 'Active',
            profileId: profile.id,
          };
        });
        setTeams((prev) => [...prev, newTeam]);
        setAgents((prev) => {
          const existing = new Set(prev.map((agent) => agent.id));
          return [...prev, ...newAgents.filter((agent) => !existing.has(agent.id))];
        });
      })();
    },
    [employeeProfiles, freeModels, settings, teams.length]
  );

  // Delete Team
  const handleDeleteTeam = useCallback(
    (teamId: string) => {
      if (armedDeleteTeamId !== teamId) {
        setArmedDeleteTeamId(teamId);
        showToast('Click delete again to confirm team removal');
        return;
      }

      setArmedDeleteTeamId(null);
      const members = agents.filter((a) => a.team === teamId).map((a) => a.id);
      setAgents((prev) => prev.filter((a) => a.team !== teamId));
      setTeams((prev) => prev.filter((t) => t.id !== teamId));
      setMessages((prev) => {
        const next = { ...prev };
        let changed = false;
        for (const mId of members) {
          if (mId in next) {
            delete next[mId];
            changed = true;
          }
        }
        return changed ? next : prev;
      });

      if (members.includes(selectedAgentId || '')) setSelectedAgentId(null);
      if (members.includes(activeConvoId)) setActiveConvoId('a1');
      if (focusTeamId === teamId) setFocusTeamId(null);

      showToast('Team and associated desks removed');
    },
    [armedDeleteTeamId, agents, selectedAgentId, activeConvoId, focusTeamId, showToast]
  );

  // Inbox Review Actions
  const handleApproveReview = useCallback(
    (id: string) => {
      setInbox((prev) =>
        prev.map((item) => (item.id === id ? { ...item, st: 'approved' } : item))
      );
      showToast('Review approved');
    },
    [showToast]
  );

  const handleRequestChanges = useCallback(
    (id: string) => {
      setInbox((prev) =>
        prev.map((item) => (item.id === id ? { ...item, st: 'changes' } : item))
      );
      showToast('Changes requested from department');
    },
    [showToast]
  );

  const handleMarkNoticeRead = useCallback((id: string) => {
    setInbox((prev) =>
      prev.map((item) => (item.id === id ? { ...item, read: true } : item))
    );
  }, []);

  const handleApproveAllReviews = useCallback(() => {
    setInbox((prev) =>
      prev.map((item) =>
        item.kind === 'review' && item.st === 'pending' ? { ...item, st: 'approved' } : item
      )
    );
    showToast('All pending reviews approved');
  }, [showToast]);

  const handleMarkAllAsRead = useCallback(() => {
    setInbox((prev) => prev.map((item) => ({ ...item, read: true })));
    showToast('All notices marked as read');
  }, [showToast]);

  const handleOpenNoticeTarget = useCallback(
    (itemId: string, targetAgentId?: string) => {
      handleMarkNoticeRead(itemId);
      if (!targetAgentId) return;

      const target = agents.find((a) => a.id === targetAgentId);
      if (target?.mgr) {
        setActiveConvoId(target.id);
        navigateTo('conversations');
      } else {
        setSelectedAgentId(targetAgentId);
        navigateTo('company');
      }
    },
    [agents, handleMarkNoticeRead, navigateTo]
  );

  // Inspect Review Item / Deliverable
  const handleInspectInboxItem = useCallback(
    (item: InboxItem) => {
      const deliv = item.deliverable;

      if (deliv) {
        setInspectDeliverable({
          deliverable: deliv,
          reviewId: item.id,
          reviewStatus: item.st,
        });
      } else {
        const targetAgent = agents.find((a) => a.id === item.target);
        setInspectDeliverable({
          deliverable: {
            id: `task_${item.id}`,
            title: item.title,
            agentId: item.target || 'a1',
            agentName: targetAgent ? targetAgent.name : 'Loqman Employee',
            department: 'Operations',
            status: item.st || 'Informational',
            summary: item.sub,
            turns: [
              {
                turn: 1,
                from: targetAgent ? targetAgent.name : 'System',
                to: 'Supervisor',
                role: targetAgent?.role || 'Notice',
                time: item.time,
                text: `${item.title}: ${item.sub}`,
              },
            ],
          },
          reviewId: item.id,
          reviewStatus: item.st,
        });
      }
    },
    [agents]
  );

  // Inspect Agent Active Task & Dialogue from Graph or Org Chart
  const handleInspectAgentTask = useCallback(
    (agentId: string) => {
      const agent = agents.find((a) => a.id === agentId);
      if (!agent) return;

      const task = agent.activeTask;

      if (task) {
        setInspectDeliverable({
          deliverable: task,
        });
      } else {
        const team = teams.find((t) => t.id === agent.team);
        setInspectDeliverable({
          deliverable: {
            id: `task_${agent.id}`,
            title: `${agent.name} · Active Work & Coordination`,
            agentId: agent.id,
            agentName: agent.name,
            department: team ? team.name : 'Executive',
            status: agent.status,
            summary: `Operating under contract: ${agent.model} (${agent.reason} reasoning). ${agent.prompt.slice(0, 120)}...`,
            turns: [
              {
                turn: 1,
                from: agent.name,
                to: agent.mgr ? 'Department Specialists' : 'Department Manager',
                role: agent.role,
                time: 'Active now',
                text: `أعمل حالياً على تنفيذ بنود العقد الموكلة لي وفق متطلبات الـ System Prompt: "${agent.prompt}"`,
              },
            ],
          },
        });
      }
    },
    [agents, teams]
  );

  // Export CSV
  const handleExportCSV = useCallback(
    (periodVal: string) => {
      const dayLimit = periodVal === '7' ? 6 : periodVal === '30' ? 29 : 1e9;
      const filtered = runs.filter((r) => r.d <= dayLimit);
      const csvHeader = 'days_ago,pipeline,status,duration_s,tokens\n';
      const rows = filtered
        .map((r) => `${r.d},"${r.pipe.replace(/"/g, '""')}",${r.st},${r.dur},${r.tok}`)
        .join('\n');
      const blob = new Blob([csvHeader + rows], { type: 'text/csv' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `agentos-results-${periodVal}d.csv`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
      showToast('Results CSV exported');
    },
    [runs, showToast]
  );

  // Export JSON
  const handleExportJSON = useCallback(() => {
    const payload = { teams, agents, runs, inbox, settings };
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `agentos-workspace-backup.json`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
    showToast('Full workspace exported as JSON');
  }, [teams, agents, runs, inbox, settings, showToast]);

  // Reset Workspace (back to the clean initial state)
  const handleResetDemo = useCallback(() => {
    const keys = [
      'agentos_teams_v2',
      'agentos_agents_v2',
      'agentos_inbox_v2',
      'agentos_runs_v2',
      'agentos_msgs_v2',
      'agentos_settings',
      'agentos_ceo_threads_v1',
    ];
    keys.forEach((k) => {
      try {
        localStorage.removeItem(k);
      } catch {
        /* ignore */
      }
    });
    setTeams(INITIAL_TEAMS);
    setAgents(INITIAL_AGENTS);
    setInbox(INITIAL_INBOX);
    setRuns(INITIAL_RUNS);
    setMessages(INITIAL_MESSAGES);
    setSettings(INITIAL_SETTINGS);
    setCeoThreads([]);
    setActiveThreadId(null);
    setSelectedAgentId(null);
    setFocusTeamId(null);
    setViewMode('graph');
    setCamera({ x: 0, y: 0, k: 0.95 });
    setGraphOptions({ source: 'live', trails: false, motion: true });
    setModalState(null);
    setInspectDeliverable(null);
    setArmedDeleteTeamId(null);
    showToast('Workspace reset to clean state');
  }, [showToast]);

  // Navigation Items
  const navItems: { id: Page; label: string; icon: React.FC<{ size?: number; className?: string }> }[] = [
    { id: 'ceo', label: 'CEO', icon: Crown },
    { id: 'company', label: 'Company', icon: Building2 },
    { id: 'inbox', label: 'Inbox', icon: Inbox },
    { id: 'conversations', label: 'Conversations', icon: MessageSquare },
    { id: 'stream', label: 'Team Stream', icon: Radio },
    { id: 'results', label: 'Results', icon: BarChart3 },
    { id: 'settings', label: 'Settings', icon: Settings },
  ];

  const pageLabel: Record<Page, string> = {
    ceo: 'CEO',
    company: 'Company',
    inbox: 'Inbox',
    conversations: 'Conversations',
    stream: 'Team Stream',
    results: 'Results',
    settings: 'Settings',
  };

  // CEO ask history for the sidebar (Today / Previous 7 days / Previous 30 days).
  const threadGroups = useMemo(
    () => buildThreadGroups(ceoThreads, threadSearch),
    [ceoThreads, threadSearch]
  );

  const handleNewThread = useCallback(() => {
    setActiveThreadId(null);
    navigateTo('ceo');
  }, [navigateTo]);

  const handleSelectThread = useCallback(
    (id: string) => {
      setActiveThreadId(id);
      navigateTo('ceo');
    },
    [navigateTo]
  );

  return (
    <div className={`app-shell ${sidebarCollapsed ? 'sidebar-collapsed' : ''}`}>
      {/* Sidebar Navigation */}
      {!isStreamMode && (
        <aside className={`sidebar ${sidebarCollapsed ? 'collapsed' : ''}`}>
          <div className="sidebar-topline">
            <button
              className="brand-button"
              onClick={() => navigateTo('ceo')}
              title="Loqman"
              aria-label="Loqman"
            >
              <span className="brand-mark">
                <span />
                <span />
                <span />
              </span>
              {!sidebarCollapsed && (
                <span className="brand-name">
                  Loq<span>man</span>
                </span>
              )}
            </button>
            <button
              className="icon-button sidebar-toggle"
              title={sidebarCollapsed ? 'Expand sidebar' : 'Collapse sidebar'}
              aria-label={sidebarCollapsed ? 'Expand sidebar' : 'Collapse sidebar'}
              onClick={() => setSidebarCollapsed((v) => !v)}
            >
              {sidebarCollapsed ? <PanelRight size={15} /> : <PanelLeftClose size={15} />}
            </button>
          </div>

          <nav className="primary-nav" aria-label="Main navigation">
            {navItems.map((item) => {
              const Icon = item.icon;
              const isActive = page === item.id;
              const showBadge = item.id === 'inbox' && pendingInboxCount > 0;

              return (
                <button
                  key={item.id}
                  onClick={() => navigateTo(item.id)}
                  className={`nav-item ${isActive ? 'active' : ''}`}
                  title={sidebarCollapsed ? item.label : undefined}
                >
                  <Icon size={16} />
                  {!sidebarCollapsed && <span className="nav-item-label">{item.label}</span>}
                  {!sidebarCollapsed && showBadge && (
                    <span className="nav-count" aria-hidden="true">
                      {pendingInboxCount}
                    </span>
                  )}
                  {!sidebarCollapsed && isActive && <span className="active-dot" aria-hidden="true" />}
                </button>
              );
            })}
          </nav>

          {!sidebarCollapsed && (
            <>
              <div className="sidebar-divider" />
              <div className="conversation-toolbar">
                <div className="section-label">RECENT QUESTIONS</div>
                <button
                  className="mini-icon-button"
                  title="New question"
                  aria-label="New question"
                  onClick={handleNewThread}
                >
                  <Plus size={15} />
                </button>
              </div>
              <label className="conversation-search">
                <Search size={13} />
                <input
                  value={threadSearch}
                  onChange={(e) => setThreadSearch(e.target.value)}
                  placeholder="Search questions"
                  aria-label="Search questions"
                />
              </label>
              <div className="conversation-list">
                {threadGroups.map((group) => (
                  <div className="conversation-group" key={group.label}>
                    <div className="group-label">{group.label}</div>
                    {group.items.map((thread) => (
                      <button
                        key={thread.id}
                        className={`conversation-item ${
                          activeThreadId === thread.id && page === 'ceo' ? 'selected' : ''
                        }`}
                        onClick={() => handleSelectThread(thread.id)}
                        title={thread.question}
                      >
                        <MessageCircle size={14} />
                        <span className="conversation-item-title" dir="auto">
                          {thread.question.length > 40
                            ? `${thread.question.slice(0, 40).trim()}…`
                            : thread.question}
                        </span>
                      </button>
                    ))}
                  </div>
                ))}
                {threadGroups.length === 0 && (
                  <div className="no-search-results">
                    {ceoThreads.length === 0 ? 'اسأل لقمان عشان تبدأ' : 'مفيش نتائج'}
                  </div>
                )}
              </div>
              <div className="sidebar-bottom">
                <div className="workspace-status-card">
                  <span className="workspace-status-icon">
                    <Zap size={15} />
                  </span>
                  <span className="workspace-status-copy">
                    <strong>Local workspace</strong>
                    <small className="workspace-status-meta">
                      <span>OpenCode</span>
                      <span>·</span>
                      <span className="ok">
                        <i className="status-inline-dot" />
                        Online
                      </span>
                    </small>
                  </span>
                </div>
                <button className="profile-row" onClick={() => navigateTo('settings')}>
                  <div className="profile-avatar">YB</div>
                  <span className="profile-copy">
                    <strong>Youssef Behairy</strong>
                    <small>Loqman workspace</small>
                  </span>
                  <MoreHorizontal size={16} className="muted" />
                </button>
              </div>
            </>
          )}

          {sidebarCollapsed && (
            <div className="collapsed-actions">
              <button
                className="icon-button"
                title="New question"
                aria-label="New question"
                onClick={handleNewThread}
              >
                <Plus size={17} />
              </button>
              <button
                className="icon-button"
                title="Expand sidebar"
                aria-label="Expand sidebar"
                onClick={() => setSidebarCollapsed(false)}
              >
                <ChevronRight size={17} />
              </button>
            </div>
          )}
        </aside>
      )}

      {/* Main Container */}
      <div className="main-area">
        {/* Topbar */}
        {!isStreamMode && (
          <header className="topbar">
            <div className="topbar-left">
              <div className="breadcrumb">
                <span>Workspace</span>
                <ChevronRight size={13} />
                <strong>{pageLabel[page]}</strong>
              </div>
            </div>
            <div className="topbar-actions">
              <button
                className="status-pill"
                title="Re-check runtime"
                onClick={() => {
                  refreshRuntime();
                  showToast('Runtime health re-checked');
                }}
              >
                <span className="status-dot" />
                <span>
                  {runtimeInfo
                    ? `Runtime ${runtimeInfo.status} · ${runtimeInfo.free_models_count} free models`
                    : 'Checking runtime…'}
                </span>
              </button>
              <button
                className="icon-button"
                title="Help"
                aria-label="Help"
                onClick={() => setModalState({ type: 'help' })}
              >
                <CircleHelp size={17} />
              </button>
              <button
                className="icon-button notification-button"
                title="Pending approvals"
                aria-label="Pending approvals"
                onClick={() => {
                  navigateTo('ceo');
                  setApprovalsOpen(true);
                }}
              >
                <Bell size={17} />
                {pendingApprovals > 0 ? <span aria-hidden="true" /> : null}
              </button>
              <div className="topbar-avatar">YB</div>
            </div>
          </header>
        )}

        {/* Stream mode banner when active */}
        {isStreamMode && (
          <div className="stream-banner">
            <span className="flex items-center gap-2">
              <span className="ping" />
              Stream View Active (Press <kbd>F</kbd> or <kbd>Esc</kbd> to return to standard view)
            </span>
            <button
              onClick={() => setIsStreamMode(false)}
              className="os-btn os-btn-ghost os-btn-sm text-[11px]"
            >
              Exit stream view
            </button>
          </div>
        )}

        <main className="content-host">
          {page === 'ceo' ? (
            <CeoChatView
              models={freeModels}
              selectedModelId={settings.defModel}
              selectedVariant={settings.reason}
              onModelChange={(modelId, variant) => {
                setSettings((prev) => ({
                  ...prev,
                  defModel: modelId,
                  ...(isReasoning(variant) ? { reason: variant } : {}),
                }));
                showToast('CEO model updated');
              }}
              onProfilesCreated={handleCeoProfilesCreated}
              threads={ceoThreads}
              activeThreadId={activeThreadId}
              onSelectThread={handleSelectThread}
              onNewThread={handleNewThread}
              setThreads={setCeoThreads}
              onToast={showToast}
              approvalsOpen={approvalsOpen}
              onApprovalsOpenChange={setApprovalsOpen}
            />
          ) : (
            <div className="view-scroll">

          {page === 'company' && (
            <CompanyView
              agents={agents}
              teams={teams}
              runs={runs}
              viewMode={viewMode}
              onViewModeChange={setViewMode}
              selectedAgentId={selectedAgentId}
              onSelectAgent={setSelectedAgentId}
              focusTeamId={focusTeamId}
              onFocusTeam={handleFocusTeam}
              graphOptions={graphOptions}
              onGraphOptionsChange={setGraphOptions}
              camera={camera}
              onCameraChange={setCamera}
              armedDeleteTeamId={armedDeleteTeamId}
              onOpenConversation={(id) => {
                setActiveConvoId(id);
                navigateTo('conversations');
              }}
              onEditContract={(id) => setModalState({ type: 'contract', agentId: id })}
              onAddSpecialist={(teamId) =>
                setModalState({ type: 'contract', agentId: null, teamIdPrefill: teamId })
              }
              onDeleteTeam={handleDeleteTeam}
              onNewTeam={() => setModalState({ type: 'team' })}
              onOpenHelp={() => setModalState({ type: 'help' })}
              onOpenSourceDetails={() => setModalState({ type: 'source' })}
              onToggleStream={() => setIsStreamMode((prev) => !prev)}
              onInspectAgentTask={handleInspectAgentTask}
            />
          )}

          {page === 'inbox' && (
            <InboxView
              inbox={inbox}
              agents={agents}
              onApproveReview={handleApproveReview}
              onRequestChanges={handleRequestChanges}
              onMarkNoticeRead={handleMarkNoticeRead}
              onApproveAllReviews={handleApproveAllReviews}
              onMarkAllAsRead={handleMarkAllAsRead}
              onOpenTarget={handleOpenNoticeTarget}
              onInspectDeliverable={handleInspectInboxItem}
            />
          )}

          {page === 'conversations' && (
            <ConversationsView
              agents={agents}
              teams={teams}
              activeConvoId={activeConvoId}
              onSelectConvo={setActiveConvoId}
              messages={messages}
              onSendMessage={handleSendMessage}
              onDelegateTask={handleDelegateTask}
              employeeProfiles={employeeProfiles}
              freeModels={freeModels}
              selectedModelId={settings.defModel}
              selectedVariant={settings.reason}
              busy={busyConversations}
              onLaunchMission={handleLaunchMission}
              onOpenContract={(id) => setModalState({ type: 'contract', agentId: id })}
            />
          )}

          {page === 'stream' && (
            <TeamStreamView
              agents={agents}
              teams={teams}
            />
          )}

          {page === 'results' && (
            <ResultsView
              runs={runs}
              inbox={inbox}
              agents={agents}
              onConnectSource={() =>
                showToast('No external data source connected. Metrics stay unavailable until verified.')
              }
              onExportCSV={handleExportCSV}
            />
          )}

          {page === 'settings' && (
            <SettingsView
              settings={settings}
              models={freeModels}
              onUpdateSettings={(newVals) => {
                setSettings((prev) => ({ ...prev, ...newVals }));
                showToast('Settings saved');
              }}
              onCheckRuntime={() => {
                (async () => {
                  try {
                    const health = await getRuntimeHealth();
                    showToast(
                      `Runtime ${health.status} · ${health.free_models_count} free models · ${health.models_count} total`
                    );
                  } catch (e: unknown) {
                    showToast(
                      `Runtime unreachable: ${e instanceof Error ? e.message : 'unknown error'}`
                    );
                  }
                })();
              }}
              onExportJSON={handleExportJSON}
              onResetDemo={handleResetDemo}
            />
          )}
            </div>
          )}
        </main>
      </div>

      {/* Modals Container */}
      {modalState?.type === 'contract' && (
        <ContractModal
          agent={agents.find((a) => a.id === modalState.agentId) || null}
          teamIdPrefill={modalState.teamIdPrefill}
          teams={teams}
          models={freeModels}
          profiles={employeeProfiles}
          onClose={() => setModalState(null)}
          onSave={handleSaveContract}
          onDelete={modalState.agentId ? handleDeleteAgent : undefined}
        />
      )}

      {modalState?.type === 'team' && (
        <NewTeamModal
          onClose={() => setModalState(null)}
          onSave={handleSaveTeam}
        />
      )}

      {modalState?.type === 'help' && (
        <HelpModal onClose={() => setModalState(null)} />
      )}

      {modalState?.type === 'source' && (
        <SourceModal
          mode={graphOptions.source}
          agentsCount={agents.length}
          teamsCount={teams.length}
          runsCount={runs.length}
          onClose={() => setModalState(null)}
        />
      )}

      {/* Task & Dialogue Deliverable Inspector Modal */}
      {inspectDeliverable && (
        <TaskInspectionModal
          deliverable={inspectDeliverable.deliverable}
          reviewId={inspectDeliverable.reviewId}
          reviewStatus={inspectDeliverable.reviewStatus}
          onClose={() => setInspectDeliverable(null)}
          onApprove={handleApproveReview}
          onRequestChanges={handleRequestChanges}
        />
      )}

      {/* Floating Toast Notification */}
      {toastMessage && (
        <div className="toast" role="status">
          <span className="toast-mark">
            <Sparkles size={15} />
          </span>
          <span className="toast-copy">{toastMessage}</span>
          <button
            onClick={() => setToastMessage(null)}
            aria-label="Dismiss notification"
            title="Dismiss"
          >
            <X size={14} />
          </button>
        </div>
      )}
    </div>
  );
}

export default App;
