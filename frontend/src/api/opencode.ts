import { createOpencodeClient } from '@opencode-ai/sdk/v2/client';
import { ApiError, call } from './transport';

// The generated SDK invokes its custom fetch with a single `Request` object
// (never `(url, init)`). There is no HTTP proxy any more: the request is handed
// to the worker's `chat.opencode_proxy`, which only allows session reads/prompts
// and re-validates the model/variant against the verified-free catalog.
const sdkFetch: typeof fetch = async (input, init) => {
  const request =
    typeof input === 'string' || input instanceof URL ? new Request(input.toString(), init) : input;
  const url = new URL(request.url);
  const method = request.method.toUpperCase();
  let body: unknown;
  if (method === 'POST') {
    const text = await request.text();
    if (text.length) {
      try {
        body = JSON.parse(text);
      } catch {
        body = undefined;
      }
    }
  }
  const path = url.pathname.replace(/^\/+/, '');
  const query = url.search.replace(/^\?/, '');
  try {
    const result = await call<{ data: unknown }>('chat.opencode_proxy', {
      method,
      path,
      ...(query ? { query } : {}),
      ...(body && typeof body === 'object' ? { body } : {}),
    });
    return new Response(JSON.stringify(result.data ?? null), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    });
  } catch (e) {
    const err = e as ApiError;
    return new Response(JSON.stringify({ detail: err.message }), {
      status: err.status ?? 502,
      headers: { 'Content-Type': 'application/json' },
    });
  }
};

// Locked OpenCode agent for conversational surfaces. It must stay in sync with
// settings.CHAT_AGENT_ID on the backend; "build" is never used by the app.
const AGENT_ID = 'agentos-chat';

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
  const { session } = await call<{ session: SavedConversationSession | null }>('chat.session.get', {
    conversation_id: conversationId,
  });
  return session;
}

async function saveSession(conversationId: string, session: SavedConversationSession) {
  await call('chat.session.save', {
    conversation_id: conversationId,
    session_id: session.session_id,
    model_id: session.model_id,
    provider_id: session.provider_id,
    agent_id: session.agent_id,
  });
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
  // Security migration: a conversation pinned to an unrestricted agent
  // (legacy "build") is re-opened with the locked AgentOS chat agent instead
  // of silently continuing on the unsafe session.
  if (mapping && mapping.agent_id !== AGENT_ID) {
    mapping = null;
  }
  if (!mapping) {
    const created: any = await client.session.create({
      title: `Loqman chat ${args.conversationId}`,
      agent: AGENT_ID,
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
      agent_id: AGENT_ID,
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
