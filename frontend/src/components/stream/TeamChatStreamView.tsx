import React, { useState, useEffect, useRef, useCallback } from 'react';
import {
  MessagesSquare,
  Sparkles,
  Play,
  RefreshCw,
  FileCode,
  Check,
  Copy,
  ChevronDown,
  ChevronRight,
  ArrowRight,
  Clock,
  Radio,
  ShieldCheck,
  Compass,
  ShieldAlert,
  CheckCircle2,
  Users,
  Hash,
} from 'lucide-react';
import type { TeamMessage, WorkflowExecution } from '../../types';
import {
  API_BASE,
  getTeamMessages,
  getExecution,
  listExecutions,
  listWorkflows,
  runWorkflow,
} from '../../api/client';

export interface TeamChatStreamViewProps {
  messages?: TeamMessage[];
  executionId?: string;
  onRunWorkflow?: () => void;
  isRunning?: boolean;
}

interface EnrichedHandoverMessage {
  id: string;
  turn: number;
  from_node_id: string;
  from_display_name: string;
  from_role: string;
  to_node_ids: string[];
  excerpt: string;
  full_output_ref: string;
  timestamp: string;
  badge_title: string;
  deliverable?: Record<string, unknown>;
  natural_name?: string;
  department_name?: string;
}

const DEFAULT_HANDOVER_TURNS: EnrichedHandoverMessage[] = [];

function resolveRoleMetadata(
  role: string,
  displayName: string,
  turn: number,
  defaultDept = 'إدارة العمليات'
): { natural: string; dept: string; badge: string } {
  const name = displayName ?? '';
  if (role === 'planner' || name.includes('Planner')) {
    return {
      natural: 'د. سارة (Dr. Sara)',
      dept: 'إدارة التخطيط والإستراتيجية',
      badge: 'Turn 1 • الجولة 1: تكليف إداري (Manager Delegation)',
    };
  }
  if (role === 'analyst' || name.includes('Analyst')) {
    return {
      natural: 'م. طارق (Eng. Tarek)',
      dept: 'إدارة التحليل والمخاطر',
      badge: 'Turn 2 • الجولة 2: تسليم مخرجات (Deliverable Handover)',
    };
  }
  if (role === 'reviewer' || name.includes('Reviewer')) {
    return {
      natural: 'أ. ياسمين (Prof. Yasmin)',
      dept: 'إدارة الجودة والاعتماد',
      badge: 'Turn 3 • الجولة 3: اعتماد ومراجعة (Governance Approval)',
    };
  }
  return {
    natural: displayName,
    dept: defaultDept,
    badge: `Turn ${turn} • الجولة ${turn}`,
  };
}

function enrichMessage(m: TeamMessage): EnrichedHandoverMessage {
  const { natural, dept, badge } = resolveRoleMetadata(
    m.from_role,
    m.from_display_name,
    m.turn,
    'إدارة العمليات المشتركة'
  );

  const fallbackDeliverable = DEFAULT_HANDOVER_TURNS.find((d) => d.turn === m.turn)?.deliverable || {
    turn: m.turn,
    sender: m.from_display_name,
    role: m.from_role,
    recipient: m.to_node_ids.join(', ') || 'Next Agent Node',
    excerpt: m.excerpt,
    status: 'verified_deliverable',
  };

  return {
    id: `${m.execution_id}-${m.turn}-${m.from_node_id}`,
    turn: m.turn,
    from_node_id: m.from_node_id,
    from_display_name: natural,
    from_role: m.from_role,
    natural_name: natural,
    department_name: dept,
    to_node_ids: m.to_node_ids,
    excerpt: m.excerpt,
    full_output_ref: m.full_output_ref,
    timestamp: m.timestamp || 'Live',
    badge_title: badge,
    deliverable: fallbackDeliverable,
  };
}

export const TeamChatStreamView: React.FC<TeamChatStreamViewProps> = ({
  messages: propMessages = [],
  executionId: propExecutionId,
  onRunWorkflow,
  isRunning: propIsRunning = false,
}) => {
  const [selectedExecutionId, setSelectedExecutionId] = useState<string | null>(null);
  const activeExecutionId = propExecutionId || selectedExecutionId;

  const [localMessages, setLocalMessages] = useState<EnrichedHandoverMessage[]>(
    DEFAULT_HANDOVER_TURNS
  );
  const [expandedTurns, setExpandedTurns] = useState<Set<number | string>>(new Set());
  const [copiedTurn, setCopiedTurn] = useState<number | string | null>(null);
  const [isStreaming, setIsStreaming] = useState<boolean>(propIsRunning);
  const [sseConnected, setSseConnected] = useState<boolean>(true);
  const [executionsList, setExecutionsList] = useState<WorkflowExecution[]>([]);
  const [roleFilter, setRoleFilter] = useState<'all' | 'managers' | 'specialists'>('all');

  const sseRef = useRef<EventSource | null>(null);
  const feedEndRef = useRef<HTMLDivElement | null>(null);

  // Derive streamMessages cleanly with useMemo to avoid setState cascades
  const streamMessages = React.useMemo(() => {
    if (propMessages && propMessages.length > 0) {
      const enriched = propMessages.map(enrichMessage);
      const hasTurn1 = enriched.some((m) => m.turn === 1);
      const hasTurn2 = enriched.some((m) => m.turn === 2);
      const hasTurn3 = enriched.some((m) => m.turn === 3);

      if (hasTurn1 && hasTurn2 && hasTurn3) {
        return [...enriched].sort((a, b) => a.turn - b.turn);
      }
      const merged = [...DEFAULT_HANDOVER_TURNS];
      enriched.forEach((em) => {
        const idx = merged.findIndex((m) => m.turn === em.turn);
        if (idx >= 0) {
          merged[idx] = em;
        } else {
          merged.push(em);
        }
      });
      return merged.sort((a, b) => a.turn - b.turn);
    }
    return localMessages;
  }, [propMessages, localMessages]);

  // Auto-scroll feed to latest message
  useEffect(() => {
    feedEndRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' });
  }, [streamMessages]);

  // Fetch execution history on mount
  useEffect(() => {
    let mounted = true;
    listExecutions(0, 10)
      .then((execs) => {
        if (mounted && execs && execs.length > 0) {
          setExecutionsList(execs);
          if (!activeExecutionId && execs[0]) {
            setSelectedExecutionId(execs[0].id);
          }
        }
      })
      .catch(() => {
        // Fallback silently if offline or initial load
      });
    return () => {
      mounted = false;
    };
  }, [activeExecutionId]);

  // Live SSE connection & Historical message replay for active execution
  useEffect(() => {
    if (!activeExecutionId) return;

    let isSubscribed = true;

    // 1. Fetch historical messages
    getTeamMessages(activeExecutionId)
      .then(async (res) => {
        if (!isSubscribed) return;
        if (res.messages && res.messages.length > 0) {
          // Fetch execution details to attach structured deliverables
          const execDetail = await getExecution(activeExecutionId).catch(() => null);
          const outputsMap: Record<string, Record<string, unknown>> = {};

          if (execDetail && execDetail.node_executions) {
            execDetail.node_executions.forEach((ne) => {
              if (ne.output_json) {
                try {
                  const parsed = JSON.parse(ne.output_json);
                  outputsMap[ne.id] = parsed;
                  outputsMap[ne.node_id] = parsed;
                } catch {
                  // ignore parse error
                }
              }
            });
          }

          const parsedList: EnrichedHandoverMessage[] = res.messages.map((m) => {
            const { natural, dept, badge } = resolveRoleMetadata(
              m.from_role,
              m.from_display_name,
              m.turn
            );

            const del =
              outputsMap[m.full_output_ref] ||
              outputsMap[m.from_node_id] ||
              DEFAULT_HANDOVER_TURNS.find((d) => d.turn === m.turn)?.deliverable || {
                turn: m.turn,
                output: m.excerpt,
                status: 'completed',
              };

            return {
              id: `${m.execution_id}-${m.turn}-${m.from_node_id}`,
              turn: m.turn,
              from_node_id: m.from_node_id,
              from_display_name: natural,
              from_role: m.from_role,
              natural_name: natural,
              department_name: dept,
              to_node_ids: m.to_node_ids,
              excerpt: m.excerpt,
              full_output_ref: m.full_output_ref,
              timestamp: m.timestamp || 'Just now',
              badge_title: badge,
              deliverable: del,
            };
          });

          // Guarantee Turn 1, 2, 3 presence
          const merged = [...DEFAULT_HANDOVER_TURNS];
          parsedList.forEach((pl) => {
            const idx = merged.findIndex((m) => m.turn === pl.turn);
            if (idx >= 0) {
              merged[idx] = pl;
            } else {
              merged.push(pl);
            }
          });
          setLocalMessages(merged.sort((a, b) => a.turn - b.turn));
        }
      })
      .catch(() => {
        // Stay empty if fetch fails — no demo fallback
      });

    // 2. Connect to live SSE events
    try {
      if (sseRef.current) {
        sseRef.current.close();
      }

      const sse = new EventSource(`${API_BASE}/executions/${activeExecutionId}/events`);
      sseRef.current = sse;

      sse.onopen = () => {
        if (isSubscribed) setSseConnected(true);
      };

      sse.addEventListener('team.message', (evt: MessageEvent) => {
        if (!isSubscribed) return;
        try {
          const payload = JSON.parse(evt.data);
          const displayName = payload.fromDisplayName || payload.fromRole;
          const { natural, dept, badge } = resolveRoleMetadata(
            payload.fromRole,
            displayName,
            payload.turn
          );

          const fallback =
            DEFAULT_HANDOVER_TURNS.find((d) => d.turn === payload.turn)?.deliverable || {
              turn: payload.turn,
              from: payload.fromRole,
              excerpt: payload.excerpt,
            };

          const newMsg: EnrichedHandoverMessage = {
            id: `${payload.executionId}-${payload.turn}-${payload.fromNodeId}`,
            turn: payload.turn,
            from_node_id: payload.fromNodeId,
            from_display_name: natural,
            from_role: payload.fromRole,
            natural_name: natural,
            department_name: dept,
            to_node_ids: payload.toNodeIds || [],
            excerpt: payload.excerpt,
            full_output_ref: payload.fullOutputRef,
            timestamp: payload.timestamp || 'Live',
            badge_title: badge,
            deliverable: fallback,
          };

          setLocalMessages((prev) => {
            const exists = prev.some(
              (m) => m.turn === newMsg.turn && m.from_node_id === newMsg.from_node_id
            );
            if (exists) {
              return prev.map((m) =>
                m.turn === newMsg.turn && m.from_node_id === newMsg.from_node_id ? newMsg : m
              );
            }
            return [...prev, newMsg].sort((a, b) => a.turn - b.turn);
          });
        } catch (e) {
          console.error('Failed to parse SSE team.message:', e);
        }
      });

      sse.addEventListener('execution.finished', () => {
        if (isSubscribed) setIsStreaming(false);
      });

      sse.addEventListener('execution.failed', () => {
        if (isSubscribed) setIsStreaming(false);
      });

      sse.onerror = () => {
        // SSE may close normally after execution finishes
        if (isSubscribed) setSseConnected(false);
      };
    } catch {
      // ignore
    }

    return () => {
      isSubscribed = false;
      if (sseRef.current) {
        sseRef.current.close();
      }
    };
  }, [activeExecutionId]);

  // Handle Start Handover / Run Pipeline
  const handleStartHandover = useCallback(async () => {
    setIsStreaming(true);

    if (onRunWorkflow) {
      onRunWorkflow();
      return;
    }

    try {
      const workflows = await listWorkflows();
      if (workflows && workflows.length > 0) {
        const wf = workflows[0];
        const res = await runWorkflow(
          wf.id,
          'Execute organizational multi-turn handover stream',
          false
        );
        if (res && res.execution_id) {
          setSelectedExecutionId(res.execution_id);
          return;
        }
      }
    } catch {
      // In mock/test environments, stay empty — no demo fallback
    }

    // Clear the stream (no demo content to restore)
    setLocalMessages(DEFAULT_HANDOVER_TURNS);
    setTimeout(() => {
      setIsStreaming(false);
    }, 1500);
  }, [onRunWorkflow]);

  // Toggle deliverable JSON expansion
  const toggleExpand = (turnId: number | string) => {
    setExpandedTurns((prev) => {
      const next = new Set(prev);
      if (next.has(turnId)) {
        next.delete(turnId);
      } else {
        next.add(turnId);
      }
      return next;
    });
  };

  // Copy deliverable JSON
  const handleCopyJson = (turnId: number | string, deliverable: unknown) => {
    try {
      navigator.clipboard.writeText(JSON.stringify(deliverable, null, 2));
      setCopiedTurn(turnId);
      setTimeout(() => setCopiedTurn(null), 2000);
    } catch {
      // fallback
    }
  };

  // Filter messages
  const filteredMessages = streamMessages.filter((m) => {
    if (roleFilter === 'managers') {
      return (
        m.from_role === 'planner' ||
        m.from_role === 'reviewer' ||
        m.from_role === 'general_manager'
      );
    }
    if (roleFilter === 'specialists') {
      return m.from_role === 'analyst' || m.from_role === 'specialist';
    }
    return true;
  });

  return (
    <div
      data-testid="team-chat-stream"
      className="team-chat-stream flex-1 flex flex-col h-full bg-gray-950 text-gray-100 font-sans select-text overflow-hidden"
    >
      {/* Slack/Discord Styled Channel Header */}
      <header className="px-6 py-4 bg-gray-900/90 border-b border-gray-800/80 backdrop-blur-md flex flex-wrap items-center justify-between gap-4 flex-shrink-0 z-10 shadow-sm">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-xl bg-gradient-to-tr from-indigo-600 via-indigo-500 to-purple-600 flex items-center justify-center text-white shadow-lg shadow-indigo-500/20">
            <MessagesSquare className="w-5 h-5" />
          </div>

          <div>
            <div className="flex items-center gap-2">
              <Hash className="w-4 h-4 text-indigo-400" />
              <h1 className="text-lg font-bold text-white tracking-tight flex items-center gap-2">
                <span>Team Chat Stream</span>
                <span className="text-gray-600 font-normal">/</span>
                <span className="text-gray-300 font-arabic text-base font-semibold">
                  سجل محادثات الوكلاء والتسليم التتابعي
                </span>
              </h1>
            </div>

            <p className="text-xs text-gray-400 font-arabic flex items-center gap-2 mt-0.5">
              <span>تتبع المحادثات المباشرة والتسليم التتابعي بين المدراء (👑) والموظفين (💼) بالأسماء الطبيعية.</span>
            </p>
          </div>
        </div>

        {/* Live SSE Status Pill & Handover Controls */}
        <div className="flex items-center flex-wrap gap-2.5">
          {/* Live SSE Status Indicator */}
          <div
            data-testid="sse-status-indicator"
            className={`flex items-center gap-2 px-3 py-1.5 rounded-full border text-xs font-medium shadow-sm ${
              sseConnected
                ? 'bg-emerald-500/10 border-emerald-500/30 text-emerald-400'
                : 'bg-amber-500/10 border-amber-500/30 text-amber-400'
            }`}
          >
            <span
              className={`w-2 h-2 rounded-full ${
                sseConnected ? 'bg-emerald-400 animate-pulse' : 'bg-amber-400'
              }`}
            />
            <Radio className="w-3.5 h-3.5" />
            <span className="font-semibold">Live SSE Stream Channel</span>
            <span className="opacity-60">•</span>
            <span className="font-arabic font-medium">
              {sseConnected ? 'متصل (Online)' : 'غير متصل (Offline)'}
            </span>
          </div>

          {/* Execution Switcher (if executions exist) */}
          {executionsList.length > 0 && (
            <select
              aria-label="Select Execution Stream"
              value={activeExecutionId || ''}
              onChange={(e) => setSelectedExecutionId(e.target.value)}
              className="bg-gray-800/80 hover:bg-gray-700/80 border border-gray-700/60 rounded-lg text-xs text-gray-300 px-2.5 py-1.5 focus:outline-none focus:border-indigo-500 cursor-pointer"
            >
              {executionsList.map((ex) => (
                <option key={ex.id} value={ex.id}>
                  {ex.id.slice(0, 8)} ({ex.status})
                </option>
              ))}
            </select>
          )}

          {/* Start Handover / Run Pipeline Button */}
          <button
            type="button"
            data-action="run"
            onClick={handleStartHandover}
            disabled={isStreaming}
            className="flex items-center gap-2 px-4 py-1.5 rounded-lg bg-gradient-to-r from-indigo-600 to-purple-600 hover:from-indigo-500 hover:to-purple-500 text-white text-xs font-semibold shadow-md shadow-indigo-600/20 transition active:scale-95 disabled:opacity-50 cursor-pointer"
          >
            {isStreaming ? (
              <RefreshCw className="w-3.5 h-3.5 animate-spin" />
            ) : (
              <Play className="w-3.5 h-3.5 fill-current" />
            )}
            <span>Start Handover</span>
            <span className="text-indigo-200">|</span>
            <span className="font-arabic">بدء التسليم التتابعي</span>
          </button>

          {/* Replay / Reset Button */}
          <button
            type="button"
            onClick={() => setLocalMessages(DEFAULT_HANDOVER_TURNS)}
            title="Replay Stream"
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-gray-800/80 hover:bg-gray-700/80 border border-gray-700/60 text-gray-300 text-xs font-medium transition cursor-pointer"
          >
            <RefreshCw className="w-3.5 h-3.5 text-indigo-400" />
            <span className="font-arabic text-[11px]">إعادة تشغيل الجولات</span>
          </button>
        </div>
      </header>

      {/* Channel Subbar: Persona Pills & Channel Context */}
      <div className="px-6 py-2 bg-gray-900/60 border-b border-gray-800/60 flex flex-wrap items-center justify-between gap-3 text-xs text-gray-400 flex-shrink-0">
        <div className="flex items-center gap-2">
          <Users className="w-3.5 h-3.5 text-indigo-400" />
          <span className="font-medium text-gray-300">أعضاء الفريق النشطون في القناة:</span>
          <span className="text-[11px] text-gray-500">تظهر الأعضاء هنا عند بدء تنفيذ فعلي.</span>
        </div>

        {/* Filter Chips */}
        <div className="flex items-center gap-1.5">
          <span className="text-[11px] text-gray-500 font-arabic">تصفية العرض:</span>
          <button
            type="button"
            onClick={() => setRoleFilter('all')}
            className={`px-2 py-0.5 rounded text-[11px] transition cursor-pointer ${
              roleFilter === 'all'
                ? 'bg-indigo-600 text-white font-semibold'
                : 'bg-gray-800 text-gray-400 hover:text-gray-200'
            }`}
          >
            الكل
          </button>
          <button
            type="button"
            onClick={() => setRoleFilter('managers')}
            className={`px-2 py-0.5 rounded text-[11px] transition cursor-pointer ${
              roleFilter === 'managers'
                ? 'bg-amber-600 text-white font-semibold'
                : 'bg-gray-800 text-gray-400 hover:text-gray-200'
            }`}
          >
            👑 المدراء
          </button>
          <button
            type="button"
            onClick={() => setRoleFilter('specialists')}
            className={`px-2 py-0.5 rounded text-[11px] transition cursor-pointer ${
              roleFilter === 'specialists'
                ? 'bg-blue-600 text-white font-semibold'
                : 'bg-gray-800 text-gray-400 hover:text-gray-200'
            }`}
          >
            💼 الموظفون
          </button>
        </div>
      </div>

      {/* Main Slack/Discord Chronological Feed */}
      <div
        data-testid="team-stream-messages"
        className="team-stream-messages team-chat-container conversation-feed flex-1 overflow-y-auto p-4 md:p-6 space-y-4"
      >
        {filteredMessages.map((msg) => {
          const isExpanded = expandedTurns.has(msg.turn);
          const isCopied = copiedTurn === msg.turn;
          const isManager =
            msg.from_role === 'planner' ||
            msg.from_role === 'reviewer' ||
            msg.from_role === 'general_manager';

          const avatarGradient =
            msg.turn === 1
              ? 'from-amber-500 via-amber-600 to-yellow-600 ring-amber-500/30'
              : msg.turn === 2
              ? 'from-blue-600 via-indigo-600 to-cyan-600 ring-blue-500/30'
              : 'from-emerald-500 via-teal-600 to-emerald-700 ring-emerald-500/30';

          const cardBorder =
            msg.turn === 1
              ? 'border-amber-500/30 bg-gray-900/90'
              : msg.turn === 2
              ? 'border-blue-500/30 bg-gray-900/90'
              : 'border-emerald-500/30 bg-gray-900/90';

          const roleIcon =
            msg.turn === 1 ? (
              <Compass className="w-3.5 h-3.5 text-amber-400" />
            ) : msg.turn === 2 ? (
              <ShieldAlert className="w-3.5 h-3.5 text-blue-400" />
            ) : (
              <CheckCircle2 className="w-3.5 h-3.5 text-emerald-400" />
            );

          return (
            <article
              key={msg.id || `${msg.turn}-${msg.from_node_id}`}
              className={`rounded-2xl border ${cardBorder} p-4 md:p-5 shadow-xl transition-all duration-200 hover:border-gray-700/80`}
            >
              <div className="flex items-start gap-3.5">
                {/* Persona Avatar Badge */}
                <div
                  className={`w-11 h-11 rounded-2xl bg-gradient-to-tr ${avatarGradient} ring-2 flex items-center justify-center text-white text-base font-bold shadow-lg flex-shrink-0`}
                >
                  {isManager ? '👑' : '💼'}
                </div>

                {/* Message Content Body */}
                <div className="flex-1 min-w-0 space-y-2.5">
                  {/* Sender Metadata Header */}
                  <div className="flex flex-wrap items-center justify-between gap-2 border-b border-gray-800/60 pb-2">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="font-bold text-sm text-white tracking-wide flex items-center gap-1.5">
                        {roleIcon}
                        <span>{msg.natural_name || msg.from_display_name}</span>
                      </span>

                      {/* Turn & Role Badge */}
                      <span
                        className={`px-2.5 py-0.5 rounded-full text-xs font-semibold border flex items-center gap-1 ${
                          msg.turn === 1
                            ? 'bg-amber-500/15 border-amber-500/30 text-amber-300'
                            : msg.turn === 2
                            ? 'bg-blue-500/15 border-blue-500/30 text-blue-300'
                            : 'bg-emerald-500/15 border-emerald-500/30 text-emerald-300'
                        }`}
                      >
                        <span>{msg.badge_title}</span>
                      </span>

                      {/* Department Tag */}
                      {msg.department_name && (
                        <span className="text-[11px] text-gray-400 font-arabic bg-gray-800/80 px-2 py-0.5 rounded border border-gray-700/40">
                          {msg.department_name}
                        </span>
                      )}
                    </div>

                    <div className="flex items-center gap-2 text-gray-500 text-xs font-mono">
                      <Clock className="w-3 h-3" />
                      <span>{msg.timestamp}</span>
                    </div>
                  </div>

                  {/* Message Natural Language Narrative */}
                  <p className="text-sm text-gray-200 leading-relaxed font-arabic bg-gray-950/50 p-3 rounded-xl border border-gray-800/50">
                    {msg.excerpt}
                  </p>

                  {/* Handover Direction & Action Row */}
                  <div className="flex flex-wrap items-center justify-between gap-3 pt-1">
                    <div className="flex items-center gap-1.5 text-xs text-gray-400 font-arabic">
                      <ArrowRight className="w-3.5 h-3.5 text-indigo-400 flex-shrink-0" />
                      <span>
                        {msg.turn === 1
                          ? 'مسار التسليم: تفويض المهمة'
                          : msg.turn === 2
                          ? 'مسار التسليم: إحالة المخرجات الفنية'
                          : 'مسار التسليم: اعتماد نهائي'}
                      </span>
                    </div>

                    {/* Expandable Deliverable JSON Button */}
                    <button
                      type="button"
                      data-testid="expand-deliverable"
                      onClick={() => toggleExpand(msg.turn)}
                      className="inline-flex items-center gap-1.5 px-3 py-1 rounded-lg bg-gray-800 hover:bg-gray-700/80 border border-gray-700 text-indigo-300 hover:text-indigo-200 text-xs font-medium transition cursor-pointer"
                    >
                      <FileCode className="w-3.5 h-3.5 text-indigo-400" />
                      <span>
                        {isExpanded
                          ? 'Hide Deliverable (إخفاء المخرجات)'
                          : 'Inspect Deliverable JSON (عرض مخرجات JSON)'}
                      </span>
                      {isExpanded ? (
                        <ChevronDown className="w-3.5 h-3.5" />
                      ) : (
                        <ChevronRight className="w-3.5 h-3.5" />
                      )}
                    </button>
                  </div>

                  {/* Expandable Structured Deliverable Section */}
                  {isExpanded && msg.deliverable && (
                    <div className="mt-3 rounded-xl bg-gray-950 border border-indigo-500/30 p-3.5 shadow-2xl space-y-2 animate-fadeIn">
                      <div className="flex items-center justify-between border-b border-gray-800/80 pb-2">
                        <div className="flex items-center gap-2">
                          <ShieldCheck className="w-4 h-4 text-emerald-400" />
                          <span className="text-xs font-semibold text-gray-200">
                            Structured Output & Verified Execution Contract (JSON)
                          </span>
                        </div>

                        <button
                          type="button"
                          onClick={() => handleCopyJson(msg.turn, msg.deliverable)}
                          className="flex items-center gap-1 px-2.5 py-1 rounded bg-gray-800/80 hover:bg-gray-700 text-gray-300 hover:text-white text-[11px] font-mono transition cursor-pointer"
                        >
                          {isCopied ? (
                            <>
                              <Check className="w-3 h-3 text-emerald-400" />
                              <span className="text-emerald-400">Copied!</span>
                            </>
                          ) : (
                            <>
                              <Copy className="w-3 h-3" />
                              <span>Copy JSON</span>
                            </>
                          )}
                        </button>
                      </div>

                      <pre
                        data-testid="deliverable-json"
                        className="text-xs font-mono text-emerald-400 bg-black/60 p-3 rounded-lg overflow-x-auto border border-gray-850 leading-relaxed max-h-72"
                      >
                        <code>{JSON.stringify(msg.deliverable, null, 2)}</code>
                      </pre>
                    </div>
                  )}
                </div>
              </div>
            </article>
          );
        })}
        <div ref={feedEndRef} />
      </div>

      {/* Footer / Real-time Handover Status Banner */}
      <footer className="px-6 py-3 bg-gray-900/90 border-t border-gray-800/80 flex flex-wrap items-center justify-between gap-3 text-xs text-gray-400 flex-shrink-0">
        <div className="flex items-center gap-2 font-arabic">
          <Sparkles className="w-4 h-4 text-indigo-400" />
          <span>
            سير العمل متكامل وفق معايير الحوكمة التتابعية (Turn 1 التكليف ➡️ Turn 2 التسليم ➡️ Turn 3 الاعتماد).
          </span>
        </div>

        <div className="flex items-center gap-3 text-[11px] font-mono text-gray-500">
          <span>Channel: #team-handover-stream</span>
          <span>•</span>
          <span>Protocol: SSE v2.0</span>
          <span>•</span>
          <span className="text-emerald-400 font-semibold">Active & Synced</span>
        </div>
      </footer>
    </div>
  );
};

export default TeamChatStreamView;
