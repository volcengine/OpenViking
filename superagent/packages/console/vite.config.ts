import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

export default defineConfig({
  base: '/console/',
  plugins: [react()],
  build: {
    outDir: '../web/public/console',
    emptyOutDir: true,
    sourcemap: false,
  },
  server: {
    proxy: {
      '/api': 'http://127.0.0.1:3737',
      '/health': 'http://127.0.0.1:3737',
      '/ready': 'http://127.0.0.1:3737',
    },
  },
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    css: true,
  },
})
