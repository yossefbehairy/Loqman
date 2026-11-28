/*
 * Live view of one streaming turn, built from normalised-v1 driver events.
 * Pure: the chat hook feeds events in and renders the snapshot. Unknown event types and
 * malformed payloads are ignored, so a new worker event can never break or end a turn.
 */

export interface LiveItem {
  id: string;
  kind: 'text' | 'tool';
  /** text items: the text so far. */
  text: string;
  /** text items: set when the worker completes the item; commentary is what Claude says before a tool call. */
  phase: 'commentary' | 'final' | null;
  done: boolean;
  /** tool items. */
  title: string;
  status: 'running' | 'completed' | 'failed' | 'unknown';
  /** True once at least one content.delta arrived; items without deltas are never shown (legacy engines). */
  streamed: boolean;
}

export interface LiveTurn {
  runId: string;
  items: LiveItem[];
  /** The user pressed Stop: the text no longer changes. */
  stopped: boolean;
}

export const emptyLive = (runId: string): LiveTurn => ({ runId, items: [], stopped: false });

const isRecord = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null && !Array.isArray(v);

function upsert(turn: LiveTurn, id: string, make: () => LiveItem, patch: (item: LiveItem) => LiveItem): LiveTurn {
  const at = turn.items.findIndex((i) => i.id === id);
  if (at < 0) return { ...turn, items: [...turn.items, patch(make())] };
  const items = turn.items.slice();
  items[at] = patch(items[at]);
  return { ...turn, items };
}

const newItem = (id: string, kind: LiveItem['kind']): LiveItem => ({
  id, kind, text: '', phase: null, done: false, title: '', status: 'running', streamed: false,
});

/** Fold one driver event envelope (the `data` of a normalised stream message) into the turn. */
export function reduceLive(turn: LiveTurn, type: string, envelope: Record<string, unknown>): LiveTurn {
  if (turn.stopped) return turn;
  const itemId = typeof envelope.item_id === 'string' && envelope.item_id ? envelope.item_id : null;
  const data = isRecord(envelope.data) ? envelope.data : {};
  if (!itemId) return turn;
  if (type === 'content.delta') {
    if (data.stream_kind !== 'assistant_text' || typeof data.delta !== 'string' || !data.delta) return turn;
    const delta = data.delta;
    return upsert(turn, itemId, () => newItem(itemId, 'text'), (i) =>
      i.kind !== 'text' ? i : { ...i, text: i.text + delta, streamed: true });
  }
  if (data.item_type === 'tool') {
    if (type === 'item.started') {
      const title = typeof data.title === 'string' && data.title ? data.title : 'tool';
      return upsert(turn, itemId, () => newItem(itemId, 'tool'), (i) => ({ ...i, kind: 'tool', title: i.title || title }));
    }
    if (type === 'item.completed') {
      const status = data.status === 'completed' || data.status === 'failed' ? data.status : 'unknown';
      return upsert(turn, itemId, () => ({ ...newItem(itemId, 'tool'), title: 'tool' }), (i) => ({ ...i, done: true, status }));
    }
    return turn;
  }
  if (type === 'item.completed' && data.item_type === 'assistant') {
    const at = turn.items.findIndex((i) => i.id === itemId);
    if (at < 0) return turn; // never streamed: not shown live, the persisted message is the reply
    const phase = data.phase === 'commentary' ? 'commentary' : 'final';
    const items = turn.items.slice();
    // The completed text is authoritative and replaces the accumulated deltas.
    items[at] = { ...items[at], done: true, phase, text: typeof data.text === 'string' ? data.text : items[at].text };
    return { ...turn, items };
  }
  return turn;
}

export const freezeLive = (turn: LiveTurn): LiveTurn => (turn.stopped ? turn : { ...turn, stopped: true });

/** Whether the turn has anything worth a bubble. */
export const hasLiveContent = (turn: LiveTurn | null | undefined): turn is LiveTurn =>
  Boolean(turn && turn.items.some((i) => (i.kind === 'text' ? i.streamed && i.text.length > 0 : true)));

export const TERMINAL_RUN_STATUSES = ['succeeded', 'failed', 'cancelled', 'interrupted'] as const;
