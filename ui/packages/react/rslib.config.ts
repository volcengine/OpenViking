import { pluginReact } from '@rsbuild/plugin-react';
import { defineConfig } from '@rslib/core';

export default defineConfig({
  source: {
    entry: {
      index: './src/index.ts',
      tree: './src/patterns/file-tree/file-tree.ts',
      preview: './src/patterns/file-preview/file-preview.ts',
      console: './src/patterns/command-console/command-console.ts',
      'viking-bot': './src/patterns/viking-bot/viking-bot.ts',
    },
  },
  lib: [{ format: 'esm', dts: true }],
  output: { target: 'web' },
  plugins: [pluginReact()],
});
