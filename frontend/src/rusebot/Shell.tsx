/*
 * Frame composition follows RuseBot 0.1.127 src/App.tsx (Apache-2.0, Copyright
 * 2026 Milind Soni and OpenMausBot contributors): sidebar + main column, the
 * hamburger/drawer behaviour below the md breakpoint and the collapse to an
 * icon rail when a side panel needs the room. State comes from the Loqman
 * worker instead of the RuseBot store.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Menu } from 'lucide-react';
import { Sidebar, type View } from './components/Sidebar';
import { ChatView } from './components/ChatView';
import { TeamView } from './components/TeamView';
import { ResultsView } from './components/ResultsView';
import { SettingsModal, type PageId } from './components/SettingsModal';
import { NewAgentDialog } from './components/NewAgentDialog';
import { useChat, messageOf } from './state/useChat';
import { useCompany } from './state/useCompany';
import {
  approveCeoAction,
  ceoActionOutcome,
  denyCeoAction,
  listCeoActions,
  listMissions,
  type CeoAction,
  type Mission,
} from './data/worker';
import { useMediaQuery, SIDEBAR_AND_PANEL_FIT, SIDEBAR_INLINE } from './lib/use-media-query';
import type { Agent } from './types';
import type { ApiError } from '../api/transport';
import type { DecideResult } from './components/ApprovalCard';
import { useProfilePhoto } from './state/useProfilePhoto';

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
  const profilePhoto = useProfilePhoto();
  const company = useCompany();
  const reloadAgents = company.reload;
  const [view, setView] = useState<View>('chat');
  const [drawer, setDrawer] = useState(false);
  const drawerToggle = useRef<HTMLButtonElement>(null);
  const sidebarInline = useMediaQuery(SIDEBAR_INLINE, true);
  const [rail, setRail] = useState(() => readText(KEY_RAIL, '0') === '1');
  const [panelOpen, setPanelOpen] = useState(false);
  const [settings, setSettings] = useState<PageId | null>(null);
  const [newAgent, setNewAgent] = useState(false);
  const [userName, setUserName] = useState(() => readText(KEY_NAME, 'You'));
  const [missions, setMissions] = useState<Mission[]>([]);
  const [ceoActions, setCeoActions] = useState<CeoAction[]>([]);
  const [ceoActionsConfirmed, setCeoActionsConfirmed] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  // What failed to load (key -> message). The last data that did load stays on screen; Retry reads it all again.
  const [failures, setFailures] = useState<Record<string, string>>({});
  const note = useCallback((key: string, message: string | null) => {
    setFailures((prev) => {
      if (message === null) {
        if (!(key in prev)) return prev;
        const next = { ...prev };
        delete next[key];
        return next;
      }
      return prev[key] === message ? prev : { ...prev, [key]: message };
    });
  }, []);
  const fits = useMediaQuery(SIDEBAR_AND_PANEL_FIT, true);

  const ceoActionsRequest = useRef(0);
  const refreshCeoActions = useCallback(async () => {
    const ticket = ++ceoActionsRequest.current;
    setCeoActionsConfirmed(false);
    try {
      const actions = await listCeoActions();
      if (ticket !== ceoActionsRequest.current) return;
      setCeoActions(actions);
      setCeoActionsConfirmed(true);
      note('proposals', null);
    } catch (error) {
      if (ticket === ceoActionsRequest.current) note('proposals', messageOf(error));
    }
  }, [note]);
  const chat = useChat(company.agents, company.profiles, company.loaded && company.error === null, reloadAgents, refreshCeoActions);
  const agentDefaultsNotice = chat.engines.needsAgentDefaults && company.loaded
    ? company.error
      ? 'Could not load this Agent’s Engine and Model. Retry Agent discovery above to send.'
      : 'This Agent is missing from the Agent list. Open another chat or refresh the Agent list.'
    : undefined;

  // Every missions request takes a number; only the newest one may update the list, so a slow older answer
  // (for example a late "running" snapshot) never overwrites a newer one.
  const missionsRequest = useRef(0);

  const refreshCompany = useCallback(async () => {
    const ticket = ++missionsRequest.current;
    const [, m] = await Promise.allSettled([reloadAgents(), listMissions(), refreshCeoActions()]);
    if (m.status === 'fulfilled') {
      if (ticket === missionsRequest.current) setMissions(m.value);
      note('missions', null);
    } else note('missions', messageOf(m.reason));
  }, [note, reloadAgents, refreshCeoActions]);

  /** Resolves when the request settles, so the caller can schedule the next poll after it. */
  const refreshMissions = useCallback(async () => {
    const ticket = ++missionsRequest.current;
    try {
      const list = await listMissions();
      if (ticket === missionsRequest.current) setMissions(list);
      note('missions', null);
    } catch (e) {
      // Keep the last known list on screen and say so; the next poll retries.
      note('missions', messageOf(e));
    }
  }, [note]);

  useEffect(() => {
    void refreshCompany();
  }, [refreshCompany, view]);

  const failedParts = [...(company.error ? ['agents'] : []), ...Object.keys(failures)];
  const failureDetail = company.error ?? Object.values(failures)[0] ?? '';
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

  const closeDrawer = () => {
    if (drawer && !sidebarInline && document.activeElement?.closest('[data-sidebar]')) {
      drawerToggle.current?.focus();
    }
    setDrawer(false);
  };

  const goView = (next: View) => {
    chat.invalidateNavigation();
    setView(next);
    closeDrawer();
    if (next !== 'team' && next !== 'contracts') setPanelOpen(false);
  };

  const openChatWith = (agent: Agent) => {
    setNotice(null);
    void chat.openAgentChat(agent, () => goView('chat'), setNotice);
  };

  const decideCeo = async (action: CeoAction, decision: 'approve' | 'deny'): Promise<DecideResult> => {
    let out: CeoAction;
    try {
      out = decision === 'approve' ? await approveCeoAction(action.id) : await denyCeoAction(action.id);
    } catch (e) {
      // 409: someone else decided first. Loqman applies the agents and teams it proposes on its
      // own, so the card can be clicked in the moment before that lands. Show what happened.
      if ((e as ApiError).status !== 409) throw e;
      const done = await ceoActionOutcome(action.id).catch(() => null);
      await refreshCompany();
      if (!done) throw e;
      if (done.status === 'failed') return { failed: done.note || 'The proposal could not be applied.' };
      return { already: done.status === 'approved' ? 'approve' : 'deny' };
    }
    await refreshCompany();
    if (decision === 'approve' && out.status === 'failed') return { failed: out.decision_note || 'The proposal could not be applied.' };
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
          ref={drawerToggle}
          type="button"
          aria-label="Open chat list"
          aria-expanded={drawer}
          onClick={() => drawer ? closeDrawer() : setDrawer(true)}
          className="absolute left-3 top-3 z-30 rounded-md p-1.5 text-ink-secondary hover:bg-raised hover:text-ink md:hidden"
        >
          <Menu size={18} />
        </button>
        {drawer && <div aria-hidden="true" className="absolute inset-0 z-30 bg-black/30 md:hidden" onMouseDown={(event) => event.preventDefault()} onClick={closeDrawer} />}
        <Sidebar
          open={drawer}
          inert={!sidebarInline && !drawer}
          onClose={closeDrawer}
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
          agents={company.agents}
          onOpenAgent={openChatWith}
          onSettings={() => setSettings('general')}
          activeRunIds={chat.activeRunIds}
          needsYou={needsYou}
          onNeedsYou={() => goView('results')}
          userName={userName}
          userPhoto={profilePhoto.url}
          previews={previews}
        />
        {view === 'chat' && (
          <ChatView
            chat={chat}
            engines={chat.engines}
            conversation={conversation}
            legacyThread={legacyThread}
            agent={chat.agentFor(chat.selectedId)}
            onOpenProfile={() => goView('team')}
            onNewAgent={() => setNewAgent(true)}
            onManageProviders={() => setSettings('ai')}
            agentDefaultsNotice={agentDefaultsNotice}
            ready={chat.loaded && company.loaded}
          />
        )}
        {(view === 'team' || view === 'contracts') && (
          <TeamView
            mode={view}
            loaded={company.loaded}
            teams={company.teams}
            agents={company.agents}
            profiles={company.profiles}
            missions={missions}
            onOpenChat={openChatWith}
            onPollMissions={refreshMissions}
            onChanged={() => void refreshCompany()}
            onNewAgent={() => setNewAgent(true)}
            onPanelChange={setPanelOpen}
          />
        )}
        {view === 'results' && <ResultsView chat={chat} ceoActions={ceoActions} ceoActionsConfirmed={ceoActionsConfirmed} onCeoDecide={decideCeo} />}
        {(chat.conversationListError || failedParts.length > 0) && (
          <div className="absolute left-1/2 top-3 z-40 flex max-w-[min(640px,calc(100%-24px))] -translate-x-1/2 flex-col gap-2 rounded-lg border border-danger/30 bg-card px-3 py-2 text-[13px] text-danger shadow-lg">
            {chat.conversationListError && (
              <div role="alert" className="flex items-start gap-2">
                <p dir="auto" className="min-w-0 break-words">{chat.conversationListError}</p>
                <button
                  type="button"
                  className="shrink-0 font-medium underline disabled:opacity-60"
                  disabled={chat.retryingConversationList}
                  onClick={chat.retryConversationList}
                >
                  {chat.retryingConversationList ? 'Retrying…' : 'Retry'}
                </button>
              </div>
            )}
            {failedParts.length > 0 && (
              <div role="alert" data-load-error className="flex items-start gap-2">
                <p dir="auto" className="min-w-0 break-words">
                  Could not refresh {failedParts.join(', ')}. Showing the last data that loaded. {failureDetail}
                </p>
                <button type="button" data-load-retry className="shrink-0 font-medium underline" onClick={() => void refreshCompany()}>
                  Retry
                </button>
              </div>
            )}
          </div>
        )}
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
          onClose={() => setNewAgent(false)}
          onCreated={async () => {
            setNewAgent(false);
            await refreshCompany();
          }}
        />
      )}
      {settings && (
        <SettingsModal
          profilePhoto={profilePhoto}
          initialPage={settings}
          onClose={() => setSettings(null)}
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
