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
            transport: fileURLToPath(
              new URL(
                '../../../docs/docs/_demos/console-transport.ts',
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

test('the Console demo applies grep expressions and the case-insensitive flag', async () => {
  const { consoleTransport } = await import(
    pathToFileURL(join(output, 'transport.js'))
  );
  const fetch = consoleTransport();
  const grep = (pattern, caseInsensitive = false) =>
    fetch('/api/v1/search/grep', {
      method: 'POST',
      body: JSON.stringify({ pattern, case_insensitive: caseInsensitive }),
    });

  const [exact, insensitive, sensitive, invalid] = await Promise.all([
    grep('^# OpenViking$'),
    grep('openviking', true),
    grep('openviking'),
    grep('['),
  ]);
  assert.equal((await exact.json()).result.matches[0].content, '# OpenViking');
  assert.equal((await insensitive.json()).result.matches.length, 1);
  assert.deepEqual((await sensitive.json()).result.matches, []);
  assert.equal(invalid.status, 400);
});

test('the Console demo grep returns lines and context from the readable file', async () => {
  const { consoleTransport } = await import(
    pathToFileURL(join(output, 'transport.js'))
  );
  const fetch = consoleTransport();
  const uri = 'viking://resources/demo/notes.md';
  const grep = (pattern, context) =>
    fetch('/api/v1/search/grep', {
      method: 'POST',
      body: JSON.stringify({ uri, pattern, ...context }),
    });
  const [read, heading, body] = await Promise.all([
    fetch('/api/v1/fs/read?uri=' + encodeURIComponent(uri)),
    grep('OpenViking', { after_context: 9 }),
    grep('fixed', { before_context: 9 }),
  ]);
  const lines = (await read.json()).result
    .split('\n')
    .map((content, index) => ({ line: index + 1, content }));
  assert.deepEqual((await heading.json()).result.matches, [
    { ...lines[0], uri, after_context: lines.slice(1) },
  ]);
  assert.deepEqual((await body.json()).result.matches, [
    { ...lines[2], uri, before_context: lines.slice(0, 2) },
  ]);
});
