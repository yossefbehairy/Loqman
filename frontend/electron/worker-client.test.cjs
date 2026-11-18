'use strict';

const { test } = require('node:test');
const assert = require('node:assert/strict');
const { WorkerClient } = require('./worker-client.cjs');
const { EventEmitter } = require('node:events');

function fakeChild() {
  const child = new EventEmitter();
  for (const name of ['stdin', 'stdout', 'stderr']) {
    child[name] = new EventEmitter();
    child[name].setEncoding = () => {};
  }
  child.stdin.writable = true;
  child.stdin.write = (_line, callback) => callback();
  child.stdin.end = () => {};
  child.kill = () => {};
  return child;
}

test('spawn failures reject readiness, mark exited, and cannot stall stop', async () => {
  for (const synchronous of [false, true]) {
    const child = fakeChild();
    const client = new WorkerClient({ spawnChild() {
      if (synchronous) throw new Error('spawn failed');
      return child;
    } });
    const ready = client.start({ command: 'fake', args: [] });
    if (!synchronous) child.emit('error', new Error('spawn failed'));
    await assert.rejects(ready, /spawn failed/);
    assert.equal(client.exited, true);
    await assert.rejects(client.request('health'), /not running/);
    await client.stop(5);
  }
});

test('crash rejects every pending request', async () => {
  const child = fakeChild();
  const client = new WorkerClient({ spawnChild: () => child });
  client.start({ command: 'fake', args: [] });
  const pending = [client.request('health'), client.request('settings.get')];
  child.emit('exit', 1, null);
  for (const request of pending) await assert.rejects(request, /Worker exited/);
  assert.equal(client.pending.size, 0);
});

test('stdin errors reject requests without an unhandled error event', async () => {
  const child = fakeChild();
  const client = new WorkerClient({ spawnChild: () => child });
  client.start({ command: 'fake', args: [] });
  const request = client.request('health');
  child.stdin.emit('error', new Error('EPIPE'));
  await assert.rejects(request, /EPIPE/);
  assert.equal(client.pending.size, 0);
});

test('quit with in-flight requests is bounded even when kill emits no exit', async () => {
  const child = fakeChild();
  const client = new WorkerClient({ spawnChild: () => child });
  client.start({ command: 'fake', args: [] });
  const rejected = assert.rejects(client.request('health'), /Backend stopped/);
  await client.stop(5);
  await rejected;
  assert.equal(client.pending.size, 0);
});

test('serialization and synchronous write failures leave no pending request', async () => {
  const child = fakeChild();
  const client = new WorkerClient({ spawnChild: () => child });
  client.start({ command: 'fake', args: [] });
  const params = {}; params.self = params;
  await assert.rejects(client.request('health', params), /circular/i);
  child.stdin.write = () => { throw new Error('write failed'); };
  await assert.rejects(client.request('health'), /write failed/);
  assert.equal(client.pending.size, 0);
});

test('registers the owner before events and stream_end in the same chunk', async () => {
  const client = new WorkerClient();
  const owners = new Map();
  const delivered = [];
  client.child = { stdin: { writable: true, write(_line, callback) { callback(); } } };
  client.on('stream', (msg) => {
    assert.equal(owners.get(msg.stream_id), 'renderer');
    delivered.push(msg.event);
  });
  client.on('stream_end', (msg) => {
    assert.equal(owners.get(msg.stream_id), 'renderer');
    owners.delete(msg.stream_id);
    delivered.push('end');
  });
  const opening = client.request('stream.open', {}, 1000,
    ({ stream_id }) => owners.set(stream_id, 'renderer'));
  // The stdout parser processes these synchronously, before any await continuation.
  for (const msg of [
    { type: 'response', id: 1, result: { stream_id: 'one' } },
    { type: 'stream', stream_id: 'one', event: 'succeeded' },
    { type: 'stream_end', stream_id: 'one' },
  ]) client._onLine(JSON.stringify(msg), {});
  assert.equal((await opening).stream_id, 'one');
  assert.deepEqual(delivered, ['succeeded', 'end']);
  assert.equal(owners.size, 0);
});

test('failed opening does not register a stream owner', async () => {
  const client = new WorkerClient();
  client.child = { stdin: { writable: true, write(_line, callback) { callback(); } } };
  let called = false;
  const opening = client.request('stream.open', {}, 1000, () => { called = true; });
  client._onLine(JSON.stringify({ type: 'response', id: 1, error: { message: 'gone' } }), {});
  await assert.rejects(opening, /gone/);
  assert.equal(called, false);
});

test('delivers replay and immediate completion through a real child pipe', async () => {
  const client = new WorkerClient();
  const delivered = [];
  let owner;
  const ended = new Promise((resolve) => {
    client.on('stream', (msg) => {
      assert.equal(owner, msg.stream_id);
      delivered.push(msg.event);
    });
    client.on('stream_end', (msg) => {
      assert.equal(owner, msg.stream_id);
      owner = null;
      resolve();
    });
  });
  const script = `
    process.stdout.write(JSON.stringify({type:'ready'})+'\\n');
    require('node:readline').createInterface({input:process.stdin}).on('line', line => {
      const request = JSON.parse(line);
      if (request.method === 'shutdown') return process.exit(0);
      const messages = [
        {type:'response',id:request.id,result:{stream_id:'live'}},
        {type:'stream',stream_id:'live',event:'succeeded'},
        {type:'stream_end',stream_id:'live'}
      ];
      process.stdout.write(messages.map(msg=>JSON.stringify(msg)).join('\\n')+'\\n');
    });
  `;
  try {
    await client.start({command:process.execPath, args:['-e', script], options:{windowsHide:true}});
    await client.request('stream.open', {}, 2000, ({stream_id}) => { owner = stream_id; });
    await ended;
    assert.deepEqual(delivered, ['succeeded']);
    assert.equal(owner, null);
  } finally {
    await client.stop();
  }
});
