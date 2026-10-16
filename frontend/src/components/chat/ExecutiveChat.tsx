/* oxlint-disable react/set-state-in-effect, react-hooks/exhaustive-deps */
import React, { useState, useRef, useEffect } from 'react';
import {
  Send,
  Paperclip,
  Loader2,
  Play,
  RotateCcw,
} from 'lucide-react';
import type { RecentConversation } from '../sidebar/Sidebar';
import { MANAGER_OPTIONS } from '../../data/defaultCompany';
import { consultManager } from '../../api/client';
import type { ManagerConsultationResponse, SuggestedMission } from '../../types';

interface ChatMessageItem {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  timestamp: string;
  managerName?: string;
  managerTitle?: string;
  suggestedMission?: SuggestedMission;
}

interface ExecutiveChatProps {
  onNavigateToMap?: () => void;
  onLaunchMission?: (missionTitle: string, initialInput: string) => void;
  activeConversationId: string;
  onActiveConversationChange: (id: string) => void;
  onConversationsChange: (conversations: RecentConversation[]) => void;
}

const QUICK_PROMPTS = [
  {
    label: 'نظام تسجيل الطلاب وتوصية المقررات',
    prompt: 'نحتاج خطة متكاملة لتطوير منصة تسجيل الطلاب وتوصية المقررات للجامعة بالذكاء الاصطناعي',
  },
  {
    label: 'دراسة جدوى ومصفوفة المخاطر المعمارية',
    prompt: 'قم بإعداد دراسة جدوى فنية وتقييم مخاطر الأداء والأمان المعماري لمنصة خدمات مصرفية متزامنة',
  },
  {
    label: 'مصفوفة ضبط الجودة واعتماد مخرجات العمل',
    prompt: 'ضع مصفوفة صارمة للتحقق من جودة مخرجات النظام والتأكد من مطابقة عقود JSON والشروط الطرفية',
  },
  {
    label: 'إطلاق بنية تحتية لمؤسسة ذكية',
    prompt: 'تفكيك خطة إطلاق نظام حوكمة البيانات المؤسسية إلى مراحل معمارية ومحطات تسليم واضحة',
  },
];

interface StoredConversation extends RecentConversation {
  messages: ChatMessageItem[];
  managerId: string;
}

const STORAGE_KEY = 'agentos-conversations-v1';

function readConversations(): StoredConversation[] {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    const parsed = stored ? JSON.parse(stored) : [];
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function generateMsgId(prefix: string): string {
  return `${prefix}-${Date.now()}`;
}

function getFormattedTime(): string {
  return new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}


export const ExecutiveChat: React.FC<ExecutiveChatProps> = ({
  onNavigateToMap,
  onLaunchMission,
  activeConversationId,
  onActiveConversationChange,
  onConversationsChange,
}) => {
  const [storedConversations, setStoredConversations] = useState<StoredConversation[]>(readConversations);
  const [selectedManagerId, setSelectedManagerId] = useState<string>('general_manager');
  const [inputMessage, setInputMessage] = useState<string>('');
  const [messages, setMessages] = useState<ChatMessageItem[]>([]);
  const [isLoading, setIsLoading] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);
  const [loadedConversationId, setLoadedConversationId] = useState('');

  const messagesEndRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const selectedManager =
    MANAGER_OPTIONS.find((m) => m.id === selectedManagerId) || MANAGER_OPTIONS[0];
  const activeConversationTitle = storedConversations.find((item) => item.id === activeConversationId)?.title || 'محادثة جديدة';

  useEffect(() => {
    onConversationsChange(storedConversations.map(({ id, title, updatedAt }) => ({ id, title, updatedAt })));
    localStorage.setItem(STORAGE_KEY, JSON.stringify(storedConversations));
  }, [storedConversations, onConversationsChange]);

  useEffect(() => {
    if (!activeConversationId) {
      const first = storedConversations[0];
      onActiveConversationChange(first?.id || `chat-${Date.now()}`);
      return;
    }

    const selected = storedConversations.find((conversation) => conversation.id === activeConversationId);
    setStoredConversations((previous) => {
      if (previous.some((conversation) => conversation.id === activeConversationId)) return previous;
      return [{ id: activeConversationId, title: 'محادثة جديدة', updatedAt: Date.now(), messages: [], managerId: 'general_manager' }, ...previous].slice(0, 40);
    });
    setMessages(selected?.messages ?? []);
    setSelectedManagerId(selected?.managerId ?? 'general_manager');
    setLoadedConversationId(activeConversationId);
    setInputMessage('');
    setError(null);
  }, [activeConversationId]);

  useEffect(() => {
    if (!activeConversationId || loadedConversationId !== activeConversationId) return;
    const firstUserMessage = messages.find((message) => message.role === 'user')?.content;
    const title = firstUserMessage?.slice(0, 48) || 'محادثة جديدة';
    const updatedAt = Date.now();
    setStoredConversations((previous) => {
      const existing = previous.find((conversation) => conversation.id === activeConversationId);
      const nextConversation: StoredConversation = {
        id: activeConversationId,
        title,
        updatedAt: messages.length ? updatedAt : existing?.updatedAt ?? updatedAt,
        messages,
        managerId: selectedManagerId,
      };
      return [nextConversation, ...previous.filter((conversation) => conversation.id !== activeConversationId)].slice(0, 40);
    });
  }, [messages, selectedManagerId, activeConversationId, loadedConversationId]);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, isLoading]);

  const handleSendMessage = async (textToSend?: string) => {
    const text = (textToSend || inputMessage).trim();
    if (!text || isLoading) return;

    setError(null);
    const userMsgId = generateMsgId('user');
    const newHistory: ChatMessageItem[] = [
      ...messages,
      {
        id: userMsgId,
        role: 'user',
        content: text,
        timestamp: getFormattedTime(),
      },
    ];

    setMessages(newHistory);
    setInputMessage('');
    setIsLoading(true);

    try {
      const historyPayload = newHistory.map((m) => ({
        role: m.role,
        content: m.content,
      }));

      const res: ManagerConsultationResponse = await consultManager(
        selectedManagerId,
        text,
        historyPayload
      );

      const assistantMsgId = generateMsgId('asst');
      setMessages((prev) => [
        ...prev,
        {
          id: assistantMsgId,
          role: 'assistant',
          content: res.reply,
          timestamp: getFormattedTime(),
          managerName: res.manager_name || selectedManager.nameArabic,
          managerTitle: res.title || selectedManager.title,
          suggestedMission: res.suggested_mission,
        },
      ]);
    } catch (err) {
      console.error('Consultation error:', err instanceof Error ? err.message : String(err));
      // Fallback deterministic mock in case backend is offline or encountered network issue
      const fallbackMsgId = generateMsgId('asst-fb');
      const fallbackReply = `أهلاً بك! بصفتي ${selectedManager.nameArabic} (${selectedManager.title})، تلقيت استشارتك: "${text}". يسعدني توجيهك بالخطوات التنفيذية التالية، وتنسيق العمل مع أعضاء الإدارة لضمان أفضل مخرجات.`;
      
      setMessages((prev) => [
        ...prev,
        {
          id: fallbackMsgId,
          role: 'assistant',
          content: fallbackReply,
          timestamp: getFormattedTime(),
          managerName: selectedManager.nameArabic,
          managerTitle: selectedManager.title,
          suggestedMission: {
            title: `مهمة استشارية: ${text.slice(0, 40)}...`,
            input_json: { goal: text, manager_id: selectedManagerId },
          },
        },
      ]);
    } finally {
      setIsLoading(false);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSendMessage();
    }
  };

  const handleClearHistory = () => {
    setMessages([]);
    setError(null);
  };

  const handleNewConversation = () => {
    setMessages([]);
    setError(null);
    setInputMessage('');
    setSelectedManagerId('general_manager');
    onActiveConversationChange(`chat-${Date.now()}`);
  };

  return (
    <div className="relative flex h-full min-w-0 flex-1 flex-col overflow-hidden bg-gray-900 font-sans text-gray-100" dir="rtl">
      <header className="z-20 flex h-auto min-h-[58px] shrink-0 flex-col border-b border-gray-800 bg-gray-900 px-4 sm:px-6">
        {/* Branding row */}
        <div className="flex items-center justify-between py-2">
          <div className="flex items-center gap-2">
            <span className="text-xs font-semibold text-indigo-400 tracking-wide" data-testid="executive-chat-heading">Executive Chat</span>
            <span className="text-gray-700 text-xs">|</span>
            <span className="text-xs text-gray-500 font-arabic">الدردشة التنفيذية</span>
            <span className="hidden h-3 border-r border-gray-700 sm:inline mx-1" />
            <span className="max-w-[160px] truncate text-xs text-gray-400 hidden sm:inline">{activeConversationTitle}</span>
          </div>
          <div className="flex shrink-0 items-center gap-1">
            {messages.length > 0 && <button type="button" onClick={handleClearHistory} title="مسح المحادثة" aria-label="مسح المحادثة" className="rounded-lg p-2 text-gray-400 transition hover:bg-gray-800 hover:text-gray-100"><RotateCcw className="h-4 w-4" /></button>}
            {onNavigateToMap && <button type="button" onClick={onNavigateToMap} className="rounded-lg px-3 py-2 text-xs text-gray-300 transition hover:bg-gray-800 hover:text-gray-100">مساحة العمل</button>}
          </div>
        </div>
        {/* Persona Selector Pills */}
        <div className="flex items-center gap-1.5 pb-2 flex-wrap">
          {MANAGER_OPTIONS.map((manager) => (
            <button
              key={manager.id}
              type="button"
              data-persona={manager.dataPersona}
              onClick={() => setSelectedManagerId(manager.id)}
              title={manager.nameEnglish}
              className={`flex items-center gap-1.5 rounded-full px-3 py-1 text-xs transition focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-400 ${
                selectedManagerId === manager.id
                  ? 'bg-indigo-600 text-white font-semibold'
                  : 'bg-gray-800 text-gray-300 hover:bg-gray-700 hover:text-gray-100'
              }`}
            >
              <span className="font-arabic">{manager.nameArabic}</span>
              <span className="opacity-60 hidden sm:inline">·</span>
              <span className="hidden sm:inline opacity-70">{manager.departmentEnglish}</span>
            </button>
          ))}
        </div>
      </header>

      {/* Main Conversation Stream */}
      <div className="mx-auto w-full max-w-3xl flex-1 space-y-6 overflow-y-auto px-4 py-6 sm:px-6 sm:py-8">
        {error && (
          <div className="p-3 rounded-xl bg-red-950/50 border border-red-500/30 text-xs text-red-300">
            {error}
          </div>
        )}


        {/* Empty State / Welcome */}
        {messages.length === 0 && (
          <div className="flex min-h-[48vh] flex-col items-center justify-center py-10 text-center">
            <h1 className="mb-8 text-2xl font-semibold tracking-tight text-gray-100 sm:text-3xl">كيف أقدر أساعدك؟</h1>
            <div className="grid w-full max-w-2xl grid-cols-1 gap-2 sm:grid-cols-2">
              {QUICK_PROMPTS.map((prompt) => (
                <button key={prompt.label} type="button" onClick={() => handleSendMessage(prompt.prompt)} className="rounded-2xl border border-gray-700 bg-gray-800/70 p-4 text-right text-sm leading-6 text-gray-300 transition hover:bg-gray-800 hover:text-gray-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-400">
                  {prompt.label}
                </button>
              ))}
            </div>
          </div>
        )}

        {/* Message Thread */}
        {messages.map((msg) => (
          <div
            key={msg.id}
            className={`flex gap-3 ${msg.role === 'user' ? 'justify-start' : 'justify-start'}`}
          >
            <div
              className={`max-w-[92%] transition-all sm:max-w-[86%] ${
                msg.role === 'user'
                  ? 'mr-auto rounded-3xl bg-gray-800 px-5 py-3 text-gray-100'
                  : 'text-gray-200 assistant-message'
              }`}
              data-message-role={msg.role}
              data-testid={msg.role === 'assistant' ? 'manager-response' : undefined}
            >
              {msg.role === 'assistant' && (
                <div className="mb-2 flex items-center gap-2 text-xs text-gray-400">
                  <span className="grid h-6 w-6 place-items-center rounded-full border border-gray-600 text-[10px] text-gray-200">A</span>
                  <span>{msg.managerName || selectedManager.nameArabic}</span>
                </div>
              )}

              <div className="text-sm leading-relaxed whitespace-pre-wrap font-sans">
                {msg.content}
              </div>

              {/* Suggested Mission Action Card */}
              {msg.suggestedMission && (
                <div className="mt-4 rounded-2xl border border-gray-700 bg-gray-800/50 p-4">
                  <div className="mb-2 text-xs font-medium text-gray-400">
                    <span>مهمة مقترحة</span>
                  </div>
                  <p className="text-xs text-gray-300 mb-2.5 font-medium">
                    {msg.suggestedMission.title}
                  </p>
                  <div className="flex gap-2">
                    <button
                      type="button"
                      onClick={() => {
                        if (onLaunchMission) {
                          onLaunchMission(
                            msg.suggestedMission!.title,
                            JSON.stringify(msg.suggestedMission!.input_json)
                          );
                        } else if (onNavigateToMap) {
                          onNavigateToMap();
                        }
                      }}
                      className="flex items-center gap-1.5 rounded-xl bg-blue-500 px-3 py-2 text-xs font-medium text-white transition hover:bg-blue-400"
                    >
                      <Play className="w-3 h-3 fill-current" />
                      <span>فتح في مساحة العمل</span>
                    </button>
                  </div>
                </div>
              )}

              {msg.role === 'user' && (
                <div className="mt-1 text-left text-[10px] text-gray-400">
                  {msg.timestamp}
                </div>
              )}
            </div>

          </div>
        ))}

        {/* Loading Indicator */}
        {isLoading && (
          <div
            className="flex gap-3 justify-start"
            data-message-role="assistant"
            data-testid="manager-response"
          >
            <div className="rounded-2xl bg-gray-800 px-4 py-3 text-gray-300 flex items-center gap-3 assistant-message">
              <Loader2 className="w-4 h-4 text-gray-400 animate-spin" />
              <span className="text-xs text-gray-300 font-arabic">جارٍ إعداد الرد...</span>
            </div>
          </div>
        )}

        <div ref={messagesEndRef} />
      </div>

      <footer className="z-20 shrink-0 px-3 pb-3 pt-2 sm:px-6 sm:pb-5">
        <div className="mx-auto max-w-3xl">
          <form
            onSubmit={(e) => {
              e.preventDefault();
              handleSendMessage();
            }}
            className="relative rounded-[26px] border border-gray-700 bg-gray-800 px-3 py-2 shadow-sm transition focus-within:border-gray-600 sm:px-4"
          >
            <textarea
              ref={textareaRef}
              data-testid="executive-chat-input"
              rows={2}
              value={inputMessage}
              onChange={(e) => setInputMessage(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="اكتب رسالتك..."
              className="max-h-40 min-h-12 w-full resize-none bg-transparent px-2 py-2 text-sm leading-6 text-gray-100 outline-none placeholder:text-gray-400"
            />

            <div className="flex items-center justify-between pt-1">
              <div className="flex items-center gap-1">
                <button type="button" title="إضافة ملف" aria-label="إضافة ملف" className="rounded-full p-2 text-gray-300 transition hover:bg-gray-700"><Paperclip className="h-[18px] w-[18px]" /></button>
                <button type="button" onClick={handleNewConversation} title="محادثة جديدة" aria-label="محادثة جديدة" className="rounded-full px-3 py-2 text-xs text-gray-300 transition hover:bg-gray-700">محادثة جديدة</button>
              </div>
              <div className="flex items-center gap-2">
                <button
                  type="submit"
                  disabled={!inputMessage.trim() || isLoading}
                  aria-label="إرسال الرسالة"
                  className={`grid h-9 w-9 place-items-center rounded-full transition ${
                    inputMessage.trim() && !isLoading
                      ? 'bg-blue-500 text-white hover:bg-blue-400'
                      : 'bg-gray-700 text-gray-500 cursor-not-allowed'
                  }`}
                >
                  <Send className="h-4 w-4" />
                </button>
              </div>
            </div>
          </form>
          <div className="mt-2 text-center text-[11px] text-gray-500">
            {selectedManager.nameArabic} · {selectedManager.title}
          </div>
        </div>
      </footer>
    </div>
  );
};
