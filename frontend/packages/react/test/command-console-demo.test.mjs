import assert from 'node:assert/strict';
import { after, test } from 'node:test';
import { mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { createRslib } from '@rslib/core';
import { pluginReact } from '@rsbuild/plugin-react';

const output = await mkdtemp(
  join(tmpdir(), 'ov-command-console-results-test-'),
);
const builder = await createRslib({
  config: {
    plugins: [pluginReact()],
    performance: { buildCache: false },
    lib: [
      {
        format: 'esm',
        dts: false,
        source: {
          entry: {
            demo: fileURLToPath(
              new URL(
                '../../../docs/docs/_demos/command-console-demo-result.ts',
                import.meta.url,
              ),
            ),
            commands: fileURLToPath(
              new URL(
                '../../../docs/docs/_demos/command-console-commands/index.ts',
                import.meta.url,
              ),
            ),
          },
        },
      },
    ],
    output: { target: 'node', distPath: { root: output } },
  },
});
await builder.build();
await writeFile(join(output, 'package.json'), '{"type":"module"}');
after(() => rm(output, { recursive: true, force: true }));
const { createCommandConsoleDemoRecord } = await import(
  pathToFileURL(join(output, 'demo.js'))
);
const { createCommandConsoleCommands } = await import(
  pathToFileURL(join(output, 'commands.js'))
);

test('every demo command returns its structured result instead of a generic execution message', () => {
  for (const descriptor of createCommandConsoleCommands('zh-CN')) {
    const result = createCommandConsoleDemoRecord(
      `${descriptor.inputText} demo`,
      'zh-CN',
    );
    assert.equal(result.status, 'success', descriptor.name);
    const expected =
      descriptor.name === 'help'
        ? 'command-help'
        : ['task list', 'session list'].includes(descriptor.name)
          ? 'resource-tree'
          : descriptor.name.startsWith('task ')
            ? 'status'
            : {
                search: 'search-list',
                resource: 'resource-preview',
                tree: 'resource-tree',
                status: 'status',
                write: 'write-result',
              }[descriptor.skeleton];
    assert.equal(result.content.variant, expected, descriptor.name);
  }
  const compile = createCommandConsoleDemoRecord(
    'ov compile --compile-name demo-wiki',
    'zh-CN',
  );
  assert.equal(compile.status, 'success');
  assert.ok(compile.content.lines.includes('Status: pending'));
  assert.match(
    compile.content.lines.find((line) => line.startsWith('Request:')),
    /"compile_name":"demo-wiki"/,
  );
  const saved = createCommandConsoleDemoRecord(
    'ov compile create --compile-name demo-wiki --from viking://resources/acme --to viking://resources/acme-wiki --skill viking://agent/skills/llm-wiki',
    'zh-CN',
  );
  assert.ok(saved.content.lines.includes('Status: saved'));
  assert.ok(saved.content.lines.includes('Compilation: not started'));
  assert.equal(saved.content.lines.includes('Status: pending'), false);
});

test('demo execution recognizes documented aliases and whitespace-separated arguments', () => {
  for (const command of [
    '/list viking://resources/acme',
    'ov list viking://resources/acme',
    'ov ls\tviking://resources/acme',
    'ov\t session  get demo',
  ]) {
    assert.equal(
      createCommandConsoleDemoRecord(command, 'zh-CN').status,
      'success',
      command,
    );
  }
  const descriptor = createCommandConsoleCommands('en-US').find(
    (item) => item.name === 'session add-messages',
  );
  assert.match(descriptor.usage, /<session-id> <messages-json>/);
  assert.doesNotMatch(descriptor.usage, /--messages/);
  assert.deepEqual(
    descriptor.params.map((param) => param.label),
    ['<session-id>', '<messages-json>'],
  );
});
