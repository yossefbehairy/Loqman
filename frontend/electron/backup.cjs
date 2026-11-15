'use strict';

const path = require('node:path');

async function saveBackup({ call, dialog, fs, documentsPath }) {
  const backup = await call('backup.create', {});
  let failure;
  try {
    const choice = await dialog.showSaveDialog({
      title: 'Save database backup',
      defaultPath: path.join(documentsPath, path.basename(backup.name)),
      filters: [{ name: 'SQLite database', extensions: ['db'] }],
    });
    if (choice.canceled || !choice.filePath) return { saved: false, cancelled: true };
    await fs.copyFile(backup.path, choice.filePath);
    return { saved: true, path: choice.filePath, size: backup.size };
  } catch (error) {
    failure = error;
    throw error;
  } finally {
    try {
      await call('backup.cleanup', { path: backup.path });
    } catch (error) {
      // Preserve a copy/dialog failure if cleanup also fails.
      if (!failure) throw error;
    }
  }
}

module.exports = { saveBackup };
