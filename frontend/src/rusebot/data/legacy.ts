/**
 * Read-only view of conversations kept by the classic Loqman UI in
 * localStorage (`agentos_ceo_threads_v1`). The worker's
 * `conversations.import_ceo` only copies the `ceo_messages` table, so these
 * browser-side threads are surfaced separately instead of being lost.
 */

export interface LegacyTurn {
  question: string;
  reply: string;
  error?: string;
  ts: number;
}

export interface LegacyThread {
  id: string;
  title: string;
  ts: number;
  turns: LegacyTurn[];
}

interface StoredThread {
  id?: unknown;
  question?: unknown;
  reply?: unknown;
  error?: unknown;
  ts?: unknown;
  turns?: unknown;
}

const text = (value: unknown): string => (typeof value === 'string' ? value : '');

export function parseLegacyThreads(raw: string | null): LegacyThread[] {
  if (!raw) return [];
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return [];
  }
  if (!Array.isArray(parsed)) return [];
  const out: LegacyThread[] = [];
  for (const item of parsed as StoredThread[]) {
    if (!item || typeof item !== 'object' || typeof item.id !== 'string') continue;
    const turnsRaw = Array.isArray(item.turns) ? (item.turns as StoredThread[]) : [];
    const turns: LegacyTurn[] = turnsRaw.map((t) => ({
      question: text(t.question),
      reply: text(t.reply),
      error: typeof t.error === 'string' ? t.error : undefined,
      ts: typeof t.ts === 'number' ? t.ts : 0,
    }));
    if (turns.length === 0 && text(item.question)) {
      turns.push({ question: text(item.question), reply: text(item.reply), ts: typeof item.ts === 'number' ? item.ts : 0 });
    }
    out.push({
      id: item.id,
      title: text(item.question) || turns[0]?.question || 'Earlier chat',
      ts: typeof item.ts === 'number' ? item.ts : (turns[turns.length - 1]?.ts ?? 0),
      turns,
    });
  }
  return out.sort((a, b) => b.ts - a.ts);
}

export function readLegacyThreads(): LegacyThread[] {
  try {
    return parseLegacyThreads(localStorage.getItem('agentos_ceo_threads_v1'));
  } catch {
    return [];
  }
}
