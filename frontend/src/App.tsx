import React, { useState, useEffect, useCallback, useMemo } from 'react';
import type {
  Page,
  CompanyViewMode,
  Team,
  Agent,
  InboxItem,
  RunReceipt,
  Pipeline,
  ScheduleItem,
  AppSettings,
  GraphOptions,
  CameraState,
  Message,
  TaskDeliverable,
} from './types/agentos';
import {
  INITIAL_TEAMS,
  INITIAL_AGENTS,
  INITIAL_INBOX,
  INITIAL_RUNS,
  INITIAL_PIPELINES,
  INITIAL_SCHEDULE,
  INITIAL_MESSAGES,
  INITIAL_SETTINGS,
  TEAM_COLORS,
} from './data/agentosDefaults';
import { CompanyView } from './components/company/CompanyView';
import { InboxView } from './components/inbox/InboxView';
import { ConversationsView } from './components/conversations/ConversationsView';
import { TeamStreamView } from './components/stream/TeamStreamView';
import { ResultsView } from './components/results/ResultsView';
import { CalendarView } from './components/calendar/CalendarView';
import { SettingsView } from './components/settings/SettingsView';
import { ContractModal } from './components/modals/ContractModal';
import { NewTeamModal } from './components/modals/NewTeamModal';
import { HelpModal } from './components/modals/HelpModal';
import { SourceModal } from './components/modals/SourceModal';
import { TaskInspectionModal } from './components/modals/TaskInspectionModal';
import {
  Building2,
  Inbox,
  MessageSquare,
  BarChart3,
  Calendar,
  Settings,
  Radio,
} from 'lucide-react';
import { createDelegatedMission, createMission, getAgentProfiles, getFreeModels, getMission, getRuntimeHealth, createAgentProfile, deleteAgentProfile } from './api/client';
import type { AgentProfile, MissionOut, ModelCatalogEntry, SuggestedMission } from './types';
import { sendOpenCodeMessage } from './api/opencode';
import { resolveCompanyEmployees, suggestRoleType } from './utils/employeeLink';

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

export function App() {
  // Navigation State
  const [page, setPage] = useState<Page>(() => {
    const hash = window.location.hash.replace('#', '') as Page;
    const valid: Page[] = ['company', 'inbox', 'conversations', 'stream', 'results', 'calendar', 'settings'];
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

  const [pipelines, setPipelines] = useState<Pipeline[]>(() => safeParse('agentos_pipes_v2', INITIAL_PIPELINES));

  const [schedules, setSchedules] = useState<ScheduleItem[]>(() =>
    safeParse('agentos_sched_v2', INITIAL_SCHEDULE)
  );

  const [messages, setMessages] = useState<Record<string, Message[]>>(() =>
    safeParse('agentos_msgs_v2', INITIAL_MESSAGES)
  );

  const [settings, setSettings] = useState<AppSettings>(() =>
    safeParse('agentos_settings', INITIAL_SETTINGS)
  );
  const [freeModels, setFreeModels] = useState<ModelCatalogEntry[]>([]);
  const [employeeProfiles, setEmployeeProfiles] = useState<AgentProfile[]>([]);
  const [busyConversations, setBusyConversations] = useState<Record<string, boolean>>({});

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
    localStorage.setItem('agentos_pipes_v2', JSON.stringify(pipelines));
  }, [pipelines]);

  useEffect(() => {
    localStorage.setItem('agentos_sched_v2', JSON.stringify(schedules));
  }, [schedules]);

  useEffect(() => {
    localStorage.setItem('agentos_msgs_v2', JSON.stringify(messages));
  }, [messages]);

  useEffect(() => {
    localStorage.setItem('agentos_settings', JSON.stringify(settings));
    document.documentElement.dataset.theme = settings.theme;
  }, [settings]);

  // Sync Page to Hash
  const navigateTo = useCallback((newPage: Page) => {
    setPage(newPage);
    window.location.hash = newPage;
  }, []);

  useEffect(() => {
    const onHashChange = () => {
      const hash = window.location.hash.replace('#', '') as Page;
      const valid: Page[] = ['company', 'inbox', 'conversations', 'stream', 'results', 'calendar', 'settings'];
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
  const missionPollersRef = React.useRef<Record<string, number>>({});
  useEffect(() => {
    const pollers = missionPollersRef.current;
    return () => {
      Object.values(pollers).forEach((id) => window.clearInterval(id));
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

  const pollMission = useCallback(
    (missionId: string, agentId: string) => {
      if (missionPollersRef.current[missionId]) return;
      const timer = window.setInterval(async () => {
        let m: MissionOut;
        try {
          m = await getMission(missionId);
        } catch (e: any) {
          appendMessage(agentId, `⚠️ Lost contact with mission ${missionId.slice(0, 8)}: ${e?.message || e}. It keeps running on the server; check Inbox later.`);
          window.clearInterval(missionPollersRef.current[missionId]);
          delete missionPollersRef.current[missionId];
          return;
        }
        if (m.status === 'planning' || m.status === 'running' || m.status === 'queued') {
          const done = m.tasks.filter((t) => t.status === 'succeeded' || t.status === 'failed').length;
          if (done > 0 && m.status === 'running') {
            appendMessage(agentId, `⏳ Mission ${missionId.slice(0, 8)} running: ${done}/${m.tasks.length} employee tasks finished.`);
          }
          return;
        }
        window.clearInterval(missionPollersRef.current[missionId]);
        delete missionPollersRef.current[missionId];
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
      }, 2500);
      missionPollersRef.current[missionId] = timer;
    },
    [appendMessage, deliverMissionToInbox]
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
            pollMission(mission.id, agentId);
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
    [appendAgentReply, appendMessage, agents, employeeProfiles, freeModels, pollMission, settings.defModel, settings.reason]
  );

  const handleDelegateTask = useCallback(async (payload: {
    employee_role: string; agent_profile_id?: string; title: string; instructions: string; input_context?: string;
    expected_output?: string; model_id: string; variant?: string;
  }) => {
    const mission = await createDelegatedMission({ ...payload, conversation_id: activeConvoId });
    appendMessage(activeConvoId, `📋 Employee task ${mission.id.slice(0, 8)} assigned to ${payload.employee_role}. The result will be available in Inbox.`);
    pollMission(mission.id, activeConvoId);
  }, [activeConvoId, appendMessage, pollMission]);

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
        pollMission(mission.id, agentId);
      } catch (e: any) {
        appendMessage(agentId, `❌ Could not start mission: ${e?.message || e}`);
      }
    })();
  }, [appendMessage, freeModels, pollMission, settings.defModel, settings.reason]);

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
            agentName: targetAgent ? targetAgent.name : 'AgentOS Employee',
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

  // Schedules
  const handleAddSchedule = useCallback(
    (pipeId: string, time: string, rep: 'Daily' | 'Weekdays' | 'Weekly') => {
      const newSchedule: ScheduleItem = {
        id: `s_${Date.now().toString(36)}`,
        pipe: pipeId,
        time,
        rep,
        on: true,
      };
      setSchedules((prev) => [...prev, newSchedule]);
      showToast('Schedule added');
    },
    [showToast]
  );

  const handleToggleSchedule = useCallback(
    (id: string, active: boolean) => {
      setSchedules((prev) =>
        prev.map((s) => (s.id === id ? { ...s, on: active } : s))
      );
      showToast(active ? 'Schedule resumed' : 'Schedule paused');
    },
    [showToast]
  );

  const handleDeleteSchedule = useCallback(
    (id: string) => {
      setSchedules((prev) => prev.filter((s) => s.id !== id));
      showToast('Schedule removed');
    },
    [showToast]
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
    const payload = { teams, agents, pipelines, schedules, runs, inbox, settings };
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
  }, [teams, agents, pipelines, schedules, runs, inbox, settings, showToast]);

  // Reset Workspace (back to the clean initial state)
  const handleResetDemo = useCallback(() => {
    const keys = [
      'agentos_teams_v2',
      'agentos_agents_v2',
      'agentos_inbox_v2',
      'agentos_runs_v2',
      'agentos_pipes_v2',
      'agentos_sched_v2',
      'agentos_msgs_v2',
      'agentos_settings',
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
    setPipelines(INITIAL_PIPELINES);
    setSchedules(INITIAL_SCHEDULE);
    setMessages(INITIAL_MESSAGES);
    setSettings(INITIAL_SETTINGS);
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
    { id: 'company', label: 'Company', icon: Building2 },
    { id: 'inbox', label: 'Inbox', icon: Inbox },
    { id: 'conversations', label: 'Conversations', icon: MessageSquare },
    { id: 'stream', label: 'Team Stream', icon: Radio },
    { id: 'results', label: 'Results', icon: BarChart3 },
    { id: 'calendar', label: 'Calendar', icon: Calendar },
    { id: 'settings', label: 'Settings', icon: Settings },
  ];

  return (
    <div
      className={`h-screen w-screen overflow-hidden grid transition-all duration-200 ${
        isStreamMode ? 'grid-cols-1' : 'grid-cols-1 md:grid-cols-[220px_1fr]'
      }`}
    >
      {/* Sidebar Navigation */}
      {!isStreamMode && (
        <aside className="border-r border-[var(--line)] p-4 flex flex-col gap-2 bg-[var(--card2)]/40 backdrop-blur-xl select-none">
          {/* Navigation Links */}
          <nav className="flex flex-col gap-1 flex-1">
            {navItems.map((item) => {
              const Icon = item.icon;
              const isActive = page === item.id;
              const showBadge = item.id === 'inbox' && pendingInboxCount > 0;

              return (
                <button
                  key={item.id}
                  onClick={() => navigateTo(item.id)}
                  className={`flex items-center justify-between gap-3 px-3 py-2 rounded-xl text-xs font-medium transition-all ${
                    isActive
                      ? 'bg-[var(--acc)]/15 text-[var(--text)] border-l-2 border-[var(--acc)]'
                      : 'text-[var(--muted)] hover:text-[var(--text)] hover:bg-white/5'
                  }`}
                >
                  <span className="flex items-center gap-2.5">
                    <Icon size={16} className={isActive ? 'text-[var(--acc)]' : 'text-[var(--dim)]'} />
                    <span>{item.label}</span>
                  </span>

                  {showBadge && (
                    <span className="min-w-5 h-[18px] px-1.5 rounded-full bg-[var(--acc)] text-[#18181b] font-bold text-[10px] flex items-center justify-center">
                      {pendingInboxCount}
                    </span>
                  )}
                </button>
              );
            })}
          </nav>

          {/* Sidebar Footer */}
          <div className="mt-auto p-3 border border-[var(--line)] rounded-xl text-xs text-[var(--muted)] bg-[var(--card)]/60">
            <b className="text-[var(--text)] block font-semibold mb-0.5">Local workspace</b>
            <div className="flex items-center gap-1.5 text-[11px] text-[var(--dim)]">
              <span>OpenCode</span>
              <span>·</span>
              <span className="flex items-center gap-1 text-[var(--green)]">
                <span className="w-1.5 h-1.5 rounded-full bg-[var(--green)] inline-block" />
                Online
              </span>
            </div>
          </div>
        </aside>
      )}

      {/* Main Container */}
      <div className="flex flex-col min-w-0 min-h-0 overflow-hidden relative">
        {/* Stream mode banner when active */}
        {isStreamMode && (
          <div className="px-4 py-1.5 bg-[var(--card2)] border-b border-[var(--line)] flex items-center justify-between text-xs text-[var(--muted)] z-30">
            <span className="flex items-center gap-2">
              <span className="w-2 h-2 rounded-full bg-[var(--acc)] animate-ping" />
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

        <main className="flex-1 overflow-y-auto p-5 sm:p-6 lg:p-7 min-h-0 relative">
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

          {page === 'calendar' && (
            <CalendarView
              schedules={schedules}
              pipelines={pipelines}
              onAddSchedule={handleAddSchedule}
              onToggleSchedule={handleToggleSchedule}
              onDeleteSchedule={handleDeleteSchedule}
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
          pipelinesCount={pipelines.length}
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
        <div
          role="status"
          className="fixed bottom-6 left-1/2 -translate-x-1/2 px-4 py-2.5 bg-[var(--card2)] border border-[var(--line2)] text-[var(--text)] text-xs font-medium rounded-xl shadow-2xl z-50 animate-in fade-in slide-in-from-bottom-2 duration-150 flex items-center gap-2"
        >
          <span className="w-2 h-2 rounded-full bg-[var(--acc)] inline-block shadow-[0_0_8px_var(--acc)]" />
          <span>{toastMessage}</span>
        </div>
      )}
    </div>
  );
}

export default App;
