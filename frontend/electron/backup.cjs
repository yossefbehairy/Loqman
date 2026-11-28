'use strict';

const path = require('node:path');

async function saveBackup({ call, dialog, fs, documentsPath }) {
  const backup = await call('backup.create', {});
  let result;
  let failure;
  try {
    const choice = await dialog.showSaveDialog({
      title: 'Save database backup',
      defaultPath: path.join(documentsPath, path.basename(backup.name)),
      filters: [{ name: 'SQLite database', extensions: ['db'] }],
    });
    if (choice.canceled || !choice.filePath) {
      result = { saved: false, cancelled: true };
    } else {
      await fs.copyFile(backup.path, choice.filePath);
      result = { saved: true, path: choice.filePath, size: backup.size };
    }
  } catch (error) {
    failure = error;
  }
  // Always clean up; a dialog or copy failure wins over a cleanup failure.
  let cleanupError;
  try {
    await call('backup.cleanup', { path: backup.path });
  } catch (error) {
    cleanupError = error;
  }
  if (failure) throw failure;
  if (cleanupError) throw cleanupError;
  return result;
}

module.exports = { saveBackup };
