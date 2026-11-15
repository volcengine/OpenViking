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
      '/api': process.env.VITE_OVSA_API_TARGET ?? 'http://127.0.0.1:3737',
      '/health': process.env.VITE_OVSA_API_TARGET ?? 'http://127.0.0.1:3737',
      '/ready': process.env.VITE_OVSA_API_TARGET ?? 'http://127.0.0.1:3737',
    },
  },
  test: {
    environment: 'jsdom',
    include: ['src/**/*.test.{ts,tsx}'],
    setupFiles: ['./src/test/setup.ts'],
    css: true,
  },
})
