import React, { useEffect, useState } from 'react';
import {
  Building2,
  ChevronLeft,
  CircleHelp,
  History,
  MessageSquare,
  Network,
  PanelLeftClose,
  Search,
  Settings,
  SquarePen,
} from 'lucide-react';
import type { ActiveView } from '../../types';

export interface RecentConversation {
  id: string;
  title: string;
  updatedAt: number;
}

interface SidebarProps {
  activeView: ActiveView;
  onViewChange: (view: ActiveView) => void;
  conversations: RecentConversation[];
  activeConversationId: string;
  onNewConversation: () => void;
  onSelectConversation: (id: string) => void;
}

const NAV_ITEMS = [
  { id: 'company' as const, label: 'الشركة والعقود', icon: Building2 },
  { id: 'map' as const, label: 'مساحة العمل', icon: Network },
  { id: 'stream' as const, label: 'سجل الفريق', icon: History },
  { id: 'settings' as const, label: 'الإعدادات', icon: Settings },
];

export const Sidebar: React.FC<SidebarProps> = ({
  activeView,
  onViewChange,
  conversations,
  activeConversationId,
  onNewConversation,
  onSelectConversation,
}) => {
  const [collapsed, setCollapsed] = useState(() => window.matchMedia('(max-width: 767px)').matches);
  const [searchOpen, setSearchOpen] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');

  useEffect(() => {
    const syncSidebarWidth = () => setCollapsed(window.matchMedia('(max-width: 767px)').matches);
    window.addEventListener('resize', syncSidebarWidth);
    return () => window.removeEventListener('resize', syncSidebarWidth);
  }, []);

  return (
    <aside
      className={`relative z-30 flex h-full shrink-0 flex-col border-r border-gray-800 bg-gray-950 transition-[width] duration-200 ${collapsed ? 'w-[68px]' : 'w-[260px]'}`}
      aria-label="القائمة الرئيسية"
      dir="rtl"
    >
      <div className="flex h-[60px] items-center justify-between px-3">
        {!collapsed && (
          <button type="button" onClick={() => onViewChange('chat')} data-nav="chat" className="flex items-center gap-2.5 rounded-lg px-2 py-1.5 text-gray-100 hover:bg-gray-800" aria-label="AgentOS - الرئيسية">
            <span className="grid h-7 w-7 place-items-center rounded-full border border-gray-600 text-sm font-semibold text-gray-100">A</span>
            <span className="text-[15px] font-semibold tracking-tight">AgentOS</span>
          </button>
        )}
        <button type="button" onClick={() => setCollapsed((value) => !value)} className="rounded-lg p-2 text-gray-400 transition hover:bg-gray-800 hover:text-gray-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-400" aria-label={collapsed ? 'توسيع القائمة' : 'طي القائمة'} title={collapsed ? 'توسيع القائمة' : 'طي القائمة'}>
          {collapsed ? <ChevronLeft className="h-4 w-4" /> : <PanelLeftClose className="h-4 w-4" />}
        </button>
      </div>

      <div className="space-y-1 px-2 pb-3">
        <button type="button" onClick={onNewConversation} title="محادثة جديدة" className="flex w-full items-center gap-3 rounded-xl bg-gray-800 px-3 py-2.5 text-sm text-gray-100 transition hover:bg-gray-700 focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-400">
          <SquarePen className="h-[18px] w-[18px] shrink-0" />
          {!collapsed && <span>محادثة جديدة</span>}
        </button>
        {NAV_ITEMS.map(({ id, label, icon: Icon }) => (
          <button key={id} type="button" onClick={() => onViewChange(id)} title={label} data-nav={id} className={`flex w-full items-center gap-3 rounded-xl px-3 py-2.5 text-sm transition focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-400 ${activeView === id ? 'bg-gray-800 text-gray-100' : 'text-gray-300 hover:bg-gray-800/70 hover:text-gray-100'}`}>
            <Icon className="h-[18px] w-[18px] shrink-0" />
            {!collapsed && <span>{label}</span>}
          </button>
        ))}
      </div>

      {!collapsed && (
        <>
          <div className="mx-4 border-t border-gray-800" />
          <div className="flex items-center justify-between px-4 pb-2 pt-4 text-xs text-gray-400">
            <span>المحادثات الأخيرة</span>
            <button type="button" onClick={() => setSearchOpen((value) => !value)} aria-label="بحث في المحادثات" className="rounded-md p-1 hover:bg-gray-800 hover:text-gray-100"><Search className="h-4 w-4" /></button>
          </div>
          {searchOpen && <input autoFocus value={searchQuery} onChange={(event) => setSearchQuery(event.target.value)} type="search" placeholder="ابحث في المحادثات" className="mx-3 mb-2 rounded-lg border border-gray-700 bg-gray-900 px-3 py-2 text-sm text-gray-100 outline-none placeholder:text-gray-500 focus:border-blue-400" />}
        </>
      )}

      <div className="min-h-0 flex-1 overflow-y-auto px-2 pb-3" aria-label="المحادثات الأخيرة">
        {conversations.filter((conversation) => conversation.title.toLocaleLowerCase().includes(searchQuery.toLocaleLowerCase())).slice(0, 40).map((conversation) => (
          <button key={conversation.id} type="button" onClick={() => { onSelectConversation(conversation.id); onViewChange('chat'); }} title={conversation.title} className={`mb-0.5 flex w-full items-center gap-3 truncate rounded-lg px-3 py-2 text-right text-[13px] transition focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-400 ${conversation.id === activeConversationId && activeView === 'chat' ? 'bg-gray-800 text-gray-100' : 'text-gray-400 hover:bg-gray-800/70 hover:text-gray-100'}`}>
            <MessageSquare className="h-4 w-4 shrink-0" />
            {!collapsed && <span className="truncate">{conversation.title}</span>}
          </button>
        ))}
      </div>

      <div className="border-t border-gray-800 p-2">
        <button type="button" disabled aria-disabled="true" title="قريباً" className="flex w-full items-center gap-3 rounded-lg px-3 py-2 text-sm text-gray-400 opacity-60 cursor-not-allowed">
          <CircleHelp className="h-[18px] w-[18px] shrink-0" />{!collapsed && <span>المساعدة <span className="text-xs opacity-70">(قريباً)</span></span>}
        </button>
        <button type="button" disabled aria-disabled="true" title="قريباً" className="mt-1 flex w-full items-center gap-3 rounded-lg px-2 py-2 text-sm text-gray-300 opacity-60 cursor-not-allowed">
          <span className="grid h-7 w-7 shrink-0 place-items-center rounded-full bg-gray-700 text-xs">م</span>{!collapsed && <span>مساحة العمل <span className="text-xs opacity-70">(قريباً)</span></span>}
        </button>
      </div>
    </aside>
  );
};
