import { fileURLToPath, URL } from 'node:url'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { defineConfig } from 'vitest/config'

// Le build sort dans `web/dist`, servi en statique par le daemon FastAPI (build_app monte StaticFiles).
// En dev, Vite (:5173) proxifie l'API + les WebSockets vers le daemon (:8700) — origine unique côté navigateur.
const DAEMON = 'http://127.0.0.1:8700'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
  },
  server: {
    proxy: {
      '/api': { target: DAEMON, changeOrigin: true },
      '/ws': { target: DAEMON, ws: true, changeOrigin: true },
      '/health': { target: DAEMON, changeOrigin: true },
    },
  },
  build: { outDir: 'dist', emptyOutDir: true },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: './src/test/setup.ts',
    css: false,
  },
})
