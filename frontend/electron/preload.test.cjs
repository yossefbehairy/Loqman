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
