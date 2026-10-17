// AgentOS — Electron main process.
//
// CommonJS on purpose: frontend/package.json declares "type": "module", so
// every file under electron/ must use the .cjs extension.
'use strict';

const { app, BrowserWindow, Menu, dialog, shell } = require('electron');
const { spawn } = require('node:child_process');
const fs = require('node:fs');
const net = require('node:net');
const path = require('node:path');

const isDev = !app.isPackaged;

app.setAppUserModelId('com.agentos.desktop');
// Keep userData deterministic across dev and packaged builds: %APPDATA%\AgentOS
// (must run before the first getPath('userData') call).
app.setName('AgentOS');

const DEV_SERVER_URL = 'http://127.0.0.1:5173';
const HEALTH_TIMEOUT_MS = 45000;
const HEALTH_POLL_MS = 300;
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
/** @type {import('node:child_process').ChildProcess | null} */
let backendProcess = null;
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

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

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

/** Splits a child-process stream into lines and feeds logBackend. */
function pipeBackendOutput(stream) {
  if (!stream) return;
  stream.setEncoding('utf8');
  let pending = '';
  stream.on('data', (chunk) => {
    pending += chunk;
    let newlineIndex;
    while ((newlineIndex = pending.indexOf('\n')) !== -1) {
      logBackend(pending.slice(0, newlineIndex));
      pending = pending.slice(newlineIndex + 1);
    }
  });
  stream.on('end', () => {
    if (pending) logBackend(pending);
  });
}

function tailOfBackendLog(lines) {
  if (backendLog.length === 0) return '(no backend output captured)';
  return backendLog.slice(-lines).join('\n');
}

/** Asks the OS for a free TCP port on 127.0.0.1. @returns {Promise<number>} */
function getFreePort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.unref();
    server.once('error', reject);
    server.listen(0, '127.0.0.1', () => {
      const address = server.address();
      const port = address && typeof address === 'object' ? address.port : null;
      server.close(() => {
        if (port) resolve(port);
        else reject(new Error('Could not determine a free TCP port'));
      });
    });
  });
}

// ---------------------------------------------------------------------------
// Backend process lifecycle
// ---------------------------------------------------------------------------

function resolveBackendCommand(port) {
  if (isDev) {
    const repoRoot = path.resolve(__dirname, '..', '..');
    const venvPython = path.resolve(repoRoot, 'backend', '.venv', 'Scripts', 'python.exe');
    const pythonExe = fs.existsSync(venvPython) ? venvPython : 'python';
    return {
      command: pythonExe,
      args: ['-m', 'backend.app.cli', '--port', String(port)],
      options: {
        cwd: repoRoot,
        env: { ...process.env },
        windowsHide: true,
      },
    };
  }
  const backendExe = path.join(process.resourcesPath, 'agentos-backend.exe');
  return {
    command: backendExe,
    args: ['--port', String(port), '--data-dir', app.getPath('userData')],
    options: { windowsHide: true },
  };
}

function spawnBackend(port) {
  const { command, args, options } = resolveBackendCommand(port);
  logBackend(`spawning: ${command} ${args.join(' ')}`);

  const child = spawn(command, args, options);
  backendProcess = child;

  pipeBackendOutput(child.stdout);
  pipeBackendOutput(child.stderr);

  child.once('error', (error) => {
    logBackend(`failed to spawn backend: ${error.message}`);
    if (child === backendProcess) backendProcess = null;
    // Startup/restart flows notice via waitForBackend(); there is nothing to
    // kill and no crash dialog to show here.
  });

  child.once('exit', (code, signal) => {
    if (child !== backendProcess) return; // Already cleaned up (shutdown/kill).
    backendProcess = null;
    if (quitting) return; // Expected exit during shutdown.
    if (!mainWindow || mainWindow.isDestroyed()) return; // Startup/restart reports it.
    void promptBackendCrash(code, signal);
  });

  return child;
}

/** Polls GET /api/health until the backend answers or the timeout elapses. */
async function waitForBackend(port) {
  const url = `http://127.0.0.1:${port}/api/health`;
  const deadline = Date.now() + HEALTH_TIMEOUT_MS;

  while (Date.now() < deadline) {
    if (quitting) throw new Error('App is quitting');

    const child = backendProcess;
    if (!child) throw new Error('Backend process failed to start');
    if (child.exitCode !== null) {
      throw new Error(`Backend exited before becoming healthy (exit code ${child.exitCode})`);
    }

    try {
      const response = await fetch(url, { signal: AbortSignal.timeout(2000) });
      if (response.ok) return;
    } catch {
      // Not listening yet — retry until the deadline.
    }
    await delay(HEALTH_POLL_MS);
  }

  throw new Error(
    `Backend did not become healthy within ${Math.round(HEALTH_TIMEOUT_MS / 1000)}s (${url})`,
  );
}

/** Kills the backend process tree; safe to call multiple times. */
function killBackendTree() {
  const child = backendProcess;
  backendProcess = null; // Prevent the exit handler from treating this as a crash.
  if (!child || child.exitCode !== null) return;

  if (process.platform === 'win32' && child.pid) {
    try {
      spawn('taskkill', ['/PID', String(child.pid), '/T', '/F'], { windowsHide: true });
    } catch (error) {
      console.error('[backend] taskkill failed:', error);
    }
  }
  try {
    child.kill(); // Fallback (and the primary path on non-Windows).
  } catch {
    // Process already gone.
  }
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

function createMainWindow(port) {
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
      additionalArguments: ['--agentos-api=http://127.0.0.1:' + port + '/api'],
    },
  });

  mainWindow = win;

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

  if (isDev) {
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
    win.loadFile(path.join(__dirname, '..', 'dist', 'index.html'));
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
    {
      label: 'View',
      submenu: [{ role: 'toggleDevTools' }],
    },
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
      title: 'AgentOS failed to start',
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
    const port = await getFreePort();
    spawnBackend(port);
    setSplashStatus(`Waiting for backend on 127.0.0.1:${port}…`);
    await waitForBackend(port);

    if (quitting) return;
    setSplashStatus('Backend ready — loading workspace…');
    createMainWindow(port);
  } catch (error) {
    handleStartupFailure(error);
  }
}

async function promptBackendCrash(code, signal) {
  if (quitting) return;

  const options = {
    type: 'error',
    title: 'AgentOS backend stopped',
    message: `The AgentOS backend exited unexpectedly (code ${code === null ? 'n/a' : code}, signal ${signal || 'none'}).`,
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
      'AgentOS backend keeps stopping',
      `The backend has already been restarted ${backendRestartCount} times. AgentOS will now quit.\n\nLast backend output:\n${tailOfBackendLog(DIALOG_LOG_LINES)}`,
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
    const port = await getFreePort();
    spawnBackend(port);
    await waitForBackend(port);

    if (quitting) return;
    setSplashStatus('Backend ready — reloading workspace…');
    createMainWindow(port);
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

if (!hasSingleInstanceLock) {
  // Another AgentOS instance owns the app — hand over and exit.
  app.quit();
} else {
  app.on('second-instance', () => {
    const target = mainWindow && !mainWindow.isDestroyed() ? mainWindow : splashWindow;
    if (target && !target.isDestroyed()) {
      if (target.isMinimized()) target.restore();
      target.show();
      target.focus();
    }
  });

  app.whenReady().then(boot);

  app.on('window-all-closed', () => {
    if (restartingBackend) return; // Restart flow briefly recreates windows.
    if (process.platform !== 'darwin') {
      quitting = true;
      killBackendTree();
      app.quit();
    }
  });

  app.on('before-quit', () => {
    quitting = true;
    killBackendTree();
  });
}
