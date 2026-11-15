'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { configureSession, PACKAGED_CSP } = require('./security.cjs');
const { pathToFileURL } = require('node:url');
const path = require('node:path');
const fs = require('node:fs');

function setup(isPackaged) {
  const main = {};
  const session = {
    setPermissionCheckHandler(fn) { this.check = fn; },
    setPermissionRequestHandler(fn) { this.request = fn; },
    webRequest: { onHeadersReceived(filter, fn) { this.filter = filter; this.headers = fn; } },
  };
  const rendererFile = path.resolve(__dirname, '../dist/index.html');
  configureSession(session, { isPackaged, rendererFile, getMainContents: () => main });
  return { session, main, url: pathToFileURL(rendererFile).href };
}

test('both permission paths deny unused capabilities and limit clipboard writes to main renderer', () => {
  const { session, main } = setup(true);
  for (const contents of [main, {}, null]) {
    for (const permission of ['clipboard-sanitized-write', 'clipboard-read', 'media', 'notifications', 'geolocation', 'unknown', 'fileSystem', 'openExternal']) {
      const expected = contents === main && permission === 'clipboard-sanitized-write';
      assert.equal(session.check(contents, permission), expected);
      let result;
      session.request(contents, permission, (allowed) => { result = allowed; });
      assert.equal(result, expected);
    }
  }
});

test('packaged renderer CSP disables networking and inline scripts without breaking splash policy', () => {
  const { session, url } = setup(true);
  let response;
  session.webRequest.headers({ url: `${url}?ui=rusebot`, responseHeaders: { existing: ['kept'] } }, (r) => { response = r; });
  assert.equal(response.responseHeaders['Content-Security-Policy'][0], PACKAGED_CSP);
  assert.deepEqual(response.responseHeaders.existing, ['kept']);
  assert.match(PACKAGED_CSP, /connect-src 'none'/);
  assert.match(PACKAGED_CSP, /script-src 'self';/);
  assert.match(PACKAGED_CSP, /font-src 'self';/);
  session.webRequest.headers({ url: url.replace('/dist/index.html', '/electron/splash.html') }, (r) => { response = r; });
  assert.deepEqual(response, {});
  assert.equal(setup(false).session.webRequest.headers, undefined);
});

test('installer includes runtime Electron files and excludes tests and launcher', () => {
  const { build } = require('../package.json');
  const included = (file) => build.files.some((glob) => !glob.startsWith('!') && path.matchesGlob(file, glob)) &&
    !build.files.some((glob) => glob.startsWith('!') && path.matchesGlob(file, glob.slice(1)));
  for (const name of fs.readdirSync(__dirname)) {
    assert.equal(included(`electron/${name}`), !name.endsWith('.test.cjs') && name !== 'launch.cjs', name);
  }
});
