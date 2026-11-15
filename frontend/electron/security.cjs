'use strict';
const { pathToFileURL } = require('node:url');

const PACKAGED_CSP = "default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self'; connect-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'; frame-src 'none'; frame-ancestors 'none'";

function configureSession(session, { isPackaged, rendererFile, getMainContents }) {
  // Copy buttons only write text. Never grant clipboard-read or clipboard-read/write.
  const allowed = (contents, permission) => !!contents && contents === getMainContents() &&
    permission === 'clipboard-sanitized-write';
  session.setPermissionCheckHandler((contents, permission) => allowed(contents, permission));
  session.setPermissionRequestHandler((contents, permission, callback) => callback(allowed(contents, permission)));
  if (isPackaged) {
    const rendererUrl = pathToFileURL(rendererFile).href;
    session.webRequest.onHeadersReceived({ urls: ['file:///*'] }, (details, callback) => {
      const url = new URL(details.url);
      url.search = ''; url.hash = '';
      if (url.href !== rendererUrl) return callback({});
      callback({ responseHeaders: { ...details.responseHeaders, 'Content-Security-Policy': [PACKAGED_CSP] } });
    });
  }
}

module.exports = { configureSession, PACKAGED_CSP };
