import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import agentosBridge from './vite-agentos-bridge.mjs'

// https://vite.dev/config/
export default defineConfig({
  // Relative base so the built dist/ loads correctly from file:// inside Electron.
  base: './',
  // The dev bridge (serve-only) exposes the Python worker to the browser dev server;
  // Electron uses IPC instead, so nothing here ends up in dist/.
  plugins: [react(), agentosBridge()],
  server: {
    port: 5173,
    host: '127.0.0.1',
  },
})
