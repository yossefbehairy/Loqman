import { createOpencodeClient } from '@opencode-ai/sdk/v2/client';
import { API_BASE } from './client';

// The generated SDK invokes its custom fetch with a single `Request` object
// (never `(url, init)`), so the request method, headers and body must be copied
// explicitly when the URL is rewritten onto the local AgentOS proxy.
const sdkFetch: typeof fetch = async (input, init) => {
  const request =
    typeof input === 'string' || input instanceof URL ? new Request(input.toString(), init) : input;
  const url = new URL(request.url);
  const target = `${API_BASE}/opencode-proxy${url.pathname}${url.search}`;
  const hasBody = request.method !== 'GET' && request.method !== 'HEAD';
  let body: BodyInit | undefined;
  if (hasBody) {
    const text = await request.text();
    body = text.length ? text : undefined;
  }
  return fetch(target, { method: request.method, headers: request.headers, body, signal: request.signal });
};

const client = createOpencodeClient({
  baseUrl: 'http://agentos-opencode.local',
  fetch: sdkFetch,
  responseStyle: 'data',
  throwOnError: true,
});

export interface SavedConversationSession {
  conversation_id: string;
  session_id: string;
  model_id: string;
  provider_id: string;
  agent_id: string;
}

async function getSavedSession(conversationId: string): Promise<SavedConversationSession | null> {
  const response = await fetch(`${API_BASE}/chat/sessions/${encodeURIComponent(conversationId)}`);
  if (!response.ok) throw new Error((await response.json()).detail || `Session lookup failed (${response.status})`);
  return response.json();
}

async function saveSession(conversationId: string, session: SavedConversationSession) {
  const response = await fetch(`${API_BASE}/chat/sessions/${encodeURIComponent(conversationId)}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(session),
  });
  if (!response.ok) throw new Error((await response.json()).detail || `Session save failed (${response.status})`);
}

function extractText(message: any): string {
  if (!message) return '';
  const parts = message.parts ?? message.content;
  if (Array.isArray(parts)) {
    return parts
      .filter((part: any) => part?.type === 'text')
      .map((part: any) => part.text || '')
      .join('');
  }
  if (typeof message.text === 'string') return message.text;
  return '';
}

function isAssistantMessage(item: any): boolean {
  return (
    item?.info?.role === 'assistant' ||
    item?.role === 'assistant' ||
    item?.type === 'assistant'
  );
}

export async function sendOpenCodeMessage(args: {
  conversationId: string;
  text: string;
  modelId: string;
  providerId: string;
  variant?: string;
}): Promise<string> {
  let mapping = await getSavedSession(args.conversationId);
  if (!mapping) {
    const created: any = await client.session.create({
      title: `AgentOS chat ${args.conversationId}`,
      agent: 'build',
      model: { id: args.modelId, providerID: args.providerId, ...(args.variant ? { variant: args.variant } : {}) },
    });
    const session = created?.data ?? created;
    const sessionId = session?.id ?? session?.sessionID;
    if (!sessionId) throw new Error('OpenCode did not return a session ID.');
    mapping = {
      conversation_id: args.conversationId,
      session_id: sessionId,
      model_id: args.modelId,
      provider_id: args.providerId,
      agent_id: 'build',
    };
    await saveSession(args.conversationId, mapping);
  } else if (mapping.model_id !== args.modelId || mapping.provider_id !== args.providerId) {
    mapping = { ...mapping, model_id: args.modelId, provider_id: args.providerId };
    await saveSession(args.conversationId, mapping);
  }

  const prompted: any = await client.session.prompt({
    sessionID: mapping.session_id,
    agent: mapping.agent_id,
    model: { providerID: args.providerId, modelID: args.modelId },
    ...(args.variant ? { variant: args.variant } : {}),
    parts: [{ type: 'text', text: args.text }],
  });
  const promptResponse = prompted?.data ?? prompted;
  const directText = extractText(promptResponse);
  if (directText.trim()) return directText.trim();

  const response: any = await client.session.messages({ sessionID: mapping.session_id });
  const messages = response?.data ?? response;
  const list = Array.isArray(messages) ? messages : messages?.messages ?? [];
  const assistant = [...list].reverse().find(isAssistantMessage);
  const text = extractText(assistant);
  if (!text.trim()) throw new Error('OpenCode completed the request without returning assistant text.');
  return text.trim();
}
