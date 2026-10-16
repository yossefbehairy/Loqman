import React, { useMemo, useState } from 'react';
import type { TeamTurn, Agent, Team } from '../../types/agentos';
import {
  MessagesSquare,
  ArrowRight,
  Clock,
  ShieldCheck,
  Search,
  Copy,
  Check,
  FileCode2,
  Users,
  Radio,
  ChevronDown,
  ChevronUp,
} from 'lucide-react';

interface Props {
  agents: Agent[];
  teams: Team[];
}

type EnrichedTurn = TeamTurn & { agentId?: string; department?: string; uid: string };

export const TeamStreamView: React.FC<Props> = ({ agents, teams }) => {
  const [filterAgent, setFilterAgent] = useState<string>('all');
  const [searchQuery, setSearchQuery] = useState('');
  const [expandedTurn, setExpandedTurn] = useState<string | null>(null);
  const [copiedTurn, setCopiedTurn] = useState<string | null>(null);

  // Collect all turns from agents' active tasks (no seeded demo content).
  const allTurns = useMemo<EnrichedTurn[]>(() => {
    const list: EnrichedTurn[] = [];
    let idx = 0;
    const push = (t: TeamTurn, agentId: string | undefined, department: string | undefined, fullOutput: unknown) => {
      const resolved = ((t as { fullOutput?: unknown }).fullOutput ?? fullOutput) as Record<string, any> | undefined;
      list.push({
        ...t,
        agentId,
        department,
        fullOutput: resolved,
        uid: `${agentId ?? 'live'}::${t.turn}::${t.from}::${t.to}::${idx++}`,
      });
    };

    agents.forEach((agent) => {
      if (agent.activeTask?.turns) {
        agent.activeTask.turns.forEach((t) => {
          push(t, agent.id, agent.activeTask?.department, (agent.activeTask as { fullJson?: unknown })?.fullJson);
        });
      }
    });
    return list;
  }, [agents]);

  // Deduplicate exact duplicates, keyed by agent + turn + parties + text
  // so legit distinct turns survive.
  const uniqueTurns = useMemo(() => {
    const seen = new Map<string, EnrichedTurn>();
    for (const item of allTurns) {
      const key = `${item.agentId}::${item.turn}::${item.from}::${item.to}::${item.text.slice(0, 120)}`;
      if (!seen.has(key)) seen.set(key, item);
    }
    return Array.from(seen.values()).sort((a, b) => a.turn - b.turn || a.uid.localeCompare(b.uid));
  }, [allTurns]);

  const filteredTurns = useMemo(() => {
    const q = searchQuery.trim().toLowerCase();
    return uniqueTurns.filter((turn) => {
      if (filterAgent !== 'all' && turn.agentId !== filterAgent) return false;
      if (q) {
        return (
          turn.text.toLowerCase().includes(q) ||
          turn.from.toLowerCase().includes(q) ||
          turn.to.toLowerCase().includes(q)
        );
      }
      return true;
    });
  }, [uniqueTurns, filterAgent, searchQuery]);

  const copyTurnText = async (uid: string, text: string) => {
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      // Fallback for non-secure contexts / denied permissions
      try {
        const ta = document.createElement('textarea');
        ta.value = text;
        ta.style.position = 'fixed';
        ta.style.opacity = '0';
        document.body.appendChild(ta);
        ta.select();
        document.execCommand('copy');
        ta.remove();
      } catch {
        /* ignore */
      }
    }
    setCopiedTurn(uid);
    window.setTimeout(() => setCopiedTurn((prev) => (prev === uid ? null : prev)), 2000);
  };

  return (
    <div className="flex flex-col gap-4 max-w-5xl">
      {/* Header */}
      <div className="flex items-end justify-between gap-3 flex-wrap">
        <div>
          <div className="flex items-center gap-2 mb-1">
            <span className="os-tag os-tag-mono text-xs">
              <Radio size={12} className="animate-pulse" />
              Live Inter-Agent Stream
            </span>
            <span className="os-tag os-tag-green text-xs">● Stream Ready</span>
          </div>
          <h1 className="text-2xl font-bold tracking-tight text-[var(--text)] m-0">
            Team Dialogue Stream (حوار ونقاش الوكلاء)
          </h1>
          <p className="text-xs text-[var(--muted)] m-0 mt-1">
            سجل الحوار الفني والتبادل المباشر بين الوكلاء لتنفيذ وتدقيق المهام المعمارية وتسليم المخرجات.
          </p>
        </div>

        <div className="flex items-center gap-2 ml-auto">
          <div className="relative w-64 max-w-full">
            <Search size={14} className="absolute left-3 top-2.5 text-[var(--dim)] pointer-events-none" />
            <input
              type="text"
              placeholder="Search dialogues..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="pl-8 pr-3 py-1.5 text-xs rounded-xl"
              aria-label="Search dialogues"
            />
          </div>

          <select
            value={filterAgent}
            onChange={(e) => setFilterAgent(e.target.value)}
            className="w-40 py-1.5 text-xs rounded-xl"
            aria-label="Filter agent"
          >
            <option value="all">All Agents</option>
            {agents.map((a) => (
              <option key={a.id} value={a.id}>
                {a.name}
              </option>
            ))}
          </select>
        </div>
      </div>

      {/* Stream Overview Bar */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
        <div className="os-card p-3.5 flex items-center gap-3">
          <div className="w-9 h-9 rounded-xl bg-[var(--acc)]/10 text-[var(--acc)] border border-[var(--acc)]/30 flex items-center justify-center flex-none">
            <MessagesSquare size={18} />
          </div>
          <div>
            <div className="text-[11px] text-[var(--muted)]">Recorded Handover Turns</div>
            <div className="text-lg font-bold text-[var(--text)] mono">{uniqueTurns.length} Turns</div>
          </div>
        </div>

        <div className="os-card p-3.5 flex items-center gap-3">
          <div className="w-9 h-9 rounded-xl bg-[var(--acc2)]/10 text-[var(--acc2)] border border-[var(--acc2)]/30 flex items-center justify-center flex-none">
            <Users size={18} />
          </div>
          <div>
            <div className="text-[11px] text-[var(--muted)]">Collaborating Departments</div>
            <div className="text-lg font-bold text-[var(--text)] mono">{teams.length} Teams</div>
          </div>
        </div>

        <div className="os-card p-3.5 flex items-center gap-3">
          <div className="w-9 h-9 rounded-xl bg-[var(--green)]/10 text-[var(--green)] border border-[var(--green)]/30 flex items-center justify-center flex-none">
            <ShieldCheck size={18} />
          </div>
          <div>
            <div className="text-[11px] text-[var(--muted)]">Context Integrity</div>
            <div className="text-lg font-bold text-[var(--green)] mono">100% Deterministic</div>
          </div>
        </div>
      </div>

      {/* Turns Timeline Feed */}
      <div className="flex flex-col gap-3">
        {filteredTurns.map((t) => {
          const isExpanded = expandedTurn === t.uid;
          const isCopied = copiedTurn === t.uid;

          return (
            <div
              key={t.uid}
              className="os-card p-4 flex flex-col gap-3 hover:border-[var(--line-strong)] transition-all"
            >
              {/* Header */}
              <div className="flex items-center justify-between gap-3 flex-wrap">
                <div className="flex items-center gap-2.5 flex-wrap">
                  <span className="mono text-xs font-bold px-2.5 py-0.5 rounded-full bg-[var(--card2)] text-[var(--acc)] border border-[var(--line2)] shadow-sm">
                    Turn {t.turn}
                  </span>

                  <span className="font-semibold text-sm text-[var(--text)]">{t.from}</span>
                  <span className="text-xs text-[var(--dim)]">({t.role})</span>

                  <ArrowRight size={13} className="text-[var(--acc)] flex-none" />

                  <span className="text-xs font-medium text-[var(--muted)] bg-[var(--card2)] px-2 py-0.5 rounded-md border border-[var(--line)]">
                    {t.to}
                  </span>
                </div>

                <div className="flex items-center gap-3 ml-auto">
                  <span className="mono text-xs text-[var(--dim)] flex items-center gap-1">
                    <Clock size={12} />
                    {t.time}
                  </span>

                  <button
                    onClick={() => copyTurnText(t.uid, t.text)}
                    className="p-1 text-[var(--dim)] hover:text-[var(--text)] transition-colors"
                    title="Copy message text"
                    aria-label="Copy message text"
                  >
                    {isCopied ? <Check size={14} className="text-[var(--green)]" /> : <Copy size={14} />}
                  </button>
                </div>
              </div>

              {/* Message Body */}
              <div className="p-3.5 rounded-xl bg-[var(--card2)] text-xs text-[var(--text)] leading-relaxed whitespace-pre-wrap select-text font-sans border border-[var(--line)]/50">
                {t.text}
              </div>

              {/* Expandable Deliverable Payload */}
              {t.fullOutput && (
                <div className="pt-1">
                  <button
                    onClick={() => setExpandedTurn(isExpanded ? null : t.uid)}
                    className="text-xs font-semibold text-[var(--acc)] hover:underline flex items-center gap-1.5"
                    aria-expanded={isExpanded}
                  >
                    <FileCode2 size={13} />
                    <span>{isExpanded ? 'Hide attached payload schema' : 'View attached deliverable payload JSON'}</span>
                    {isExpanded ? <ChevronUp size={13} /> : <ChevronDown size={13} />}
                  </button>

                  {isExpanded && (
                    <pre className="mono p-3 mt-2 rounded-xl bg-[var(--card2)] border border-[var(--line)] text-[11px] text-[var(--acc)] overflow-x-auto max-h-60 select-text leading-relaxed">
                      {JSON.stringify(t.fullOutput, null, 2)}
                    </pre>
                  )}
                </div>
              )}
            </div>
          );
        })}

        {filteredTurns.length === 0 && (
          <div className="os-card text-center py-16 text-xs text-[var(--muted)]">
            No dialogue yet — turns appear here when agents report.
          </div>
        )}
      </div>
    </div>
  );
};
