'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const { externalUrl, guardNavigation } = require('./navigation.cjs');

test('only valid absolute HTTP and HTTPS links can open externally', () => {
  for (const url of ['https://example.com/docs', 'http://example.com/download']) assert.equal(externalUrl(url), url);
  for (const url of ['file:///C:/secret', 'javascript:alert(1)', 'data:text/html,test', 'mailto:a@b.com', '//example.com', 'invalid', '', null, {}]) {
    assert.equal(externalUrl(url), null);
  }
});

test('popups are denied and navigation is prevented before opening the browser', async () => {
  const contents = new EventEmitter();
  contents.setWindowOpenHandler = (handler) => { contents.open = handler; };
  const opened = [];
  guardNavigation(contents, async (url) => opened.push(url));
  assert.deepEqual(contents.open({ url: 'https://example.com/' }), { action: 'deny' });
  assert.deepEqual(contents.open({ url: 'file:///secret' }), { action: 'deny' });
  let prevented = 0;
  const event = { preventDefault() { prevented++; } };
  contents.emit('will-navigate', event, 'http://example.com/');
  contents.emit('will-navigate', event, 'javascript:alert(1)');
  contents.emit('will-redirect', event, 'https://example.com/');
  await new Promise(setImmediate);
  assert.equal(prevented, 3);
  assert.deepEqual(opened, ['https://example.com/', 'http://example.com/']);
});
