/*
 * Frame composition follows RuseBot 0.1.127 src/App.tsx (Apache-2.0, Copyright
 * 2026 Milind Soni and OpenMausBot contributors): sidebar + main column, the
 * hamburger/drawer behaviour below the md breakpoint and the collapse to an
 * icon rail when a side panel needs the room. State comes from the AgentOS
 * worker instead of the RuseBot store.
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import { Menu } from 'lucide-react';
import { Sidebar, type View } from './components/Sidebar';
import { ChatView } from './components/ChatView';
import { TeamView } from './components/TeamView';
import { ResultsView } from './components/ResultsView';
import { SettingsModal } from './components/SettingsModal';
import { NewAgentDialog } from './components/NewAgentDialog';
import { useChat, useEngines, messageOf } from './state/useChat';
import { agentOf, useCompany } from './state/useCompany';
import {
  approveCeoAction,
  denyCeoAction,
  listCeoActions,
  listMissions,
  listProfiles,
  type CeoAction,
  type Mission,
  type Profile,
} from './data/worker';
import { useMediaQuery, SIDEBAR_AND_PANEL_FIT } from './lib/use-media-query';
import type { Agent } from '../types/agentos';

const KEY_NAME = 'agentos.rusebot.userName.v1';
const KEY_RAIL = 'agentos.rusebot.rail.v1';

function readText(key: string, fallback: string): string {
  try {
    return localStorage.getItem(key) ?? fallback;
  } catch {
    return fallback;
  }
}

export function Shell() {
  const engines = useEngines();
  const company = useCompany();
  const reloadAgents = company.reload;
  const chat = useChat(engines.choice, company.agents, company.reload);
  const [view, setView] = useState<View>('chat');
  const [drawer, setDrawer] = useState(false);
  const [rail, setRail] = useState(() => readText(KEY_RAIL, '0') === '1');
  const [panelOpen, setPanelOpen] = useState(false);
  const [settings, setSettings] = useState(false);
  const [newAgent, setNewAgent] = useState(false);
  const [userName, setUserName] = useState(() => readText(KEY_NAME, 'You'));
  const [profiles, setProfiles] = useState<Profile[]>([]);
  const [missions, setMissions] = useState<Mission[]>([]);
  const [ceoActions, setCeoActions] = useState<CeoAction[]>([]);
  const [notice, setNotice] = useState<string | null>(null);
  const fits = useMediaQuery(SIDEBAR_AND_PANEL_FIT, true);

  const refreshCompany = useCallback(async () => {
    const [p, m, c] = await Promise.all([
      listProfiles().catch(() => [] as Profile[]),
      listMissions().catch(() => [] as Mission[]),
      listCeoActions().catch(() => [] as CeoAction[]),
    ]);
    setProfiles(p);
    setMissions(m);
    setCeoActions(c);
  }, []);

  useEffect(() => {
    void refreshCompany();
    void reloadAgents();
  }, [refreshCompany, reloadAgents, view]);

  const collapsed = rail || (panelOpen && !fits);

  const conversation = chat.conversations.find((c) => c.id === chat.selectedId) ?? null;
  const legacyThread = chat.selectedId?.startsWith('legacy:')
    ? (chat.legacy.find((t) => `legacy:${t.id}` === chat.selectedId) ?? null)
    : null;

  const previews = useMemo(() => {
    const out: Record<string, string> = {};
    const last = chat.messages[chat.messages.length - 1];
    if (chat.selectedId && last) out[chat.selectedId] = last.content.replace(/\s+/g, ' ').slice(0, 80);
    return out;
  }, [chat.messages, chat.selectedId]);

  const goView = (next: View) => {
    setView(next);
    setDrawer(false);
    if (next !== 'team' && next !== 'contracts') setPanelOpen(false);
  };

  const openChatWith = async (agent: Agent) => {
    try {
      await chat.openAgentChat(agent);
      goView('chat');
    } catch (e) {
      setNotice(messageOf(e));
    }
  };

  const decideCeo = async (action: CeoAction, decision: 'approve' | 'deny') => {
    if (decision === 'approve') await approveCeoAction(action.id);
    else await denyCeoAction(action.id);
    await refreshCompany();
  };

  const needsYou = chat.approvals.length + ceoActions.length;
  const { loadAllDrafts } = chat;
  useEffect(() => {
    if (view === 'results') void loadAllDrafts();
  }, [view, loadAllDrafts]);

  return (
    <div className="flex h-full flex-col" data-rusebot-shell>
      <div className="relative flex min-h-0 flex-1">
        <button
          type="button"
          aria-label="Open chat list"
          aria-expanded={drawer}
          onClick={() => setDrawer((o) => !o)}
          className="absolute left-3 top-3 z-30 rounded-md p-1.5 text-ink-secondary hover:bg-raised hover:text-ink md:hidden"
        >
          <Menu size={18} />
        </button>
        {drawer && <div aria-hidden="true" className="absolute inset-0 z-30 bg-black/30 md:hidden" onClick={() => setDrawer(false)} />}
        <Sidebar
          open={drawer}
          onClose={() => setDrawer(false)}
          rail={collapsed}
          onToggleRail={() => {
            const next = !rail;
            setRail(next);
            try {
              localStorage.setItem(KEY_RAIL, next ? '1' : '0');
            } catch {
              /* storage unavailable */
            }
          }}
          view={view}
          onView={goView}
          conversations={chat.conversations}
          legacy={chat.legacy}
          selectedId={chat.selectedId}
          onSelect={(id) => {
            chat.select(id);
            goView('chat');
          }}
          onNewChat={() => {
            chat.newChat();
            goView('chat');
          }}
          onNewAgent={() => setNewAgent(true)}
          onSettings={() => setSettings(true)}
          activeRunIds={chat.activeRunIds}
          needsYou={needsYou}
          onNeedsYou={() => goView('results')}
          userName={userName}
          previews={previews}
        />
        {view === 'chat' && (
          <ChatView
            chat={chat}
            engines={engines}
            conversation={conversation}
            legacyThread={legacyThread}
            agent={chat.agentFor(chat.selectedId)}
            onOpenProfile={() => goView('team')}
            onNewAgent={() => setNewAgent(true)}
          />
        )}
        {(view === 'team' || view === 'contracts') && (
          <TeamView
            mode={view}
            teams={company.teams}
            agents={company.agents}
            profiles={profiles}
            missions={missions}
            defaultModel={engines.choice?.model ?? ''}
            onOpenChat={(a) => void openChatWith(a)}
            onChanged={() => {
              void refreshCompany();
              void company.reload();
            }}
            onNewAgent={() => setNewAgent(true)}
            onPanelChange={setPanelOpen}
          />
        )}
        {view === 'results' && <ResultsView chat={chat} ceoActions={ceoActions} onCeoDecide={decideCeo} />}
        {notice && (
          <div role="alert" className="absolute bottom-4 left-1/2 z-50 -translate-x-1/2 rounded-lg border border-danger/30 bg-danger/10 px-3 py-2 text-[13px] text-danger">
            {notice}{' '}
            <button type="button" className="underline" onClick={() => setNotice(null)}>
              Dismiss
            </button>
          </div>
        )}
      </div>
      {newAgent && (
        <NewAgentDialog
          defaultModel={engines.choice?.model ?? ''}
          engine={engines.choice?.engine ?? null}
          onClose={() => setNewAgent(false)}
          onCreated={async (profile) => {
            setNewAgent(false);
            await company.reload();
            await chat.openAgentChat(agentOf(profile)).catch(() => undefined);
            goView('chat');
          }}
        />
      )}
      {settings && (
        <SettingsModal
          onClose={() => setSettings(false)}
          userName={userName}
          onUserName={(name) => {
            setUserName(name);
            try {
              localStorage.setItem(KEY_NAME, name);
            } catch {
              /* storage unavailable */
            }
          }}
        />
      )}
    </div>
  );
}
