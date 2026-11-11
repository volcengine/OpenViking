/// <reference types="vitest/config" />

import { readFile } from 'node:fs/promises'
import { defineConfig } from 'vite'
import { devtools } from '@tanstack/devtools-vite'
import tsconfigPaths from 'vite-tsconfig-paths'

import { tanstackRouter } from '@tanstack/router-plugin/vite'

import viteReact from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

const config = defineConfig({
  resolve: { dedupe: ['react', 'react-dom'] },
  optimizeDeps: {
    esbuildOptions: {
      plugins: [
        {
          name: 'studio-prism-grammar-order',
          setup(build) {
            // Prism grammars register on a shared global. CommonJS wrappers
            // preserve Ark's registration order when esbuild splits chunks.
            build.onLoad(
              { filter: /[/\\]prismjs[/\\]components[/\\]prism-[^/\\]+\.js$/ },
              async ({ path: filePath }) => ({
                contents: `${await readFile(filePath, 'utf8')}\nmodule.exports = {}\n`,
                loader: 'js',
              }),
            )
          },
        },
      ],
    },
  },
  plugins: [
    devtools(),
    tsconfigPaths({ projects: ['./tsconfig.json'] }),
    tailwindcss(),
    tanstackRouter({
      target: 'react',
      autoCodeSplitting: true,
      routeFileIgnorePattern: '(resource-upload|task-model|route\\.test)\\.',
    }),
    viteReact(),
  ],
  test: {
    setupFiles: ['./src/test/setup.ts'],
  },
})

export default config
