import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import agentosBridge from './vite-agentos-bridge.mjs'

const here = path.dirname(fileURLToPath(import.meta.url))
const nested = path.join(here, 'node_modules', '@tailwindcss', 'postcss', 'node_modules', 'tailwindcss', 'index.css')
// Tailwind 4 is nested under @tailwindcss/postcss (Tailwind 3 owns the top level for the classic UI).
// The ported RuseBot stylesheet begins with @import "tailwindcss" and must resolve to the v4 copy.
const tailwind4Entry = fs.existsSync(nested) ? nested : path.join(here, 'node_modules', 'tailwindcss', 'index.css')
// https://vite.dev/config/
export default defineConfig({
  // Relative base so the built dist/ loads correctly from file:// inside Electron.
  base: './',
  // The dev bridge (serve-only) exposes the Python worker to the browser dev server;
  // Electron uses IPC instead, so nothing here ends up in dist/.
  plugins: [react(), agentosBridge()],
  resolve: {
    alias: [{ find: /^tailwindcss$/, replacement: tailwind4Entry }],
  },
  server: {
    port: 5173,
    host: '127.0.0.1',
  },
})
