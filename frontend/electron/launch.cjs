'use strict';

const { spawn } = require('node:child_process');

function desktopEnvironment(env) {
  return Object.fromEntries(Object.entries(env)
    .filter(([key]) => key.toUpperCase() !== 'ELECTRON_RUN_AS_NODE'));
}

function launch(args = process.argv.slice(2), env = process.env, spawnChild = spawn) {
  const child = spawnChild(require('electron'), args.length ? args : ['.'], {
    env: desktopEnvironment(env), stdio: 'inherit', windowsHide: false,
  });
  child.once('error', (error) => {
    console.error(`Could not launch Electron: ${error.message}`);
    process.exitCode = 1;
  });
  child.once('exit', (code) => { process.exitCode = code ?? 1; });
  return child;
}

if (require.main === module) launch();
module.exports = { desktopEnvironment, launch };
