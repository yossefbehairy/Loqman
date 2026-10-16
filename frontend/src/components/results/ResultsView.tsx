import React, { useState } from 'react';
import type { RunReceipt, InboxItem, Agent } from '../../types/agentos';
import { Download, AlertCircle, TrendingUp, CheckCircle, Clock, Cpu } from 'lucide-react';

interface Props {
  runs: RunReceipt[];
  inbox: InboxItem[];
  agents: Agent[];
  onConnectSource: () => void;
  onExportCSV: (period: string) => void;
}

export const ResultsView: React.FC<Props> = ({
  runs,
  inbox,
  agents,
  onConnectSource,
  onExportCSV,
}) => {
  const [period, setPeriod] = useState<'7' | '30' | 'all'>('7');

  const dayLimit = period === '7' ? 6 : period === '30' ? 29 : 1e9;
  const filteredRuns = runs.filter((r) => r.d <= dayLimit);

  const totalRuns = filteredRuns.length;
  const successfulRuns = filteredRuns.filter((r) => r.st === 'success').length;
  const successRate = totalRuns ? `${Math.round((successfulRuns / totalRuns) * 100)}%` : '—';

  const approvedReviews = inbox.filter((i) => i.kind === 'review' && i.st === 'approved').length;
  const pendingReviews = inbox.filter((i) => i.kind === 'review' && i.st === 'pending').length;
  const activeEmployees = agents.filter((a) => a.status === 'Active').length;

  const past7Days = [6, 5, 4, 3, 2, 1, 0];
  const dayCounts = past7Days.map((d) => runs.filter((r) => r.d === d).length);
  const maxDayCount = Math.max(1, ...dayCounts);

  return (
    <div className="flex flex-col gap-4 max-w-5xl">
      {/* Header */}
      <div className="flex items-end justify-between gap-3 flex-wrap">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-[var(--text)] m-0">Results</h1>
          <p className="text-xs text-[var(--muted)] m-0 mt-1">
            Saved measurements and where each number comes from.
          </p>
        </div>

        <div className="flex items-center gap-2 ml-auto flex-wrap">
          <select
            value={period}
            onChange={(e) => setPeriod(e.target.value as any)}
            className="w-36 py-1.5 text-xs rounded-xl"
            aria-label="Filter period"
          >
            <option value="7">Last 7 days</option>
            <option value="30">Last 30 days</option>
            <option value="all">All time</option>
          </select>
          <button onClick={() => onExportCSV(period)} className="os-btn os-btn-ghost os-btn-sm">
            <Download size={13} />
            Export CSV
          </button>
        </div>
      </div>

      {/* Metrics Cards Grid */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
        <div className="os-card p-4">
          <div className="text-xs text-[var(--muted)] flex items-center justify-between">
            <span>Pipeline runs</span>
            <TrendingUp size={14} className="text-[var(--acc)]" />
          </div>
          <div className="text-2xl font-bold text-[var(--text)] tracking-tight my-1.5 mono">
            {totalRuns}
          </div>
          <div className="text-[11px] text-[var(--dim)]">Source: pipeline run log</div>
        </div>

        <div className="os-card p-4">
          <div className="text-xs text-[var(--muted)] flex items-center justify-between">
            <span>Success rate</span>
            <CheckCircle size={14} className="text-[var(--green)]" />
          </div>
          <div className="text-2xl font-bold text-[var(--green)] tracking-tight my-1.5 mono">
            {successRate}
          </div>
          <div className="text-[11px] text-[var(--dim)]">Source: pipeline run log</div>
        </div>

        <div className="os-card p-4">
          <div className="text-xs text-[var(--muted)] flex items-center justify-between">
            <span>Reviews approved</span>
            <Clock size={14} className="text-[var(--amber)]" />
          </div>
          <div className="text-2xl font-bold text-[var(--text)] tracking-tight my-1.5 mono">
            {approvedReviews}
          </div>
          <div className="text-[11px] text-[var(--dim)]">
            Source: Inbox · {pendingReviews} still pending
          </div>
        </div>

        <div className="os-card p-4">
          <div className="text-xs text-[var(--muted)] flex items-center justify-between">
            <span>Token cost</span>
            <Cpu size={14} className="text-[var(--acc2)]" />
          </div>
          <div className="text-2xl font-bold text-[var(--text)] tracking-tight my-1.5 mono">
            $0.00
          </div>
          <div className="text-[11px] text-[var(--dim)]">Source: free models · 0 tokens</div>
        </div>

        <div className="os-card p-4">
          <div className="text-xs text-[var(--muted)]">Active employees</div>
          <div className="text-2xl font-bold text-[var(--text)] tracking-tight my-1.5 mono">
            {activeEmployees}
          </div>
          <div className="text-[11px] text-[var(--dim)]">Source: Company directory</div>
        </div>

        <div className="os-card p-4 border-dashed border-[var(--line2)] bg-[var(--card2)]/50">
          <div className="text-xs text-[var(--muted)]">Website sessions</div>
          <div className="text-sm font-semibold text-[var(--muted)] my-1.5 flex items-center gap-1.5">
            <AlertCircle size={14} className="text-[var(--dim)]" />
            Unavailable
          </div>
          <div className="text-[11px] text-[var(--dim)] mb-2">Evidence needed · no connected source</div>
          <button onClick={onConnectSource} className="os-btn os-btn-ghost os-btn-sm text-[11px]">
            Connect source
          </button>
        </div>

        <div className="os-card p-4 border-dashed border-[var(--line2)] bg-[var(--card2)]/50">
          <div className="text-xs text-[var(--muted)]">Monthly net profit</div>
          <div className="text-sm font-semibold text-[var(--muted)] my-1.5 flex items-center gap-1.5">
            <AlertCircle size={14} className="text-[var(--dim)]" />
            Unavailable
          </div>
          <div className="text-[11px] text-[var(--dim)] mb-2">Evidence needed · no verified source</div>
          <button onClick={onConnectSource} className="os-btn os-btn-ghost os-btn-sm text-[11px]">
            Connect source
          </button>
        </div>
      </div>

      {/* Runs Per Day Bar Chart */}
      <div className="os-card p-5">
        <b className="text-sm text-[var(--text)] block mb-4">Runs per day (Last 7 days)</b>
        <div className="flex items-end gap-3 h-32 pt-4 px-2">
          {past7Days.map((d, i) => {
            const count = dayCounts[i];
            const heightPct = Math.max(8, (count / maxDayCount) * 100);
            return (
              <div key={d} className="flex-1 flex flex-col items-center gap-2 h-full justify-end group">
                <span className="text-[10px] mono text-[var(--dim)] opacity-0 group-hover:opacity-100 transition-opacity">
                  {count}
                </span>
                <div
                  style={{ height: `${heightPct}%` }}
                  className="w-full rounded-t-lg bg-gradient-to-t from-[var(--acc)]/20 to-[var(--acc)] hover:brightness-125 transition-all relative cursor-pointer"
                  title={`${count} run(s) ${d === 0 ? 'Today' : `${d}d ago`}`}
                />
                <span className="text-[11px] mono text-[var(--muted)]">
                  {d === 0 ? 'Today' : `${d}d`}
                </span>
              </div>
            );
          })}
        </div>
      </div>

      {/* Runs Receipt Table */}
      <div className="os-card p-0 overflow-hidden">
        <div className="px-4 py-3 border-b border-[var(--line)] bg-[var(--card2)]/50 font-semibold text-xs text-[var(--muted)]">
          Execution Run Receipts
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs border-collapse">
            <thead>
              <tr className="border-b border-[var(--line)] text-[var(--muted)]">
                <th className="py-2.5 px-4 font-medium">When</th>
                <th className="py-2.5 px-4 font-medium">Pipeline</th>
                <th className="py-2.5 px-4 font-medium">Status</th>
                <th className="py-2.5 px-4 font-medium">Duration</th>
                <th className="py-2.5 px-4 font-medium">Tokens</th>
              </tr>
            </thead>
            <tbody>
              {filteredRuns.slice(0, 10).map((r) => (
                <tr
                  key={r.id}
                  className="border-b border-[var(--line)] hover:bg-white/[0.02] transition-colors"
                >
                  <td className="py-2.5 px-4 text-[var(--text)] mono">
                    {r.d === 0 ? 'Today' : `${r.d}d ago`}
                  </td>
                  <td className="py-2.5 px-4 text-[var(--text)] font-medium">{r.pipe}</td>
                  <td className="py-2.5 px-4">
                    <span
                      className={`os-tag ${
                        r.st === 'success' ? 'os-tag-green' : 'os-tag-red'
                      } text-[11px]`}
                    >
                      {r.st}
                    </span>
                  </td>
                  <td className="py-2.5 px-4 mono text-[var(--muted)]">{r.dur}s</td>
                  <td className="py-2.5 px-4 mono text-[var(--dim)]">{r.tok} tok</td>
                </tr>
              ))}
              {filteredRuns.length === 0 && (
                <tr>
                  <td colSpan={5} className="py-8 text-center text-[var(--muted)]">
                    No runs recorded in this period.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
};
