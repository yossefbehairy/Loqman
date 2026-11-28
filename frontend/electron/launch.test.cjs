'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const { desktopEnvironment, launch } = require('./launch.cjs');

test('removes Electron Node mode case-insensitively without changing the parent environment', () => {
  const env = { ELECTRON_RUN_AS_NODE: '1', Electron_Run_As_Node: '1', PATH: 'keep', RUNTIME_MODE: 'mock' };
  assert.deepEqual(desktopEnvironment(env), { PATH: 'keep', RUNTIME_MODE: 'mock' });
  assert.equal(env.ELECTRON_RUN_AS_NODE, '1');
  assert.deepEqual(desktopEnvironment({ PATH: 'keep' }), { PATH: 'keep' });
});

test('launcher passes cleaned environment and arguments to the Electron executable', () => {
  launch(['.'], { ELECTRON_RUN_AS_NODE: '1', PATH: 'keep' }, (command, args, options) => {
    assert.equal(command, require('electron'));
    assert.deepEqual(args, ['.']);
    assert.deepEqual(options.env, { PATH: 'keep' });
    assert.equal(options.stdio, 'inherit');
    return new EventEmitter();
  });
});
