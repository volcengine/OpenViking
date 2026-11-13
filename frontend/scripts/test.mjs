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
              button: './src/shared/primitives/button/button.tsx',
              input: './src/shared/primitives/input/input.tsx',
              modal: './src/shared/primitives/modal/modal.tsx',
              notification: './src/shared/primitives/notification/index.ts',
              'browser-entries':
                './src/components/file-browser-action-bar/index.ts',
              'file-browser': './src/modules/file-browser/use-file-browser.ts',
              console: './src/modules/console/use-console.ts',
              'task-context': './src/shared/task-tracking/context.tsx',
              'task-tracker': './src/shared/task-tracking/tracker.ts',
              'task-references':
                './src/shared/task-tracking/read-references.ts',
              'bot-chat-hook':
                './src/modules/console/chat/use-viking-bot-chat.ts',
              'bot-chat': './src/modules/console/chat/chat-controller.ts',
              'bot-sessions':
                './src/modules/console/chat/sessions-controller.ts',
              'bot-aggregate': './src/modules/console/chat/aggregate.ts',
              'bot-events': './src/modules/console/chat/native-events.ts',
              'bot-client': './src/client/client.ts',
              'bot-service': './src/modules/console/chat/native-service.ts',
              'bot-submit': './src/components/viking-bot/composer-controls.tsx',
              'bot-composer-events':
                './src/components/viking-bot/composer-events.ts',
              'console-conversation':
                './src/modules/console/use-conversation.tsx',
              provider: './src/client/provider.tsx',
              'reference-tags':
                './src/components/viking-bot/composer-reference-tags.tsx',
              'wiki-pane': './src/modules/file-browser/wiki-dashboard-pane.tsx',
              'wiki-conflicts': './src/modules/file-browser/wiki-conflicts.ts',
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
        autoExternal: { exclude: ['react-focus-lock'] },
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
      [
        '--test',
        'scripts/package.test.mjs',
        'scripts/react-release*.test.mjs',
        'scripts/release-notes*.test.mjs',
        'packages/react/test/*.test.mjs',
      ],
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
