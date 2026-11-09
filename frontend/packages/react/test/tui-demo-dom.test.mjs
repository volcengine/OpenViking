import assert from 'node:assert/strict';
import { after, test } from 'node:test';
import { mkdtemp, rm, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { createElement } from 'react';
import { createRslib } from '@rslib/core';
import { pluginReact } from '@rsbuild/plugin-react';
import { act, click, type } from './helpers/tui-dom.mjs';

const output = await mkdtemp(
  fileURLToPath(
    new URL('../../../node_modules/.ov-tui-quick-start-', import.meta.url),
  ),
);
await writeFile(
  join(output, 'runtime.mjs'),
  "export const useLang = () => 'zh';\n",
);
const builder = await createRslib({
  config: {
    plugins: [pluginReact()],
    performance: { buildCache: false },
    resolve: {
      alias: { '@rspress/core/runtime': join(output, 'runtime.mjs') },
    },
    lib: [
      {
        format: 'esm',
        dts: false,
        source: {
          entry: {
            demo: fileURLToPath(
              new URL(
                '../../../docs/docs/_demos/tui-empty.tsx',
                import.meta.url,
              ),
            ),
          },
        },
      },
    ],
    output: {
      target: 'node',
      distPath: { root: join(output, 'dist') },
      externals: [
        'react',
        'react/jsx-runtime',
        '@openviking/react/components/tui',
        '@openviking/react/icons',
      ],
    },
  },
});
await builder.build();
await writeFile(join(output, 'package.json'), '{"type":"module"}');
after(() => rm(output, { recursive: true, force: true }));
const { default: TuiEmpty } = await import(
  pathToFileURL(join(output, 'dist/demo.js'))
);
const { createRoot } = await import('react-dom/client');

test('every quick-start command focuses its own composer so arguments can be entered immediately', async () => {
  const container = document.createElement('div');
  document.body.append(container);
  const root = createRoot(container);
  await act(() => root.render(createElement(TuiEmpty)));
  try {
    const input = container.querySelector('textarea');
    for (const button of container.querySelectorAll(
      '.ov-demo-tui-empty__action',
    )) {
      await click(button);
      assert.ok(document.activeElement === input);
      const prefix = input.value;
      await type(input, `${prefix}test`);
      assert.equal(input.value, `${prefix}test`);
    }
  } finally {
    await act(() => root.unmount());
    container.remove();
  }
});
