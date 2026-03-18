import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { resolve } from 'path'

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': resolve(__dirname, 'src'),
    },
  },
  define: {
    'import.meta.env': {
      VITE_ADMIN_USERNAME: 'admin',
      VITE_ADMIN_PASSWORD: 'changeme123',
      VITE_ROOT_API_KEY: '6z_TTilwV_CM16qV3ExG1PAVFCptrLp-ver8Xb1lGD8'
    }
  },
  server: {
    port: 5173,
    host: '0.0.0.0',
    allowedHosts: true,
    cors: {
      origin: '*',
      credentials: true
    },
    proxy: {
      '/api': {
        target: 'http://localhost:1933',
        changeOrigin: true,
        secure: false
      }
    }
  },
  build: {
    outDir: 'dist',
    assetsDir: 'assets'
  },
  // Enable SPA mode for proper static file serving
  appType: 'spa'
})
