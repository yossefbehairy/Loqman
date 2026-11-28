// DEV-ONLY Vite plugin: lets the plain-browser dev server (`npm run dev`) and the
// Playwright e2e suite reach the Python worker the same way Electron does.
// It is NOT part of the product build (Electron talks to the worker over IPC):
//   POST /__agentos/call    {method, params} -> {result} | {status, detail}
//   GET  /__agentos/stream?topic=&id=&last=  -> Server-Sent Events of worker streams
// The worker runs with RUNTIME_MODE from the environment (e2e uses mock) and an
// isolated data dir, never the user's real database unless you point it there.
import { createRequire } from 'node:module';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const require = createRequire(import.meta.url);
const { WorkerClient, workerCommand, METHOD_RE } = require('./electron/worker-client.cjs');
const here = path.dirname(fileURLToPath(import.meta.url));

/**
 * Cross-origin guard for the dev bridge. Any web page open in the user's browser can
 * fire a request at http://127.0.0.1:5173, so: a present `Origin` must be this server's
 * own origin (its Host), a browser-declared cross-site fetch is refused, and the call
 * endpoint only takes `application/json` (a text/plain "simple" POST needs no preflight).
 * Returns null when the request may proceed, else { status, detail }.
 */
export function guardRequest(req, { requireJson }) {
  const header = (name) => {
    const v = req.headers?.[name];
    return Array.isArray(v) ? v[0] : v;
  };
  const origin = header('origin');
  if (origin !== undefined) {
    let host = null;
    try {
      host = new URL(origin).host;
    } catch {
      /* an unparsable Origin (including "null") is never our own */
    }
    if (!host || host !== header('host')) return { status: 403, detail: 'Cross-origin request refused.' };
  }
  const site = header('sec-fetch-site');
  if (site !== undefined && site !== 'same-origin' && site !== 'none') {
    return { status: 403, detail: 'Cross-origin request refused.' };
  }
  if (requireJson) {
    const type = String(header('content-type') || '').split(';')[0].trim().toLowerCase();
    if (type !== 'application/json') return { status: 415, detail: 'Content-Type must be application/json.' };
  }
  return null;
}

export default function agentosBridge() {
  let client = null;
  let starting = null;
  const sseClients = new Map(); // stream_id -> res

  function ensureWorker() {
    if (client && !client.exited) return starting;
    client = new WorkerClient();
    const dataDir = process.env.AGENTOS_DATA_DIR
      ? path.resolve(here, '..', process.env.AGENTOS_DATA_DIR)
      : path.resolve(here, '..', '.agentos-dev-data');
    const { command, args, options } = workerCommand({
      isDev: true,
      repoRoot: path.resolve(here, '..'),
      dataDir,
      runtimeMode: process.env.RUNTIME_MODE || 'mock',
    });
    client.on('log', (line) => console.log(`[worker] ${line}`));
    client.on('stream', (msg) => {
      const res = sseClients.get(msg.stream_id);
      if (res) res.write(`event: stream\ndata: ${JSON.stringify(msg)}\n\n`);
    });
    client.on('stream_end', (msg) => {
      const res = sseClients.get(msg.stream_id);
      if (res) {
        res.write(`event: stream_end\ndata: ${JSON.stringify(msg)}\n\n`);
        res.end();
        sseClients.delete(msg.stream_id);
      }
    });
    starting = client.start({ command, args, options });
    return starting;
  }

  const readBody = (req) =>
    new Promise((resolve, reject) => {
      let raw = '';
      req.on('data', (c) => {
        raw += c;
        if (raw.length > 4 * 1024 * 1024) reject(new Error('Body too large'));
      });
      req.on('end', () => resolve(raw));
      req.on('error', reject);
    });

  const sendJson = (res, status, payload) => {
    res.statusCode = status;
    res.setHeader('Content-Type', 'application/json');
    res.end(JSON.stringify(payload));
  };

  return {
    name: 'agentos-dev-bridge',
    apply: 'serve',
    configureServer(server) {
      server.httpServer?.once('close', () => client?.stop().catch(() => {}));
      server.middlewares.use('/__agentos/call', async (req, res) => {
        if (req.method !== 'POST') return sendJson(res, 405, { detail: 'POST only' });
        const refused = guardRequest(req, { requireJson: true });
        if (refused) return sendJson(res, refused.status, { detail: refused.detail });
        try {
          const { method, params } = JSON.parse(await readBody(req));
          if (typeof method !== 'string' || !METHOD_RE.test(method) || method === 'shutdown') {
            return sendJson(res, 400, { detail: 'Invalid method' });
          }
          await ensureWorker();
          const result = await client.request(method, params && typeof params === 'object' ? params : {});
          sendJson(res, 200, { result });
        } catch (error) {
          const status = error && error.status;
          sendJson(res, status || 500, { status: status || null, detail: (error && error.detail) ?? (error && error.message) ?? 'Request failed' });
        }
      });
      server.middlewares.use('/__agentos/stream', async (req, res) => {
        const refused = guardRequest(req, { requireJson: false });
        if (refused) return sendJson(res, refused.status, { detail: refused.detail });
        try {
          const url = new URL(req.url, 'http://localhost');
          await ensureWorker();
          const params = { topic: url.searchParams.get('topic'), id: url.searchParams.get('id') };
          const last = Number(url.searchParams.get('last') || 0);
          if (last > 0) params.last_event_id = last;
          // Opt in to driver events (live text, tool items); anything else keeps the legacy status-only stream.
          if (url.searchParams.get('format') === 'normalised-v1') params.event_format = 'normalised-v1';
          await client.request('stream.open', params, 15000, ({ stream_id: streamId }) => {
            res.writeHead(200, { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache', Connection: 'keep-alive' });
            res.write('retry: 2000\n\n');
            sseClients.set(streamId, res);
            res.on('close', () => {
              if (sseClients.delete(streamId)) client.request('stream.close', { stream_id: streamId }, 5000).catch(() => {});
            });
          });
        } catch (error) {
          sendJson(res, (error && error.status) || 500, { detail: (error && error.detail) ?? (error && error.message) ?? 'Stream failed' });
        }
      });
    },
  };
}
