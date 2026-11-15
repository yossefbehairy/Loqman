// Loqman — Electron main process.
//
// CommonJS on purpose: frontend/package.json declares "type": "module", so
// every file under electron/ must use the .cjs extension.
'use strict';

const { app, BrowserWindow, Menu, dialog, shell, ipcMain, session } = require('electron');
const path = require('node:path');
const { WorkerClient, workerCommand, METHOD_RE } = require('./worker-client.cjs');
const { guardNavigation } = require('./navigation.cjs');
const { saveBackup } = require('./backup.cjs');
const { configureSession } = require('./security.cjs');
const fs = require('node:fs/promises');

const isDev = !app.isPackaged;

// Explicit renderer capabilities. Source-call coverage lives in main.test.cjs.
const RENDERER_METHODS = new Set([
  'approvals.decide', 'approvals.list',
  'ceo.actions', 'ceo.approve', 'ceo.ask', 'ceo.ask_progress', 'ceo.deny',
  'conversations.add_message', 'conversations.bind_profile', 'conversations.create',
  'conversations.import_ceo', 'conversations.list', 'conversations.messages',
  'deliverables.content', 'deliverables.create', 'deliverables.decide',
  'deliverables.get', 'deliverables.list', 'deliverables.list_all',
  'engines.capabilities', 'engines.check', 'engines.models', 'engines.open_setup',
  'engines.setup', 'engines.status', 'engines.test',
  'missions.cancel', 'missions.create', 'missions.delegate', 'missions.list',
  'models.free', 'preferences.get', 'preferences.set_default',
  'profiles.create', 'profiles.delete', 'profiles.list', 'profiles.update',
  'runs.cancel', 'runs.events', 'runs.get', 'runs.list', 'runs.start_turn',
  'usage.summary',
]);

app.setAppUserModelId('app.loqman.desktop');
// Keep userData deterministic across dev and packaged builds: %APPDATA%\Loqman
// (must run before the first getPath('userData') call).
app.setName('Loqman');

const DEV_SERVER_URL = process.env.AGENTOS_DEV_SERVER_URL;
const READY_TIMEOUT_MS = 45000;
const MAX_BACKEND_RESTARTS = 3;
const BACKEND_LOG_LIMIT = 40; // Rolling buffer size (lines).
const DIALOG_LOG_LINES = 10; // Backend lines shown in error dialogs.

// ---------------------------------------------------------------------------
// Module state
// ---------------------------------------------------------------------------

/** @type {BrowserWindow | null} */
let mainWindow = null;
/** @type {BrowserWindow | null} */
let splashWindow = null;
/** The Python worker (JSON Lines over stdin/stdout; no HTTP, no ports, no token). @type {WorkerClient | null} */
let worker = null;
/** stream_id -> webContents that opened it. @type {Map<string, Electron.WebContents>} */
const streamOwners = new Map();
const streamGenerations = new WeakMap();
let backendRestartCount = 0;
let restartingBackend = false;
let quitting = false;

/** @type {{ message: string, kind: string } | null} */
let lastSplashStatus = null;
/** Rolling buffer of backend output lines, already prefixed with "[backend]". @type {string[]} */
const backendLog = [];

// ---------------------------------------------------------------------------
// Small helpers
// ---------------------------------------------------------------------------

/** Appends one backend output line to the console + rolling buffer. */
function logBackend(rawLine) {
  const line = String(rawLine).replace(/\s+$/, '');
  if (!line.trim()) return;
  const entry = `[backend] ${line}`;
  console.log(entry);
  backendLog.push(entry);
  if (backendLog.length > BACKEND_LOG_LIMIT) {
    backendLog.splice(0, backendLog.length - BACKEND_LOG_LIMIT);
  }
}

function tailOfBackendLog(lines) {
  if (backendLog.length === 0) return '(no backend output captured)';
  return backendLog.slice(-lines).join('\n');
}

// ---------------------------------------------------------------------------
// Backend (Python worker) lifecycle
// ---------------------------------------------------------------------------

function spawnBackend() {
  const { command, args, options } = workerCommand({
    isDev,
    repoRoot: path.resolve(__dirname, '..', '..'),
    resourcesPath: process.resourcesPath,
    dataDir: app.getPath('userData'),
    runtimeMode: process.env.RUNTIME_MODE || undefined,
  });
  logBackend(`spawning: ${command} ${args.join(' ')}`);
  const child = new WorkerClient();
  worker = child;
  child.on('log', logBackend);
  child.on('stream', (msg) => {
    if (child !== worker) return;
    const owner = streamOwners.get(msg.stream_id);
    if (owner && !owner.isDestroyed()) owner.send('agentos:stream', msg);
  });
  child.on('stream_end', (msg) => {
    if (child !== worker) return;
    const owner = streamOwners.get(msg.stream_id);
    streamOwners.delete(msg.stream_id);
    if (owner && !owner.isDestroyed()) owner.send('agentos:stream', msg);
  });
  child.on('exit', ({ code, signal }) => {
    if (child !== worker) return; // Already cleaned up (shutdown/kill/restart).
    worker = null;
    streamOwners.clear();
    if (quitting) return; // Expected exit during shutdown.
    if (!mainWindow || mainWindow.isDestroyed()) return; // Startup/restart reports it.
    void promptBackendCrash(code, signal);
  });
  child.start({ command, args, options }).catch(() => {
    // Startup/restart flows notice via waitForBackend(); nothing else to do here.
  });
  return child;
}

/** Resolves when the worker printed its ready line; rejects on fatal/exit/timeout. */
async function waitForBackend() {
  const child = worker;
  if (!child) throw new Error('Backend process failed to start');
  let timer;
  const timeout = new Promise((_, reject) => {
    timer = setTimeout(
      () => reject(new Error(`Backend did not become ready within ${Math.round(READY_TIMEOUT_MS / 1000)}s`)),
      READY_TIMEOUT_MS,
    );
  });
  try {
    await Promise.race([child.ready, timeout]);
  } finally {
    clearTimeout(timer);
  }
}

/** Kills the worker process tree; safe to call multiple times. */
function killBackendTree() {
  const child = worker;
  worker = null; // Prevent the exit handler from treating this as a crash.
  streamOwners.clear();
  if (child) child.kill();
}

/** Renderer -> worker bridge. Only the main window may call; `shutdown` is never exposed. */
function registerIpc() {
  const fromMainWindow = (event) =>
    !!mainWindow && !mainWindow.isDestroyed() && event.sender === mainWindow.webContents;

  ipcMain.handle('agentos:call', async (event, method, params) => {
    if (!fromMainWindow(event)) throw new Error('Unauthorized caller');
    if (typeof method !== 'string' || !METHOD_RE.test(method) ||
        !RENDERER_METHODS.has(method)) {
      throw new Error('Invalid method: renderer method is not allowed');
    }
    if (!worker) throw new Error('Backend is not running');
    try {
      return await worker.request(method, params && typeof params === 'object' ? params : {});
    } catch (error) {
      // Errors cross IPC as plain data (status + detail), never as Error objects.
      const status = error && error.status;
      const detail = error && error.detail !== undefined ? error.detail : error && error.message;
      throw new Error(JSON.stringify({ status: status || null, detail: detail ?? 'Request failed' }));
    }
  });

  ipcMain.handle('agentos:backup-save', async (event) => {
    if (!fromMainWindow(event)) throw new Error('Unauthorized caller');
    if (!worker) throw new Error('Backend is not running');
    const owner = worker; // Cleanup must use the worker that created the backup.
    try {
      return await saveBackup({
        call: (method, params) => owner.request(method, params),
        dialog: { showSaveDialog: (options) => dialog.showSaveDialog(mainWindow, options) },
        fs, documentsPath: app.getPath('documents'),
      });
    } catch (error) {
      throw new Error(JSON.stringify({ status: error.status || null, detail: error.detail ?? error.message }));
    }
  });

  ipcMain.handle('agentos:stream-open', async (event, topic, id, lastEventId) => {
    if (!fromMainWindow(event)) throw new Error('Unauthorized caller');
    if (!worker) throw new Error('Backend is not running');
    const params = { topic, id };
    if (Number.isInteger(lastEventId) && lastEventId > 0) params.last_event_id = lastEventId;
    const owner = worker;
    const generation = streamGenerations.get(event.sender);
    try {
      const { stream_id: streamId } = await owner.request('stream.open', params, 15000,
        ({ stream_id }) => {
          if (event.sender.isDestroyed() || owner !== worker || quitting ||
              streamGenerations.get(event.sender) !== generation) {
            void owner.request('stream.close', { stream_id }, 5000).catch(() => {});
            return;
          }
          streamOwners.set(stream_id, event.sender);
        });
      return streamId;
    } catch (error) {
      throw new Error(JSON.stringify({ status: (error && error.status) || null, detail: (error && error.detail) || (error && error.message) || 'Stream failed' }));
    }
  });

  ipcMain.handle('agentos:stream-close', async (event, streamId) => {
    if (!fromMainWindow(event)) throw new Error('Unauthorized caller');
    if (typeof streamId !== 'string' || !worker || streamOwners.get(streamId) !== event.sender) return false;
    streamOwners.delete(streamId);
    try {
      return (await worker.request('stream.close', { stream_id: streamId }, 5000)).closed;
    } catch {
      return false;
    }
  });
}

// ---------------------------------------------------------------------------
// Splash window
// ---------------------------------------------------------------------------

function createSplash() {
  if (splashWindow && !splashWindow.isDestroyed()) return splashWindow;

  splashWindow = new BrowserWindow({
    width: 460,
    height: 300,
    resizable: false,
    frame: false,
    center: true,
    show: true,
    backgroundColor: '#121214',
    ...(isDev ? { icon: path.join(__dirname, '..', 'build', 'icon.ico') } : {}),
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  splashWindow.on('closed', () => {
    splashWindow = null;
  });
  splashWindow.webContents.on('did-finish-load', () => {
    applySplashStatus();
  });

  splashWindow.loadFile(path.join(__dirname, 'splash.html')).catch((error) => {
    console.error('Failed to load splash screen:', error);
  });

  return splashWindow;
}

function destroySplash() {
  if (splashWindow && !splashWindow.isDestroyed()) splashWindow.destroy();
  splashWindow = null;
}

/** Updates the splash status line (works even before the page has loaded). */
function setSplashStatus(message, kind = 'info') {
  lastSplashStatus = { message: String(message), kind };
  applySplashStatus();
}

function applySplashStatus() {
  if (!lastSplashStatus || !splashWindow || splashWindow.isDestroyed()) return;
  const payload = JSON.stringify(lastSplashStatus);
  splashWindow.webContents
    .executeJavaScript(`window.__setStatus && window.__setStatus(${payload});`)
    .catch(() => {
      // Splash gone or still navigating — did-finish-load reapplies.
    });
}

// ---------------------------------------------------------------------------
// Main window
// ---------------------------------------------------------------------------

function createMainWindow() {
  const win = new BrowserWindow({
    width: 1440,
    height: 900,
    minWidth: 1100,
    minHeight: 700,
    backgroundColor: '#121214',
    autoHideMenuBar: true,
    show: false,
    // Dev-only: packaged builds take the icon from the exe. build/ is the
    // electron-builder buildResources dir and is NOT packed into the asar.
    ...(isDev ? { icon: path.join(__dirname, '..', 'build', 'icon.ico') } : {}),
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      preload: path.join(__dirname, 'preload.cjs'),
    },
  });

  mainWindow = win;
  guardNavigation(win.webContents, (url) => shell.openExternal(url));
  const releaseStreams = () => {
    streamGenerations.set(win.webContents, (streamGenerations.get(win.webContents) || 0) + 1);
    for (const [id, owner] of streamOwners) {
      if (owner !== win.webContents) continue;
      streamOwners.delete(id);
      if (worker) void worker.request('stream.close', { stream_id: id }, 5000).catch(() => {});
    }
  };
  win.webContents.on('destroyed', releaseStreams);
  win.webContents.on('render-process-gone', releaseStreams);
  win.webContents.on('did-start-navigation', (_event, _url, inPlace, isMainFrame) => {
    if (isMainFrame && !inPlace) releaseStreams();
  });

  win.once('ready-to-show', () => {
    if (win.isDestroyed()) return;
    win.show();
    win.focus();
    destroySplash();
  });

  // Safety net: never let the app stay invisible if ready-to-show never fires.
  setTimeout(() => {
    if (!win.isDestroyed() && !win.isVisible()) {
      win.show();
      destroySplash();
    }
  }, 3000);

  win.on('closed', () => {
    if (mainWindow === win) mainWindow = null;
  });

  if (isDev && DEV_SERVER_URL) {
    // The vite dev server may still be starting up; retry a few times.
    let loadAttempts = 0;
    const loadDevServer = () => {
      if (win.isDestroyed()) return;
      loadAttempts += 1;
      win.loadURL(DEV_SERVER_URL).catch(() => {
        if (loadAttempts < 10 && !quitting) setTimeout(loadDevServer, 1000);
      });
    };
    loadDevServer();
  } else {
    win.loadFile(path.join(__dirname, '..', 'dist', 'index.html')).catch((error) => {
      dialog.showErrorBox('Loqman could not load the interface',
        `Could not load the application files. Run npm run build in the frontend folder.\n\n${error.message}`);
    });
  }

  return win;
}

// ---------------------------------------------------------------------------
// Menu
// ---------------------------------------------------------------------------

function buildMenu() {
  const template = [
    {
      label: 'File',
      submenu: [
        {
          label: 'Open Data Folder',
          click: () => {
            shell.openPath(app.getPath('userData')).then((error) => {
              if (error) console.error('Failed to open data folder:', error);
            });
          },
        },
        { type: 'separator' },
        { role: 'quit' },
      ],
    },
    ...(isDev ? [{
      label: 'View',
      submenu: [{ role: 'toggleDevTools' }],
    }] : []),
  ];
  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}

// ---------------------------------------------------------------------------
// Startup / crash / restart orchestration
// ---------------------------------------------------------------------------

function handleStartupFailure(error) {
  if (quitting) return;
  const message = error && error.message ? error.message : String(error);
  const detail = tailOfBackendLog(8);

  // Keep the splash open with the error + last backend lines, and give the
  // user a way out (Retry or Quit) instead of a stuck frameless window.
  setSplashStatus(`Startup failed:\n${message}\n\n${detail}`, 'error');

  void (async () => {
    const result = await dialog.showMessageBox({
      type: 'error',
      title: 'Loqman failed to start',
      message,
      detail: `Last backend output:\n${tailOfBackendLog(DIALOG_LOG_LINES)}`,
      buttons: ['Retry', 'Quit'],
      defaultId: 0,
      cancelId: 1,
      noLink: true,
    });
    if (quitting) return;
    if (result.response === 0) {
      await restartBackend();
    } else {
      quitting = true;
      app.quit();
    }
  })();
}

async function boot() {
  buildMenu();
  createSplash();
  setSplashStatus('Starting backend…');

  try {
    // A recovery re-boot (second-instance with no windows) keeps the healthy
    // backend that is already running instead of spawning a duplicate.
    if (!worker || worker.exited) {
      spawnBackend();
      setSplashStatus('Waiting for the backend…');
    }
    await waitForBackend();

    if (quitting) return;
    setSplashStatus('Backend ready — loading workspace…');
    createMainWindow();
  } catch (error) {
    handleStartupFailure(error);
  }
}

async function promptBackendCrash(code, signal) {
  if (quitting) return;

  const options = {
    type: 'error',
    title: 'Loqman backend stopped',
    message: `The Loqman backend exited unexpectedly (code ${code === null ? 'n/a' : code}, signal ${signal || 'none'}).`,
    detail: `Last backend output:\n${tailOfBackendLog(DIALOG_LOG_LINES)}`,
    buttons: ['Restart backend', 'Quit'],
    defaultId: 0,
    cancelId: 1,
    noLink: true,
  };

  const parent = mainWindow && !mainWindow.isDestroyed() ? mainWindow : null;
  const result = parent
    ? await dialog.showMessageBox(parent, options)
    : await dialog.showMessageBox(options);

  if (quitting) return;
  if (result.response === 0) {
    await restartBackend();
  } else {
    quitting = true;
    app.quit();
  }
}

async function restartBackend() {
  if (quitting) return;

  if (backendRestartCount >= MAX_BACKEND_RESTARTS) {
    quitting = true;
    dialog.showErrorBox(
      'Loqman backend keeps stopping',
      `The backend has already been restarted ${backendRestartCount} times. Loqman will now quit.\n\nLast backend output:\n${tailOfBackendLog(DIALOG_LOG_LINES)}`,
    );
    app.quit();
    return;
  }
  backendRestartCount += 1;
  restartingBackend = true;

  // Bring the splash back before tearing the main window down so the app is
  // never left with zero windows (which would fire window-all-closed).
  createSplash();
  setSplashStatus(
    `Backend stopped — restarting (${backendRestartCount}/${MAX_BACKEND_RESTARTS})…`,
  );

  const oldWindow = mainWindow;
  mainWindow = null;
  if (oldWindow && !oldWindow.isDestroyed()) oldWindow.destroy();

  // Kill any still-running backend (e.g. a process that never became healthy)
  // before spawning its replacement, so it can never be orphaned on quit.
  killBackendTree();

  try {
    spawnBackend();
    await waitForBackend();

    if (quitting) return;
    setSplashStatus('Backend ready — reloading workspace…');
    createMainWindow();
  } catch (error) {
    handleStartupFailure(error);
  } finally {
    restartingBackend = false;
  }
}

// ---------------------------------------------------------------------------
// App lifecycle
// ---------------------------------------------------------------------------

const hasSingleInstanceLock = app.requestSingleInstanceLock();

// Exit-trace diagnostics: a silent quit must never be undiscoverable again.
// Every exit path prints WHY it fired (all output lands in the app log).
for (const [signal, handler] of [
  ['uncaughtException', (error) => console.error('[exit-trace] uncaughtException:', error)],
  ['unhandledRejection', (reason) => console.error('[exit-trace] unhandledRejection:', reason)],
]) {
  process.on(signal, handler);
}
app.on('render-process-gone', (_event, details) => {
  console.error('[exit-trace] renderer gone:', JSON.stringify(details));
});
app.on('child-process-gone', (_event, details) => {
  console.error('[exit-trace] child process gone:', JSON.stringify(details));
});

if (!hasSingleInstanceLock) {
  // Another Loqman instance owns the app — hand over and exit.
  console.error('[exit-trace] single-instance lock NOT acquired — quitting immediately');
  app.quit();
} else {
  app.on('second-instance', () => {
    if (quitting) return;
    const target = mainWindow && !mainWindow.isDestroyed() ? mainWindow : splashWindow;
    if (target && !target.isDestroyed()) {
      if (target.isMinimized()) target.restore();
      target.show();
      target.focus();
      return;
    }
    // Windowless instance still holding the lock (a stalled quit): recover by
    // booting the workspace again instead of ignoring every future launch.
    console.error('[exit-trace] second-instance with no windows — rebooting UI');
    void boot();
  });

  registerIpc();
  app.whenReady().then(() => {
    configureSession(session.defaultSession, {
      isPackaged: app.isPackaged,
      rendererFile: path.join(__dirname, '..', 'dist', 'index.html'),
      getMainContents: () => mainWindow && !mainWindow.isDestroyed() ? mainWindow.webContents : null,
    });
    return boot();
  });

  app.on('window-all-closed', () => {
    console.error('[exit-trace] window-all-closed firing (all windows are gone)');
    if (restartingBackend) return; // Restart flow briefly recreates windows.
    if (process.platform !== 'darwin') {
      quitting = true;
      app.quit(); // before-quit stops the worker gracefully
    }
  });

  let stoppingWorker = false;
  app.on('before-quit', (event) => {
    quitting = true;
    if (stoppingWorker) {
      event.preventDefault();
      return;
    }
    // Graceful: the worker persists in-flight runs as interrupted before exiting.
    if (worker && !stoppingWorker) {
      stoppingWorker = true;
      event.preventDefault();
      const closing = worker;
      worker = null;
      streamOwners.clear();
      closing.stop(5000).catch(() => {}).finally(() => {
        closing.kill();
        stoppingWorker = false;
        app.quit();
      });
    }
    // Hard-exit watchdog: a stalled graceful quit used to leave the main
    // process alive while holding the single-instance lock — every later
    // launch then exited silently ("the app won't open"). Never again.
    setTimeout(() => {
      console.error('[exit-trace] graceful quit stalled — forcing process exit');
      process.exit(0);
    }, 9000).unref();
  });
}
