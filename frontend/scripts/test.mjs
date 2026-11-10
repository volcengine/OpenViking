import { mkdtemp, rm, symlink, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawn } from 'node:child_process';
import { createRslib } from '@rslib/core';
import { pluginReact } from '@rsbuild/plugin-react';
import { pluginSvgr } from '@rsbuild/plugin-svgr';

// Test private orchestration without adding exports to the published package.
const root = fileURLToPath(new URL('../', import.meta.url));
const output = await mkdtemp(join(tmpdir(), 'ov-ui-private-tests-'));
try {
  const builder = await createRslib({
    cwd: join(root, 'packages/react'),
    config: {
      lib: [
        {
          format: 'esm',
          dts: false,
          source: {
            entry: {
              'file-browser': './src/modules/file-browser/use-file-browser.ts',
              console: './src/modules/console/use-console.ts',
              provider: './src/client/provider.tsx',
              'wiki-pane': './src/modules/file-browser/wiki-dashboard-pane.tsx',
              'experience-pane':
                './src/modules/file-browser/experience-dashboard-pane.tsx',
            },
          },
          tools: {
            rspack: {
              optimization: { splitChunks: { chunks: 'all', minSize: 0 } },
            },
          },
        },
      ],
      plugins: [pluginReact(), pluginSvgr()],
      performance: { buildCache: false },
      output: {
        target: 'node',
        emitCss: false,
        distPath: { root: output },
        dataUriLimit: { image: 64 * 1024 },
      },
    },
  });
  await builder.build();
  await writeFile(join(output, 'package.json'), '{"type":"module"}');
  await symlink(
    join(root, 'node_modules'),
    join(output, 'node_modules'),
    process.platform === 'win32' ? 'junction' : 'dir',
  );
  process.exitCode = await new Promise((resolve, reject) => {
    const child = spawn(
      process.execPath,
      ['--test', 'scripts/package.test.mjs', 'packages/react/test/*.test.mjs'],
      {
        cwd: root,
        stdio: 'inherit',
        env: { ...process.env, NODE_ENV: 'test', OV_UI_TEST_HOOKS: output },
      },
    );
    child.once('error', reject);
    child.once('exit', (code) => resolve(code ?? 1));
  });
} finally {
  await rm(output, { recursive: true, force: true });
}
