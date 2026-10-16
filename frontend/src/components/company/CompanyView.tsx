import React, { useState, useRef, useEffect } from 'react';
import type { Agent, Team, CameraState, GraphOptions, RunReceipt } from '../../types/agentos';
import { CompanyGraphCanvas } from './CompanyGraphCanvas';
import { CompanyOrgChart } from './CompanyOrgChart';
import { AgentDetailPanel } from './AgentDetailPanel';
import { Search, Settings2, Plus, HelpCircle, Info, Maximize2, RotateCcw } from 'lucide-react';

interface Props {
  agents: Agent[];
  teams: Team[];
  runs: RunReceipt[];
  viewMode: 'graph' | 'org';
  onViewModeChange: (mode: 'graph' | 'org') => void;
  selectedAgentId: string | null;
  onSelectAgent: (id: string | null) => void;
  focusTeamId: string | null;
  onFocusTeam: (teamId: string) => void;
  graphOptions: GraphOptions;
  onGraphOptionsChange: (opts: GraphOptions) => void;
  camera: CameraState;
  onCameraChange: (cam: CameraState) => void;
  armedDeleteTeamId: string | null;
  onOpenConversation: (agentId: string) => void;
  onEditContract: (agentId: string) => void;
  onAddSpecialist: (teamId: string) => void;
  onDeleteTeam: (teamId: string) => void;
  onNewTeam: () => void;
  onOpenHelp: () => void;
  onOpenSourceDetails: () => void;
  onToggleStream: () => void;
  onInspectAgentTask?: (agentId: string) => void;
}

export const CompanyView: React.FC<Props> = ({
  agents,
  teams,
  runs,
  viewMode,
  onViewModeChange,
  selectedAgentId,
  onSelectAgent,
  focusTeamId,
  onFocusTeam,
  graphOptions,
  onGraphOptionsChange,
  camera,
  onCameraChange,
  armedDeleteTeamId,
  onOpenConversation,
  onEditContract,
  onAddSpecialist,
  onDeleteTeam,
  onNewTeam,
  onOpenHelp,
  onOpenSourceDetails,
  onToggleStream,
  onInspectAgentTask,
}) => {
  const [searchQuery, setSearchQuery] = useState('');
  const [isSearchOpen, setIsSearchOpen] = useState(false);
  const [isOptionsOpen, setIsOptionsOpen] = useState(false);
  const searchDropdownRef = useRef<HTMLDivElement | null>(null);
  const optionsPopoverRef = useRef<HTMLDivElement | null>(null);

  const specialistsCount = agents.filter((a) => a.team && !a.mgr).length;
  const managersCount = teams.length;
  const sourceLabel = 'LIVE · your workspace';

  // Filtered agents for search dropdown
  const searchResults = searchQuery.trim()
    ? agents
        .filter((a) => {
          const team = teams.find((t) => t.id === a.team);
          const full = `${a.name} ${a.role} ${team?.name || ''}`.toLowerCase();
          return full.includes(searchQuery.trim().toLowerCase());
        })
        .slice(0, 7)
    : [];

  // Close popovers on click outside
  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (optionsPopoverRef.current && !optionsPopoverRef.current.contains(e.target as Node)) {
        setIsOptionsOpen(false);
      }
      if (searchDropdownRef.current && !searchDropdownRef.current.contains(e.target as Node)) {
        setIsSearchOpen(false);
      }
    };
    const handleEscape = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setIsSearchOpen(false);
    };
    document.addEventListener('mousedown', handleClickOutside);
    document.addEventListener('keydown', handleEscape);
    return () => {
      document.removeEventListener('mousedown', handleClickOutside);
      document.removeEventListener('keydown', handleEscape);
    };
  }, []);

  const selectedAgent = agents.find((a) => a.id === selectedAgentId) || null;

  return (
    <div className="flex flex-col gap-3 min-w-0">
      {/* Company Header */}
      <div className="flex items-end justify-between gap-3 flex-wrap">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-[var(--text)] m-0">Company</h1>
          <p className="text-xs text-[var(--muted)] m-0 mt-1">
            {specialistsCount} specialists · 1 CEO · {managersCount} managers · {managersCount} teams
          </p>
        </div>

        <div className="flex items-center gap-3 ml-auto flex-wrap">
          {/* Segmented View Switcher */}
          <div className="flex items-center p-0.5 bg-[var(--card2)] border border-[var(--line2)] rounded-full">
            <button
              onClick={() => onViewModeChange('graph')}
              className={`px-3 py-1 rounded-full text-xs font-medium transition-all ${
                viewMode === 'graph'
                  ? 'bg-white/10 text-[var(--text)] shadow-sm'
                  : 'text-[var(--muted)] hover:text-[var(--text)]'
              }`}
            >
              Graph
            </button>
            <button
              onClick={() => onViewModeChange('org')}
              className={`px-3 py-1 rounded-full text-xs font-medium transition-all ${
                viewMode === 'org'
                  ? 'bg-white/10 text-[var(--text)] shadow-sm'
                  : 'text-[var(--muted)] hover:text-[var(--text)]'
              }`}
            >
              Org chart
            </button>
          </div>

          <span className="mono text-[11px] text-[var(--dim)] hidden sm:inline">
            {sourceLabel} · {runs.length} run receipts
          </span>
        </div>
      </div>

      {/* Subheader: Search & Quick Guide */}
      <div className="flex items-center gap-3 flex-wrap">
        <div className="relative w-80 max-w-full" ref={searchDropdownRef}>
          <div className="relative flex items-center">
            <Search size={14} className="absolute left-3 text-[var(--dim)] pointer-events-none" />
            <input
              id="cq"
              type="text"
              placeholder="Find a person, role or desk"
              value={searchQuery}
              onChange={(e) => {
                setSearchQuery(e.target.value);
                setIsSearchOpen(true);
              }}
              onFocus={() => setIsSearchOpen(true)}
              onKeyDown={(e) => {
                if (e.key === 'Escape') setIsSearchOpen(false);
                if (e.key === 'Enter' && searchResults.length > 0) {
                  onSelectAgent(searchResults[0].id);
                  setSearchQuery('');
                  setIsSearchOpen(false);
                }
              }}
              className="pl-8 pr-3 py-1.5 text-xs rounded-xl"
              autoComplete="off"
              aria-label="Find a person"
              aria-expanded={isSearchOpen && searchQuery.trim().length > 0}
              role="combobox"
            />
          </div>

          {isSearchOpen && searchQuery.trim() && (
            <div
              className="absolute top-full left-0 right-0 mt-1.5 bg-[var(--card2)] border border-[var(--line2)] rounded-xl p-1.5 z-30 shadow-2xl"
              role="listbox"
            >
              {searchResults.length > 0 ? (
                searchResults.map((a) => (
                  <button
                    key={a.id}
                    role="option"
                    aria-selected={selectedAgentId === a.id}
                    onClick={() => {
                      onSelectAgent(a.id);
                      setSearchQuery('');
                      setIsSearchOpen(false);
                    }}
                    className="w-full flex items-center justify-between p-2 rounded-lg text-left hover:bg-[var(--acc)]/10 text-xs transition-colors"
                  >
                    <span className="font-medium text-[var(--text)]">{a.name}</span>
                    <span className="text-[11px] text-[var(--muted)] truncate max-w-[140px]">{a.role}</span>
                  </button>
                ))
              ) : (
                <div className="p-2 text-xs text-[var(--muted)] text-center">No matches found</div>
              )}
            </div>
          )}
        </div>

        <span className="text-xs text-[var(--muted)] hidden md:inline">
          Select anyone for summary. Focus a desk to bring it closer.
        </span>
      </div>

      {/* Team Focus Chips & Options Menu */}
      <div className="flex items-center gap-2 flex-wrap">
        {teams.map((t) => {
          const count = agents.filter((a) => a.team === t.id).length;
          const isFocused = focusTeamId === t.id;
          return (
            <button
              key={t.id}
              onClick={() => onFocusTeam(t.id)}
              className={`inline-flex items-center gap-2 px-3 py-1.5 rounded-full border text-xs font-medium transition-all ${
                isFocused
                  ? 'border-[var(--acc)] bg-[var(--acc)]/10 text-[var(--text)] shadow-sm'
                  : 'border-[var(--line2)] bg-[var(--card)] text-[var(--muted)] hover:text-[var(--text)] hover:border-[var(--line-strong)]'
              }`}
            >
              <span className="w-2 h-2 rounded-full" style={{ background: t.color }} />
              <span>{t.name}</span>
              <b className="mono font-semibold text-[11px] text-[var(--text)]">{count}</b>
            </button>
          );
        })}

        <button onClick={onNewTeam} className="os-btn os-btn-ghost os-btn-sm rounded-full">
          <Plus size={13} />
          New team
        </button>

        {/* Options Popover */}
        <div className="relative ml-auto" ref={optionsPopoverRef}>
          <button
            onClick={() => setIsOptionsOpen((prev) => !prev)}
            className="os-btn os-btn-ghost os-btn-sm rounded-full"
            aria-expanded={isOptionsOpen}
          >
            <Settings2 size={13} />
            Options
          </button>

          {isOptionsOpen && (
            <div className="absolute right-0 top-full mt-2 w-72 p-3 bg-[var(--card)] border border-[var(--line2)] rounded-2xl shadow-2xl z-40 backdrop-blur-xl text-xs flex flex-col gap-2.5">
              <div className="text-[10px] font-bold uppercase tracking-wider text-[var(--muted)]">Graph</div>
              <div className="flex items-center justify-between py-1">
                <span className="text-[var(--text)]">Evidence trails</span>
                <label className="os-switch">
                  <input
                    type="checkbox"
                    checked={graphOptions.trails}
                    onChange={(e) => onGraphOptionsChange({ ...graphOptions, trails: e.target.checked })}
                  />
                  <span className="slider" />
                </label>
              </div>

              <div className="flex items-center justify-between py-1">
                <span className="text-[var(--text)]">Motion</span>
                <label className="os-switch">
                  <input
                    type="checkbox"
                    checked={graphOptions.motion}
                    onChange={(e) => onGraphOptionsChange({ ...graphOptions, motion: e.target.checked })}
                  />
                  <span className="slider" />
                </label>
              </div>

              <button
                onClick={() => {
                  onCameraChange({ x: 0, y: 0, k: 0.95 });
                  setIsOptionsOpen(false);
                }}
                className="flex items-center justify-between w-full p-2 rounded-lg hover:bg-white/5 text-[var(--text)] text-left"
              >
                <span>Reset camera</span>
                <RotateCcw size={13} className="text-[var(--dim)]" />
              </button>

              <div className="border-t border-[var(--line)] pt-2 text-[10px] font-bold uppercase tracking-wider text-[var(--muted)]">
                Presentation & Help
              </div>

              <button
                onClick={() => {
                  onToggleStream();
                  setIsOptionsOpen(false);
                }}
                className="flex items-center justify-between w-full p-2 rounded-lg hover:bg-white/5 text-[var(--text)] text-left"
              >
                <span className="flex items-center gap-1.5">
                  <Maximize2 size={13} />
                  Stream view
                </span>
                <kbd>F</kbd>
              </button>

              <button
                onClick={() => {
                  onOpenHelp();
                  setIsOptionsOpen(false);
                }}
                className="flex items-center justify-between w-full p-2 rounded-lg hover:bg-white/5 text-[var(--text)] text-left"
              >
                <span className="flex items-center gap-1.5">
                  <HelpCircle size={13} />
                  How to read graph
                </span>
                <kbd>H</kbd>
              </button>

              <button
                onClick={() => {
                  onOpenSourceDetails();
                  setIsOptionsOpen(false);
                }}
                className="flex items-center justify-between w-full p-2 rounded-lg hover:bg-white/5 text-[var(--text)] text-left"
              >
                <span className="flex items-center gap-1.5">
                  <Info size={13} />
                  Source details
                </span>
              </button>
            </div>
          )}
        </div>
      </div>

      {/* Main View Area */}
      {viewMode === 'graph' ? (
        <div className="relative h-[calc(100vh-270px)] min-h-[480px] border border-[var(--line)] rounded-2xl overflow-hidden bg-radial-stage shadow-2xl">
          <CompanyGraphCanvas
            agents={agents}
            teams={teams}
            selectedId={selectedAgentId}
            focusTeamId={focusTeamId}
            opts={graphOptions}
            camera={camera}
            onCameraChange={onCameraChange}
            onSelectAgent={onSelectAgent}
            onToggleStream={onToggleStream}
          />

          {/* Floating Detail Panel (Right) */}
          <div className="absolute right-3.5 top-3.5 w-[19rem] sm:w-80 max-h-[calc(100%-28px)] overflow-y-auto os-card p-4 z-20 shadow-2xl backdrop-blur-xl">
            <AgentDetailPanel
              agent={selectedAgent}
              agents={agents}
              teams={teams}
              armedDeleteTeamId={armedDeleteTeamId}
              onClose={() => onSelectAgent(null)}
              onSelectAgent={onSelectAgent}
              onOpenConversation={onOpenConversation}
              onEditContract={onEditContract}
              onAddSpecialist={onAddSpecialist}
              onDeleteTeam={onDeleteTeam}
              onInspectAgentTask={onInspectAgentTask || (() => {})}
            />
          </div>
        </div>
      ) : (
        <CompanyOrgChart
          agents={agents}
          teams={teams}
          selectedId={selectedAgentId}
          focusTeamId={focusTeamId}
          armedDeleteTeamId={armedDeleteTeamId}
          onSelectAgent={onSelectAgent}
          onFocusTeam={onFocusTeam}
          onOpenConversation={onOpenConversation}
          onEditContract={onEditContract}
          onAddSpecialist={onAddSpecialist}
          onDeleteTeam={onDeleteTeam}
          onInspectAgentTask={onInspectAgentTask || (() => {})}
        />
      )}
    </div>
  );
};
