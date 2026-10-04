import { readFileSync } from 'node:fs';
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { ViteImageOptimizer } from 'vite-plugin-image-optimizer';

export default defineConfig({
  plugins: [
    {
      name: 'language-bootstrap',
      transformIndexHtml(html) {
        const source = readFileSync(new URL('./src/language-preference.js', import.meta.url), 'utf8');
        return html.replace('/* __LANGUAGE_BOOTSTRAP__ */', source.replace('export function', 'function'));
      },
    },
    react(),
    ViteImageOptimizer({
      // Keep the small, versioned brand card lossless (including its SVG source).
      exclude: /og-openviking-blog[^/]*\.(png|svg)$/,
      png: { quality: 80 },
      jpeg: { quality: 80 },
    }),
  ],
  root: '.',
  build: {
    outDir: 'dist',
  },
});
