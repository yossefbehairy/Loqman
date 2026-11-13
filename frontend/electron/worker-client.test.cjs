'use strict';

const { test } = require('node:test');
const assert = require('node:assert/strict');
const { WorkerClient } = require('./worker-client.cjs');

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
