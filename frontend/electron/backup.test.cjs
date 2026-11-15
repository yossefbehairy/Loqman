'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const { saveBackup } = require('./backup.cjs');

function fixture({ canceled = false, copyError, createError, dialogError, cleanupError } = {}) {
  const calls = [];
  return {
    calls,
    deps: {
      documentsPath: '/Documents',
      async call(method, params) {
        calls.push([method, params]);
        if (method === 'backup.create') {
          if (createError) throw createError;
          return { path: '/private/worker-backup.db', name: 'backup.db', size: 123 };
        }
        if (cleanupError) throw cleanupError;
      },
      dialog: { async showSaveDialog(options) {
        assert.equal(options.defaultPath, path.join('/Documents', 'backup.db'));
        if (dialogError) throw dialogError;
        return { canceled, filePath: '/chosen/export.db' };
      } },
      fs: { async copyFile(source, destination) {
        calls.push(['copy', source, destination]);
        if (copyError) throw copyError;
      } },
    },
  };
}

test('backup success copies only the newly returned worker path and cleans it', async () => {
  const { calls, deps } = fixture();
  assert.deepEqual(await saveBackup(deps), { saved: true, path: '/chosen/export.db', size: 123 });
  assert.deepEqual(calls, [
    ['backup.create', {}], ['copy', '/private/worker-backup.db', '/chosen/export.db'],
    ['backup.cleanup', { path: '/private/worker-backup.db' }],
  ]);
});

test('cancel cleans up without copying', async () => {
  const { calls, deps } = fixture({ canceled: true });
  assert.deepEqual(await saveBackup(deps), { saved: false, cancelled: true });
  assert.deepEqual(calls.map(([method]) => method), ['backup.create', 'backup.cleanup']);
});

for (const kind of ['copyError', 'dialogError']) {
  test(`${kind} still cleans up and preserves the original error`, async () => {
    const failure = new Error(kind);
    const { calls, deps } = fixture({ [kind]: failure, cleanupError: new Error('cleanup') });
    await assert.rejects(saveBackup(deps), (error) => error === failure);
    assert.deepEqual(calls.at(-1), ['backup.cleanup', { path: '/private/worker-backup.db' }]);
  });
}

test('worker create error propagates without opening a dialog or guessing a cleanup path', async () => {
  const failure = new Error('worker unavailable');
  const { calls, deps } = fixture({ createError: failure });
  deps.dialog.showSaveDialog = () => assert.fail('unexpected dialog');
  await assert.rejects(saveBackup(deps), (error) => error === failure);
  assert.deepEqual(calls, [['backup.create', {}]]);
});

test('cleanup failure is reported after a successful copy', async () => {
  const { deps } = fixture({ cleanupError: new Error('cleanup failed') });
  await assert.rejects(saveBackup(deps), /cleanup failed/);
});
