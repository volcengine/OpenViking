import { pluginReact } from '@rsbuild/plugin-react';
import { defineConfig } from '@rslib/core';

export default defineConfig({
  lib: [
    {
      id: 'javascript',
      format: 'esm',
      dts: true,
      source: {
        entry: {
          index: './src/index.ts',
          icons: './src/icons/index.tsx',
          'components/status-badge':
            './src/components/status-badge/status-badge.tsx',
          'components/file-tree': './src/components/file-tree/file-tree.tsx',
          'components/file-preview':
            './src/components/file-preview/file-preview.tsx',
          'components/tui': './src/components/tui/tui.tsx',
          'components/viking-bot': './src/components/viking-bot/viking-bot.tsx',
          'components/scope-switcher':
            './src/components/scope-switcher/scope-switcher.tsx',
          'components/agent-connect':
            './src/components/agent-connect/agent-connect.tsx',
          'components/resource-import':
            './src/components/resource-import/resource-import.tsx',
        },
      },
      output: { emitCss: false },
      tools: {
        rspack: {
          optimization: { splitChunks: { chunks: 'all', minSize: 0 } },
        },
      },
    },
    // Build each stylesheet independently so every public CSS entry is complete.
    ...Object.entries({
      style: './src/style.css',
      'components/style': './src/components/style.css',
      'components/status-badge/style':
        './src/components/status-badge/style.css',
      'components/file-tree/style': './src/components/file-tree/style.css',
      'components/file-preview/style':
        './src/components/file-preview/style.css',
      'components/tui/style': './src/components/tui/style.css',
      'components/viking-bot/style': './src/components/viking-bot/style.css',
      'components/scope-switcher/style':
        './src/components/scope-switcher/style.css',
      'components/agent-connect/style':
        './src/components/agent-connect/style.css',
      'components/resource-import/style':
        './src/components/resource-import/style.css',
    }).map(([name, entry]) => ({
      id: name.replaceAll('/', '-'),
      format: 'esm' as const,
      dts: false,
      source: { entry: { [name]: entry } },
    })),
  ],
  output: { target: 'web' },
  plugins: [pluginReact()],
});
