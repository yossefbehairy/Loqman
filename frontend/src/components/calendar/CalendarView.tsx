import React, { useState } from 'react';
import type { ScheduleItem, Pipeline } from '../../types/agentos';
import { Calendar as CalendarIcon, Clock, Repeat, Plus, Trash2 } from 'lucide-react';

interface Props {
  schedules: ScheduleItem[];
  pipelines: Pipeline[];
  onAddSchedule: (pipelineId: string, time: string, repeat: 'Daily' | 'Weekdays' | 'Weekly') => void;
  onToggleSchedule: (id: string, active: boolean) => void;
  onDeleteSchedule: (id: string) => void;
}

export const CalendarView: React.FC<Props> = ({
  schedules,
  pipelines,
  onAddSchedule,
  onToggleSchedule,
  onDeleteSchedule,
}) => {
  const [selectedPipe, setSelectedPipe] = useState(pipelines[0]?.id || '');
  const [timeVal, setTimeVal] = useState('09:00');
  const [repeatVal, setRepeatVal] = useState<'Daily' | 'Weekdays' | 'Weekly'>('Daily');

  const handleAdd = () => {
    if (!selectedPipe) return;
    onAddSchedule(selectedPipe, timeVal || '09:00', repeatVal);
  };

  return (
    <div className="flex flex-col gap-4 max-w-4xl">
      <div>
        <h1 className="text-2xl font-bold tracking-tight text-[var(--text)] m-0">Calendar</h1>
        <p className="text-xs text-[var(--muted)] m-0 mt-1">
          Scheduled pipeline runs. Runs execute automatically when the workspace engine is active.
        </p>
      </div>

      {/* Add Schedule Form */}
      <div className="os-card p-4">
        <div className="text-xs font-semibold text-[var(--muted)] uppercase tracking-wider mb-3">
          Schedule New Pipeline Run
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-[2fr_1fr_1fr_auto] gap-3 items-end">
          <div>
            <label className="block text-xs text-[var(--muted)] mb-1">Pipeline</label>
            <select
              value={selectedPipe}
              onChange={(e) => setSelectedPipe(e.target.value)}
              className="text-xs py-2 rounded-xl"
            >
              {pipelines.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
              {pipelines.length === 0 && <option value="">No pipelines available</option>}
            </select>
          </div>

          <div>
            <label className="block text-xs text-[var(--muted)] mb-1">Time</label>
            <input
              type="time"
              value={timeVal}
              onChange={(e) => setTimeVal(e.target.value)}
              className="text-xs py-2 rounded-xl mono"
            />
          </div>

          <div>
            <label className="block text-xs text-[var(--muted)] mb-1">Repeat</label>
            <select
              value={repeatVal}
              onChange={(e) => setRepeatVal(e.target.value as any)}
              className="text-xs py-2 rounded-xl"
            >
              <option value="Daily">Daily</option>
              <option value="Weekdays">Weekdays</option>
              <option value="Weekly">Weekly</option>
            </select>
          </div>

          <button
            onClick={handleAdd}
            disabled={!selectedPipe}
            className="os-btn h-10 px-4"
          >
            <Plus size={14} />
            <span>Add schedule</span>
          </button>
        </div>
      </div>

      {/* Schedules List */}
      <div className="os-card p-0 overflow-hidden divide-y divide-[var(--line)]">
        {schedules.map((s) => {
          const pipe = pipelines.find((p) => p.id === s.pipe);
          return (
            <div key={s.id} className="flex items-center justify-between p-4 hover:bg-white/[0.02] transition-colors">
              <div className="flex items-center gap-3">
                <div className="w-8 h-8 rounded-xl bg-[var(--card2)] border border-[var(--line)] flex items-center justify-center text-[var(--acc)]">
                  <Clock size={16} />
                </div>
                <div>
                  <b className="text-sm text-[var(--text)] block">{pipe ? pipe.name : '(Deleted pipeline)'}</b>
                  <div className="text-xs text-[var(--muted)] flex items-center gap-1.5 mt-0.5">
                    <Repeat size={12} className="text-[var(--dim)]" />
                    <span>{s.rep}</span>
                    <span>·</span>
                    <span className="mono text-[var(--text)]">{s.time}</span>
                  </div>
                </div>
              </div>

              <div className="flex items-center gap-4">
                <label className="os-switch" title={s.on ? 'Schedule enabled' : 'Schedule paused'}>
                  <input
                    type="checkbox"
                    checked={s.on}
                    onChange={(e) => onToggleSchedule(s.id, e.target.checked)}
                  />
                  <span className="slider" />
                </label>

                <button
                  onClick={() => onDeleteSchedule(s.id)}
                  className="os-btn os-btn-ghost os-btn-sm text-[var(--red)] border-[var(--red)]/20 hover:bg-[var(--red)]/10"
                  title="Delete schedule"
                >
                  <Trash2 size={13} />
                </button>
              </div>
            </div>
          );
        })}

        {schedules.length === 0 && (
          <div className="p-12 text-center text-xs text-[var(--muted)]">
            <CalendarIcon size={28} className="mx-auto mb-2 text-[var(--dim)]" />
            No scheduled runs yet. Create one above to run pipelines on a schedule.
          </div>
        )}
      </div>
    </div>
  );
};
