import { pluginReact } from '@rsbuild/plugin-react';
import { defineConfig } from '@rslib/core';

export default defineConfig({
  source: {
    entry: {
      index: './src/index.ts',
      tree: './src/components/file-tree/file-tree.ts',
      preview: './src/components/file-preview/file-preview.ts',
      console: './src/components/command-console/command-console.ts',
      'viking-bot': './src/components/viking-bot/viking-bot.ts',
    },
  },
  lib: [{ format: 'esm', dts: true }],
  output: { target: 'web' },
  plugins: [pluginReact()],
});
