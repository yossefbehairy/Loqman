// Loqman — Electron preload.
//
// CommonJS on purpose (frontend/package.json is "type": "module").
// Exposes a NARROW bridge to the Python worker owned by the main process:
//   window.agentos.call(method, params)                   -> Promise<result>
//   window.agentos.openStream(topic, id, lastId, onMsg)   -> Promise<{ close() }>
// No raw ipcRenderer, no arbitrary channel, no HTTP/port/token: the main process
// validates the sender and the method name, and never exposes `shutdown`.
'use strict';

const { contextBridge, ipcRenderer } = require('electron');

/** @type {Map<string, (msg: object) => void>} */
const handlers = new Map();
/** Messages that arrive before the renderer registered its handler (IPC ordering is not guaranteed). */
const early = new Map();

ipcRenderer.on('agentos:stream', (_event, msg) => {
  const handler = handlers.get(msg.stream_id);
  if (handler) {
    handler(msg);
    if (msg.type === 'stream_end') handlers.delete(msg.stream_id);
    return;
  }
  const queue = early.get(msg.stream_id) || [];
  if (queue.length < 256) queue.push(msg);
  early.set(msg.stream_id, queue);
});

/**
 * Main throws Error(JSON.stringify({status, detail})). contextBridge only carries an Error's
 * message across the isolated world, so the payload stays JSON in `message`; the renderer
 * (src/api/transport.ts) parses it back into ApiError(status, detail).
 */
function unwrap(error) {
  const raw = error && error.message ? String(error.message) : String(error);
  const marker = raw.indexOf('{"status"');
  if (marker >= 0) {
    try {
      JSON.parse(raw.slice(marker));
      return new Error(raw.slice(marker));
    } catch {
      /* fall through */
    }
  }
  return new Error(JSON.stringify({ status: null, detail: raw.replace(/^Error invoking remote method '[^']+': (Error: )?/, '') }));
}

contextBridge.exposeInMainWorld('agentos', {
  call: async (method, params) => {
    try {
      return await ipcRenderer.invoke('agentos:call', method, params || {});
    } catch (error) {
      throw unwrap(error);
    }
  },
  openStream: async (topic, id, lastEventId, onMessage) => {
    let streamId;
    try {
      streamId = await ipcRenderer.invoke('agentos:stream-open', topic, id, lastEventId || 0);
    } catch (error) {
      throw unwrap(error);
    }
    handlers.set(streamId, onMessage);
    for (const msg of early.get(streamId) || []) {
      onMessage(msg);
      if (msg.type === 'stream_end') handlers.delete(streamId);
    }
    early.delete(streamId);
    return {
      close: () => {
        handlers.delete(streamId);
        early.delete(streamId);
        return ipcRenderer.invoke('agentos:stream-close', streamId).catch(() => false);
      },
    };
  },
});

contextBridge.exposeInMainWorld('__AGENTOS_DESKTOP__', true);
