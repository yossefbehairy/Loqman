'use strict';

const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('typescript');

function transport(globals) {
  const source = fs.readFileSync(path.join(__dirname, '../src/api/transport.ts'), 'utf8');
  const compiled = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  const exports = {};
  vm.runInNewContext(compiled, { exports, ...globals });
  return exports;
}

test('completed desktop stream never reconnects even when it ends before open resolves', async () => {
  let opens = 0;
  let closes = 0;
  const timers = [];
  const api = transport({
    window: { agentos: { openStream: async (_topic, _id, _last, onMessage) => {
      opens += 1;
      await Promise.resolve();
      onMessage({ type: 'stream_end', reason: 'complete' });
      return { close() { closes += 1; } };
    } } },
    setTimeout(fn) { timers.push(fn); },
  });
  const stream = new api.WorkerEventSource('mission', 'finished');
  await new Promise(setImmediate);
  assert.equal(opens, 1);
  assert.equal(closes, 1);
  assert.equal(timers.length, 0);
  stream.close();
});

test('unexpected stream error schedules recovery and explicit close prevents retry', async () => {
  let opens = 0;
  const timers = [];
  const api = transport({
    window: { agentos: { openStream: async (_topic, _id, _last, onMessage) => {
      opens += 1;
      await Promise.resolve();
      onMessage({ type: 'stream_end', reason: 'error' });
      return { close() {} };
    } } },
    setTimeout(fn) { timers.push(fn); },
  });
  const stream = new api.WorkerEventSource('mission', 'broken');
  await new Promise(setImmediate);
  assert.equal(timers.length, 1);
  stream.close();
  timers[0]();
  await new Promise(setImmediate);
  assert.equal(opens, 1);
});

test('browser EventSource closes before forwarding explicit stream end', async () => {
  let instance;
  class EventSource {
    constructor() { instance = this; this.listeners = {}; this.closed = false; }
    addEventListener(name, fn) { this.listeners[name] = fn; }
    close() { this.closed = true; }
  }
  const api = transport({ window: {}, EventSource });
  let delivered = false;
  await api.openStream('run', 'one', () => {
    assert.equal(instance.closed, true);
    delivered = true;
  });
  instance.listeners.stream_end({ data: JSON.stringify({ type: 'stream_end', reason: 'complete' }) });
  assert.equal(delivered, true);
});

test('preload removes handlers when an early stream already ended', async () => {
  let bridge;
  let deliver;
  const ipcRenderer = {
    on(_name, fn) { deliver = fn; },
    async invoke(channel) {
      if (channel === 'agentos:stream-open') {
        deliver({}, { type: 'stream_end', stream_id: 'early' });
        return 'early';
      }
      return false;
    },
  };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, 'preload.cjs'), 'utf8'), {
    require() { return { contextBridge: { exposeInMainWorld(name, value) {
      if (name === 'agentos') bridge = value;
    } }, ipcRenderer }; },
  });
  let calls = 0;
  await bridge.openStream('run', 'one', 0, () => { calls += 1; });
  deliver({}, { type: 'stream', stream_id: 'early' });
  assert.equal(calls, 1);
});
