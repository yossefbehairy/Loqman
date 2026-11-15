'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const flush = () => new Promise(setImmediate);

function runtime({ lock = true, ready = Promise.resolve(), saveBackup = async () => ({ saved: true }) } = {}) {
  const windows = [], workers = [], timers = [], handlers = {};
  const app = new EventEmitter();
  Object.assign(app, {
    isPackaged: false, setAppUserModelId() {}, setName() {}, getPath: (name) => `/user/${name}`,
    requestSingleInstanceLock: () => lock, whenReady: () => Promise.resolve(),
    quit() { this.quits = (this.quits || 0) + 1; },
  });
  class Window extends EventEmitter {
    constructor(options) {
      super(); this.options = options; this.visible = options.show;
      this.webContents = new EventEmitter();
      Object.assign(this.webContents, {
        isDestroyed: () => !!this.destroyed, send() {}, executeJavaScript: async () => {},
        setWindowOpenHandler(handler) { this.open = handler; },
      });
      windows.push(this);
    }
    isDestroyed() { return !!this.destroyed; }
    isVisible() { return this.visible; }
    isMinimized() { return false; }
    show() { this.visible = true; }
    focus() { this.focused = true; }
    destroy() { this.destroyed = true; this.webContents.emit('destroyed'); this.emit('closed'); }
    async loadFile() {}
    async loadURL() {}
  }
  class Worker extends EventEmitter {
    constructor() { super(); this.ready = ready; this.calls = []; workers.push(this); }
    start() { return this.ready; }
    request(...args) { this.calls.push(args); return Promise.resolve({ closed: true }); }
    stop() { return new Promise((resolve) => { this.finishStop = resolve; }); }
    kill() { this.killed = true; }
  }
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, 'main.cjs'), 'utf8'), {
    __dirname, console: { log() {}, error() {} },
    process: Object.assign(new EventEmitter(), { env: {}, platform: 'win32', exit() {} }),
    setTimeout(fn, ms) { const timer = { fn, ms, unref() {} }; timers.push(timer); return timer; },
    clearTimeout() {},
    require(name) {
      if (name === 'electron') return {
        app, BrowserWindow: Window, Menu: { buildFromTemplate() {}, setApplicationMenu() {} },
        dialog: { showSaveDialog: async () => ({}), showMessageBox: async () => ({ response: 1 }), showErrorBox() {} },
        shell: { openExternal: async () => {} }, ipcMain: { handle(name, fn) { handlers[name] = fn; } },
      };
      if (name === './worker-client.cjs') return { WorkerClient: Worker,
        METHOD_RE: /^[a-z][a-z0-9_.]{0,63}$/, workerCommand: () => ({ command: 'mock', args: [], options: {} }) };
      if (name === './backup.cjs') return { saveBackup };
      return require(name);
    },
  });
  return { app, windows, workers, timers, handlers };
}

test('a second process does not start a worker or register IPC', async () => {
  const r = runtime({ lock: false }); await flush();
  assert.equal(r.app.quits, 1);
  assert.equal(r.workers.length, 0);
  assert.equal(Object.keys(r.handlers).length, 0);
});

test('second instance focuses the existing window without spawning another worker', async () => {
  const r = runtime(); await flush();
  r.app.emit('second-instance');
  assert.equal(r.workers.length, 1);
  assert.equal(r.windows[1].focused, true);
});

test('ready-to-show and fallback both close the splash', async () => {
  for (const fallback of [false, true]) {
    const r = runtime(); await flush();
    if (fallback) r.timers.find((timer) => timer.ms === 3000).fn();
    else r.windows[1].emit('ready-to-show');
    assert.equal(r.windows[0].destroyed, true);
    assert.equal(r.windows[1].visible, true);
  }
});

test('startup failure offers the failure dialog and honors Quit without creating a main window', async () => {
  const r = runtime({ ready: Promise.reject(new Error('spawn failed')) });
  await flush();
  assert.equal(r.windows.length, 1);
  assert.equal(r.app.quits, 1);
});

test('repeated quit waits for the in-flight worker stop; second instance cannot reboot during quit', async () => {
  const r = runtime(); await flush();
  let prevented = 0;
  const event = { preventDefault() { prevented++; } };
  r.app.emit('before-quit', event);
  r.app.emit('before-quit', event);
  assert.equal(prevented, 2);
  for (const win of r.windows) win.destroy();
  r.app.emit('second-instance');
  assert.equal(r.workers.length, 1);
  r.workers[0].finishStop(); await flush();
  assert.equal(r.workers[0].killed, true);
  assert.equal(r.app.quits, 1);
  r.app.emit('before-quit', event);
  assert.equal(prevented, 2);
});

test('backup IPC checks the sender, takes no source argument, and binds its worker', async () => {
  let deps;
  const r = runtime({ saveBackup: async (value) => { deps = value; return { saved: false, cancelled: true }; } });
  await flush();
  const save = r.handlers['agentos:backup-save'];
  await assert.rejects(save({ sender: {} }), /Unauthorized/);
  const sender = r.windows[1].webContents;
  const result = await save({ sender }, '/renderer/source.db');
  assert.equal(result.cancelled, true);
  assert.equal(deps.documentsPath, '/user/documents');
  assert.equal('source' in deps, false);
  await deps.call('backup.cleanup', { path: '/worker/temp' });
  assert.equal(r.workers[0].calls.at(-1)[0], 'backup.cleanup');
  for (const method of ['shutdown', 'backup.create', 'backup.cleanup', 'stream.open', 'stream.close']) {
    await assert.rejects(r.handlers['agentos:call']({ sender }, method, {}), /Invalid method/);
  }
});

test('stream-close requires ownership and renderer teardown releases owned streams', async () => {
  const r = runtime(); await flush();
  const sender = r.windows[1].webContents;
  const worker = r.workers[0];
  worker.request = async (method, params, _timeout, onResult) => {
    worker.calls.push([method, params]);
    if (onResult) onResult({ stream_id: 'owned' });
    return { stream_id: 'owned', closed: true };
  };
  assert.equal(await r.handlers['agentos:stream-close']({ sender }, 'unknown'), false);
  assert.equal(worker.calls.length, 0);
  await r.handlers['agentos:stream-open']({ sender }, 'run', 'one', 0);
  r.windows[1].destroy();
  assert.equal(worker.calls.at(-1)[0], 'stream.close');
  assert.equal(worker.calls.at(-1)[1].stream_id, 'owned');
});

test('stream opening across a renderer reload closes instead of retaining an orphan owner', async () => {
  const r = runtime(); await flush();
  const sender = r.windows[1].webContents;
  const worker = r.workers[0];
  let finish;
  worker.request = (method, params, _timeout, onResult) => {
    worker.calls.push([method, params]);
    if (!onResult) return Promise.resolve({ closed: true });
    return new Promise((resolve) => { finish = () => {
      onResult({ stream_id: 'late' }); resolve({ stream_id: 'late' });
    }; });
  };
  const opening = r.handlers['agentos:stream-open']({ sender }, 'run', 'one', 0);
  sender.emit('did-start-navigation', {}, 'file:///app', false, true);
  finish(); await opening;
  assert.equal(worker.calls.at(-1)[0], 'stream.close');
  assert.equal(await r.handlers['agentos:stream-close']({ sender }, 'late'), false);
});
