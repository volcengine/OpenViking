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
          client: './src/client/index.ts',
          'components/file-tree': './src/components/file-tree/index.ts',
          'components/file-preview': './src/components/file-preview/index.ts',
          'components/tui': './src/components/tui/index.ts',
          'components/scope-switcher':
            './src/components/scope-switcher/index.ts',
          'components/agent-connect': './src/components/agent-connect/index.ts',
          'modules/file-browser': './src/modules/file-browser/index.ts',
          'modules/console': './src/modules/console/index.ts',
          'modules/task-center': './src/modules/task-center/index.ts',
          'modules/user-usage': './src/modules/user-usage/index.ts',
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
      'components/agent-connect/style':
        './src/components/agent-connect/style.css',
      'modules/style': './src/modules/style.css',
      'modules/file-browser/style': './src/modules/file-browser/style.css',
      'modules/console/style': './src/modules/console/style.css',
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
