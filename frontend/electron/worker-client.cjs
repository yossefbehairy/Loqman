// Loqman — client for the Python worker (JSON Lines over stdin/stdout, protocol
// "agentos-worker/1"; see docs/PYWORKER.md). Used by the Electron main process
// and by the Vite dev bridge. CommonJS on purpose (package.json is "type": "module").
'use strict';

const { EventEmitter } = require('node:events');
const { spawn } = require('node:child_process');

const MAX_LINE_CHARS = 64 * 1024 * 1024; // responses (e.g. base64 artifacts) can be large
const METHOD_RE = /^[a-z][a-z0-9_.]{0,63}$/;

class WorkerError extends Error {
  constructor(error) {
    super((error && (typeof error.detail === 'string' ? error.detail : error.message)) || 'Request failed');
    this.name = 'WorkerError';
    this.code = error && error.code;
    this.status = error && error.status;
    this.detail = error && error.detail !== undefined ? error.detail : error && error.message;
  }
}

/**
 * Events: 'stream' (msg), 'stream_end' (msg), 'log' (line), 'exit' ({code, signal}).
 * `ready` resolves with the worker's ready message, or rejects on fatal/exit.
 */
class WorkerClient extends EventEmitter {
  constructor({ spawnChild = spawn } = {}) {
    super();
    this.spawnChild = spawnChild;
    this.child = null;
    this.nextId = 1;
    this.pending = new Map();
    this.buffer = '';
    this.readyInfo = null;
    this.exited = false;
    this.ready = null;
  }

  start({ command, args, options }) {
    this.exited = false;
    this.readyInfo = null;
    let settle;
    this.ready = new Promise((resolve, reject) => {
      settle = { resolve, reject };
    });
    this.ready.catch(() => {}); // callers handle it; avoid an unhandled rejection
    const fail = (err) => {
      this.exited = true;
      settle.reject(err);
      this._failAll(err);
    };
    try {
      this.child = this.spawnChild(command, args, { ...options, stdio: ['pipe', 'pipe', 'pipe'] });
    } catch (error) {
      fail(error);
      return this.ready;
    }
    // Writable streams emit errors in addition to invoking write callbacks.
    this.child.stdin.on('error', (err) => this._failAll(err));

    this.child.stdout.setEncoding('utf8');
    this.child.stdout.on('data', (chunk) => {
      this.buffer += chunk;
      if (this.buffer.length > MAX_LINE_CHARS && !this.buffer.includes('\n')) {
        this.buffer = ''; // runaway line: drop it rather than exhaust memory
        return;
      }
      let idx;
      while ((idx = this.buffer.indexOf('\n')) >= 0) {
        const line = this.buffer.slice(0, idx).trim();
        this.buffer = this.buffer.slice(idx + 1);
        if (line) this._onLine(line, settle);
      }
    });
    this.child.stderr.setEncoding('utf8');
    this.child.stderr.on('data', (chunk) => {
      for (const line of chunk.split(/\r?\n/)) if (line) this.emit('log', line);
    });
    this.child.once('error', (err) => {
      this.emit('log', `failed to spawn worker: ${err.message}`);
      fail(err);
    });
    this.child.once('exit', (code, signal) => {
      this.exited = true;
      const err = new Error(`Worker exited (code ${code === null ? 'n/a' : code}, signal ${signal || 'none'})`);
      settle.reject(err);
      this._failAll(err);
      this.emit('exit', { code, signal });
    });
    return this.ready;
  }

  _onLine(line, settle) {
    let msg;
    try {
      msg = JSON.parse(line);
    } catch {
      this.emit('log', `non-protocol stdout line ignored: ${line.slice(0, 120)}`);
      return;
    }
    switch (msg.type) {
      case 'ready':
        this.readyInfo = msg;
        settle.resolve(msg);
        break;
      case 'fatal':
        settle.reject(new WorkerError(msg.error));
        break;
      case 'response': {
        const entry = this.pending.get(msg.id);
        if (!entry) return;
        this.pending.delete(msg.id);
        clearTimeout(entry.timer);
        if (msg.error) entry.reject(new WorkerError(msg.error));
        else {
          try {
            // Install stream ownership before parsing the next line in this chunk.
            if (entry.onResult) entry.onResult(msg.result);
            entry.resolve(msg.result);
          } catch (error) {
            entry.reject(error);
          }
        }
        break;
      }
      case 'stream':
        this.emit('stream', msg);
        break;
      case 'stream_end':
        this.emit('stream_end', msg);
        break;
      default:
        break;
    }
  }

  _failAll(err) {
    for (const [, entry] of this.pending) {
      clearTimeout(entry.timer);
      entry.reject(err);
    }
    this.pending.clear();
  }

  /** @returns {Promise<any>} the method's `result`; rejects with WorkerError(status, detail). */
  request(method, params = {}, timeoutMs = 10 * 60 * 1000, onResult) {
    if (typeof method !== 'string' || !METHOD_RE.test(method)) {
      return Promise.reject(new Error('Invalid method name'));
    }
    if (!this.child || this.exited || !this.child.stdin.writable) {
      return Promise.reject(new Error('Backend is not running'));
    }
    const id = this.nextId++;
    let line;
    try {
      line = JSON.stringify({ id, method, params }) + '\n';
    } catch (error) {
      return Promise.reject(error);
    }
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new Error(`Backend request timed out: ${method}`));
      }, timeoutMs);
      this.pending.set(id, { resolve, reject, timer, onResult });
      const onWrite = (err) => {
        if (err) {
          this.pending.delete(id);
          clearTimeout(timer);
          reject(err);
        }
      };
      try {
        this.child.stdin.write(line, onWrite);
      } catch (error) {
        onWrite(error);
      }
    });
  }

  /** Graceful stop: ask the worker to shut down, then kill it if it lingers. */
  async stop(graceMs = 6000) {
    const child = this.child;
    if (!child || this.exited) return;
    let onExit;
    const done = new Promise((resolve) => {
      onExit = resolve;
      child.once('exit', onExit);
    });
    try {
      this.request('shutdown', {}, 3000).catch(() => {});
      child.stdin.end();
    } catch {
      /* already closed */
    }
    let timer;
    try {
      await Promise.race([done, new Promise((resolve) => {
        timer = setTimeout(() => { this.kill(); resolve(); }, graceMs);
      })]);
    } finally {
      clearTimeout(timer);
      child.removeListener('exit', onExit);
    }
  }

  kill() {
    this._failAll(new Error('Backend stopped'));
    const child = this.child;
    if (!child || this.exited) return;
    if (process.platform === 'win32' && child.pid) {
      try {
        spawn('taskkill', ['/PID', String(child.pid), '/T', '/F'], { windowsHide: true })
          .on('error', (error) => this.emit('log', `taskkill failed: ${error.message}`));
      } catch {
        /* fall through */
      }
    }
    try {
      child.kill();
    } catch {
      /* gone */
    }
  }
}

/** Command used to launch the worker (dev: venv python; packaged: bundled exe). */
function workerCommand({ isDev, repoRoot, resourcesPath, dataDir, runtimeMode, extraEnv }) {
  const path = require('node:path');
  const fs = require('node:fs');
  const env = { ...process.env, ...(extraEnv || {}) };
  const modeArgs = runtimeMode ? ['--runtime-mode', runtimeMode] : [];
  if (isDev) {
    const venvPython = path.resolve(repoRoot, 'backend', '.venv', 'Scripts', 'python.exe');
    return {
      command: fs.existsSync(venvPython) ? venvPython : 'python',
      args: ['-m', 'backend.pyworker', '--data-dir', dataDir, ...modeArgs],
      options: { cwd: repoRoot, env, windowsHide: true },
    };
  }
  return {
    command: path.join(resourcesPath, 'agentos-backend.exe'),
    args: ['--data-dir', dataDir, ...modeArgs],
    options: { env, windowsHide: true },
  };
}

module.exports = { WorkerClient, WorkerError, workerCommand, METHOD_RE };
