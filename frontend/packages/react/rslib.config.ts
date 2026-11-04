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
