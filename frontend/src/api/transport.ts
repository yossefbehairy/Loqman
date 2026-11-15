/**
 * Transport to the Python worker (no HTTP API any more).
 *
 * - Electron: `window.agentos` (preload) -> main process -> worker over stdin/stdout.
 * - Browser dev / Playwright: the Vite dev bridge (`/__agentos/call`, `/__agentos/stream`),
 *   which talks to the very same worker. It exists only in `vite dev`.
 *
 * Errors are normalised to `Error(detail)` with `.status`, like the old REST client.
 */

export interface StreamMessage {
  type: 'stream' | 'stream_end';
  stream_id: string;
  event?: string;
  id?: number | null;
  data?: unknown;
  reason?: string;
}

/** `normalised-v1` adds the driver's content/tool events to a run stream; `legacy` is lifecycle status only. */
export type StreamEventFormat = 'legacy' | 'normalised-v1';

interface AgentosBridge {
  call: (method: string, params?: Record<string, unknown>) => Promise<unknown>;
  openStream: (
    topic: string,
    id: string,
    lastEventId: number,
    onMessage: (msg: StreamMessage) => void,
    eventFormat?: StreamEventFormat
  ) => Promise<{ close: () => Promise<boolean> | void }>;
}

function bridge(): AgentosBridge | undefined {
  return typeof window !== 'undefined' ? (window as unknown as { agentos?: AgentosBridge }).agentos : undefined;
}

export class ApiError extends Error {
  status?: number;
  detail?: unknown;
  constructor(message: string, status?: number, detail?: unknown) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.detail = detail;
  }
}

/** The Electron bridge carries {status, detail} as JSON inside Error.message. */
function fromBridge(e: unknown): ApiError {
  const err = e as { status?: number; detail?: unknown; message?: string };
  if (err.status !== undefined || err.detail !== undefined) return toError(err.status, err.detail ?? err.message);
  try {
    const payload = JSON.parse(err.message ?? '');
    if (payload && typeof payload === 'object' && 'detail' in payload) {
      return toError(payload.status ?? undefined, payload.detail);
    }
  } catch {
    /* plain message */
  }
  return toError(undefined, err.message);
}

function toError(status: number | undefined, detail: unknown): ApiError {
  const message = typeof detail === 'string' ? detail : JSON.stringify(detail ?? 'Request failed');
  return new ApiError(message, status, detail);
}

export async function call<T>(method: string, params: Record<string, unknown> = {}): Promise<T> {
  const b = bridge();
  if (b) {
    try {
      return (await b.call(method, params)) as T;
    } catch (e) {
      throw fromBridge(e);
    }
  }
  // Browser dev: Vite dev bridge.
  const res = await fetch('/__agentos/call', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ method, params }),
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw toError(body.status ?? res.status, body.detail ?? body.message ?? res.statusText);
  return body.result as T;
}

export interface StreamHandle {
  close: () => void;
}

/** Open a worker event stream (run | mission). Resolves once it is open. */
export async function openStream(
  topic: 'run' | 'mission',
  id: string,
  onMessage: (msg: StreamMessage) => void,
  lastEventId = 0,
  eventFormat: StreamEventFormat = 'legacy'
): Promise<StreamHandle> {
  const b = bridge();
  if (b) {
    try {
      const handle = await b.openStream(topic, id, lastEventId, onMessage, eventFormat);
      return { close: () => void handle.close() };
    } catch (e) {
      throw fromBridge(e);
    }
  }
  const url = `/__agentos/stream?topic=${encodeURIComponent(topic)}&id=${encodeURIComponent(id)}&last=${lastEventId}${eventFormat === 'legacy' ? '' : `&format=${encodeURIComponent(eventFormat)}`}`;
  const es = new EventSource(url);
  const forward = (ev: MessageEvent) => {
    try {
      const msg = JSON.parse(ev.data);
      if (msg.type === 'stream_end') es.close();
      onMessage(msg);
    } catch {
      /* ignore malformed frames */
    }
  };
  es.addEventListener('stream', forward as EventListener);
  es.addEventListener('stream_end', forward as EventListener);
  return { close: () => es.close() };
}

/**
 * EventSource-shaped adapter over a worker mission stream, so existing watchers
 * (`addEventListener`, `onopen`, `onerror`, `close`) keep working unchanged.
 * The stream is re-opened (replaying persisted state) if it ends unexpectedly.
 */
export class WorkerEventSource {
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
  private listeners = new Map<string, Array<(ev: { data: string }) => void>>();
  private handle: StreamHandle | null = null;
  private closed = false;
  private topic: 'run' | 'mission';
  private id: string;

  constructor(topic: 'run' | 'mission', id: string) {
    this.topic = topic;
    this.id = id;
    void this.connect();
  }

  addEventListener(name: string, fn: (ev: { data: string }) => void): void {
    const list = this.listeners.get(name) ?? [];
    list.push(fn);
    this.listeners.set(name, list);
  }

  close(): void {
    this.closed = true;
    this.handle?.close();
    this.handle = null;
  }

  private async connect(): Promise<void> {
    if (this.closed) return;
    try {
      this.handle = await openStream(this.topic, this.id, (msg) => {
        if (msg.type === 'stream_end') {
          if (msg.reason === 'complete' || msg.reason === 'closed') {
            this.close();
            return;
          }
          if (!this.closed && msg.reason !== 'closed') {
            this.onerror?.();
            setTimeout(() => void this.connect(), 1500);
          }
          return;
        }
        for (const fn of this.listeners.get(msg.event ?? '') ?? []) fn({ data: JSON.stringify(msg.data ?? {}) });
      });
      if (this.closed) {
        this.handle.close();
        return;
      }
      this.onopen?.();
    } catch {
      if (!this.closed) {
        this.onerror?.();
        setTimeout(() => void this.connect(), 3000);
      }
    }
  }
}

export const openMissionEvents = (missionId: string) => new WorkerEventSource('mission', missionId);
