import React from 'react';
import { Compass, ShieldAlert, CheckCircle2, Crown, Users } from 'lucide-react';
import type { DepartmentId } from '../../types';

export interface DepartmentZoneProps {
  id: DepartmentId;
  name: string;
  nameArabic: string;
  managerArabic: string;
  managerEnglish: string;
  activeAgentsCount?: number;
  colorScheme: 'indigo' | 'amber' | 'emerald';
}

const COLOR_MAP = {
  indigo: {
    border: 'border-indigo-500/40 hover:border-indigo-400',
    bg: 'bg-indigo-950/40',
    badge: 'bg-indigo-500/20 text-indigo-300 border-indigo-500/40',
    text: 'text-indigo-400',
    halo: 'shadow-indigo-500/10',
    icon: Compass,
  },
  amber: {
    border: 'border-amber-500/40 hover:border-amber-400',
    bg: 'bg-amber-950/40',
    badge: 'bg-amber-500/20 text-amber-300 border-amber-500/40',
    text: 'text-amber-400',
    halo: 'shadow-amber-500/10',
    icon: ShieldAlert,
  },
  emerald: {
    border: 'border-emerald-500/40 hover:border-emerald-400',
    bg: 'bg-emerald-950/40',
    badge: 'bg-emerald-500/20 text-emerald-300 border-emerald-500/40',
    text: 'text-emerald-400',
    halo: 'shadow-emerald-500/10',
    icon: CheckCircle2,
  },
};

export const DepartmentZoneCard: React.FC<DepartmentZoneProps> = ({
  id,
  name,
  nameArabic,
  managerArabic,
  managerEnglish,
  activeAgentsCount = 1,
  colorScheme,
}) => {
  const theme = COLOR_MAP[colorScheme];
  const Icon = theme.icon;

  return (
    <div
      data-zone={id}
      data-department={id}
      className={`flex-1 min-w-[200px] rounded-xl border backdrop-blur-md p-3 shadow-lg transition-all ${theme.border} ${theme.bg} ${theme.halo}`}
    >
      <div className="flex items-center justify-between mb-1.5">
        <div className="flex items-center space-x-2">
          <div className={`p-1.5 rounded-lg border ${theme.badge}`}>
            <Icon className="w-3.5 h-3.5" />
          </div>
          <div>
            <h4 className="text-xs font-bold text-gray-100 flex items-center gap-1.5">
              <span>{name} Zone</span>
              <span className="text-[10px] text-gray-400 font-arabic">({nameArabic})</span>
            </h4>
          </div>
        </div>
        <div className="flex items-center gap-1 text-[10px] font-mono text-gray-400 bg-gray-900/80 px-2 py-0.5 rounded-full border border-gray-800">
          <Users className="w-2.5 h-2.5" />
          <span>{activeAgentsCount} Active</span>
        </div>
      </div>

      <div className="flex items-center justify-between pt-1 border-t border-gray-800/60 text-[11px]">
        <div className="flex items-center gap-1.5 text-gray-300">
          <span className="text-[10px] text-amber-400 flex items-center gap-0.5 font-medium">
            <Crown className="w-2.5 h-2.5" /> Manager:
          </span>
          <span className="font-arabic font-semibold text-white">{managerArabic}</span>
          <span className="text-[10px] text-gray-500">({managerEnglish})</span>
        </div>
        <span className={`text-[10px] uppercase font-bold tracking-wider ${theme.text}`}>
          {id.toUpperCase()}
        </span>
      </div>
    </div>
  );
};
