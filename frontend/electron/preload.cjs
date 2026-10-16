// AgentOS — Electron preload.
//
// CommonJS on purpose (frontend/package.json is "type": "module").
// Exposes only the API base URL passed by main.cjs via additionalArguments:
//   "--agentos-api=http://127.0.0.1:<port>/api"
// so the renderer can fall back to a relative "/api" when running in a plain
// browser (where this preload never runs and the key stays undefined).
'use strict';

const { contextBridge } = require('electron');

const API_ARG_PREFIX = '--agentos-api=';

/**
 * @param {string[]} argv
 * @returns {string | null}
 */
function readApiBase(argList) {
  for (const arg of argList) {
    if (typeof arg !== 'string' || !arg.startsWith(API_ARG_PREFIX)) continue;
    const value = arg.slice(API_ARG_PREFIX.length).trim();
    if (value) return value;
  }
  return null;
}

const apiUrl = readApiBase(process.argv);

if (typeof apiUrl === 'string' && apiUrl.length > 0) {
  contextBridge.exposeInMainWorld('__AGENTOS_API__', apiUrl);
}

contextBridge.exposeInMainWorld('__AGENTOS_DESKTOP__', true);
