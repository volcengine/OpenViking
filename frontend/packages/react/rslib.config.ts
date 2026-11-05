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
          'components/file-tree': './src/components/file-tree/file-tree.tsx',
          'components/file-preview':
            './src/components/file-preview/file-preview.tsx',
          'components/tui': './src/components/tui/tui.tsx',
          'components/scope-switcher':
            './src/components/scope-switcher/scope-switcher.tsx',
          'modules/file-browser': './src/modules/file-browser/file-browser.tsx',
          'modules/console': './src/modules/console/console.tsx',
          'modules/agent-connect':
            './src/modules/agent-connect/agent-connect.tsx',
          'modules/task-center': './src/modules/task-center/task-center.tsx',
          'modules/user-usage': './src/modules/user-usage/user-usage.tsx',
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
      'components/file-tree/style': './src/components/file-tree/style.css',
      'components/file-preview/style':
        './src/components/file-preview/style.css',
      'components/tui/style': './src/components/tui/style.css',
      'components/scope-switcher/style':
        './src/components/scope-switcher/style.css',
      'modules/style': './src/modules/style.css',
      'modules/file-browser/style': './src/modules/file-browser/style.css',
      'modules/console/style': './src/modules/console/style.css',
      'modules/agent-connect/style': './src/modules/agent-connect/style.css',
      'modules/task-center/style': './src/modules/task-center/style.css',
      'modules/user-usage/style': './src/modules/user-usage/style.css',
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
