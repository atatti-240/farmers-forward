import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Dev: /api is proxied to the public backend on :5050
// (PUBLIC_ONLY=1 /usr/bin/python3 -m backend.public_app), so no CORS locally.
// Production: VITE_API_URL points at the public backend's /api/public.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { '/api': { target: 'http://127.0.0.1:5050', changeOrigin: true } },
  },
  preview: {
    port: 4173,
    proxy: { '/api': { target: 'http://127.0.0.1:5050', changeOrigin: true } },
  },
  build: { outDir: 'dist', target: 'es2020', sourcemap: false },
})
