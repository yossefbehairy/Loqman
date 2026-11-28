'use strict';

function externalUrl(value) {
  if (typeof value !== 'string') return null;
  try {
    const url = new URL(value);
    return ['http:', 'https:'].includes(url.protocol) ? url.href : null;
  } catch {
    return null;
  }
}

function guardNavigation(webContents, openExternal) {
  const open = (value) => {
    const url = externalUrl(value);
    if (url) Promise.resolve().then(() => openExternal(url)).catch(console.error);
  };
  webContents.setWindowOpenHandler(({ url }) => {
    open(url);
    return { action: 'deny' };
  });
  // App loads/reloads use loadURL/loadFile and do not emit will-navigate.
  // Hash routing is same-document navigation and is unaffected.
  webContents.on('will-navigate', (event, url) => {
    event.preventDefault();
    open(url);
  });
  webContents.on('will-redirect', (event) => event.preventDefault());
}

module.exports = { externalUrl, guardNavigation };
