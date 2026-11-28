'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function bridge(invoke) {
  let api;
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, 'preload.cjs'), 'utf8'), {
    require: () => ({
      ipcRenderer: { on() {}, invoke },
      contextBridge: { exposeInMainWorld(name, value) { if (name === 'agentos') api = value; } },
    }),
  });
  return api;
}

test('saveBackup exposes a no-argument IPC call and returns its result', async () => {
  const result = { saved: true, path: '/chosen/export.db', size: 123 };
  const api = bridge(async (...args) => {
    assert.deepEqual(args, ['agentos:backup-save']);
    return result;
  });
  assert.equal(await api.saveBackup('/ignored/source'), result);
});

test('saveBackup uses the existing bridge error format', async () => {
  const api = bridge(async () => {
    throw new Error(`Error invoking remote method 'agentos:backup-save': Error: {"status":409,"detail":"Busy"}`);
  });
  await assert.rejects(api.saveBackup(), (error) => {
    assert.deepEqual(JSON.parse(error.message), { status: 409, detail: 'Busy' });
    return true;
  });
});

test('openStream passes the event format to the main process', async () => {
  const seen = [];
  const api = bridge(async (...args) => { seen.push(args); return 'sid'; });
  await api.openStream('run', 'r1', 0, () => {}, 'normalised-v1');
  await api.openStream('run', 'r2', 0, () => {});
  assert.deepEqual(seen[0], ['agentos:stream-open', 'run', 'r1', 0, 'normalised-v1']);
  assert.equal(seen[1][4], undefined);
});
