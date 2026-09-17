import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  // Expose the requested CRA-style variable in this Vite frontend.
  define: {
    'process.env.REACT_APP_CARTO_KEY': JSON.stringify(process.env.REACT_APP_CARTO_KEY || ''),
  },
  server: {
    port: 3000,
    proxy: {
      '/api': {
        target: 'http://localhost:5000',
        changeOrigin: true,
      },
    },
  },
  build: { outDir: 'dist' },
})
