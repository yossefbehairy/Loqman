'use strict';

const { test } = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const { pathToFileURL } = require('node:url');

const load = () => import(pathToFileURL(path.join(__dirname, '../vite-agentos-bridge.mjs')).href);

function handlers(plugin) {
  const routes = {};
  plugin.configureServer({ httpServer: null, middlewares: { use: (p, fn) => (routes[p] = fn) } });
  return routes;
}

function fakeRes() {
  const res = { statusCode: 0, headers: {}, body: '', setHeader(k, v) { this.headers[k] = v; }, end(b) { this.body = b; } };
  return res;
}

const own = { host: '127.0.0.1:5173', origin: 'http://127.0.0.1:5173', 'content-type': 'application/json' };

test('guardRequest: own origin, no Origin header and JSON content types pass', async () => {
  const { guardRequest } = await load();
  assert.equal(guardRequest({ headers: own }, { requireJson: true }), null);
  assert.equal(guardRequest({ headers: { host: '127.0.0.1:5173', 'content-type': 'application/json; charset=utf-8' } }, { requireJson: true }), null);
  assert.equal(guardRequest({ headers: { host: 'localhost:5173', origin: 'http://localhost:5173', 'sec-fetch-site': 'same-origin' } }, { requireJson: false }), null);
});

test('guardRequest: a foreign, null or malformed Origin is refused', async () => {
  const { guardRequest } = await load();
  for (const origin of ['http://evil.example', 'null', 'not a url', 'http://127.0.0.1:9999']) {
    const r = guardRequest({ headers: { ...own, origin } }, { requireJson: true });
    assert.equal(r && r.status, 403, origin);
  }
  assert.equal(guardRequest({ headers: { host: '127.0.0.1:5173', 'sec-fetch-site': 'cross-site' } }, { requireJson: false }).status, 403);
});

test('guardRequest: text/plain and form posts are refused for calls', async () => {
  const { guardRequest } = await load();
  for (const type of ['text/plain', 'text/plain;charset=UTF-8', 'application/x-www-form-urlencoded', undefined]) {
    const headers = { host: '127.0.0.1:5173' };
    if (type) headers['content-type'] = type;
    assert.equal(guardRequest({ headers }, { requireJson: true }).status, 415, String(type));
  }
});

test('the call and stream endpoints answer a cross-origin request without touching the worker', async () => {
  const { default: agentosBridge } = await load();
  const routes = handlers(agentosBridge());
  const evil = { host: '127.0.0.1:5173', origin: 'http://evil.example' };

  const call = fakeRes();
  await routes['/__agentos/call']({ method: 'POST', headers: { ...evil, 'content-type': 'text/plain' }, url: '/' }, call);
  assert.equal(call.statusCode, 403);

  const plain = fakeRes();
  await routes['/__agentos/call']({ method: 'POST', headers: { host: '127.0.0.1:5173', 'content-type': 'text/plain' }, url: '/' }, plain);
  assert.equal(plain.statusCode, 415);

  const stream = fakeRes();
  await routes['/__agentos/stream']({ method: 'GET', headers: evil, url: '/?topic=t&id=1' }, stream);
  assert.equal(stream.statusCode, 403);
});
