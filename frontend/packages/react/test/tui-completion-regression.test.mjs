import assert from 'node:assert/strict';
import { after, test } from 'node:test';
import { mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { createRslib } from '@rslib/core';

const output = await mkdtemp(join(tmpdir(), 'ov-tui-subcommands-'));
const source = new URL('../src/components/tui/', import.meta.url);
const builder = await createRslib({
  config: {
    performance: { buildCache: false },
    lib: [
      {
        format: 'esm',
        dts: false,
        source: {
          entry: {
            input: fileURLToPath(new URL('command-input.ts', source)),
            completion: fileURLToPath(new URL('command-completion.ts', source)),
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
const { matchTuiCommandInput, formatTuiCommandInput } = await import(
  pathToFileURL(join(output, 'input.js'))
);
const { getTuiCommandQuery, getTuiInlineCompletion } = await import(
  pathToFileURL(join(output, 'completion.js'))
);
const commands = [
  'session',
  'session get',
  'session list',
  'task',
  'task list',
].map((name) => ({
  name,
  inputText: `ov ${name}`,
  description: name,
  usage: `ov ${name}`,
}));

test('parent commands do not hide longer matching prefixes or their inline completion', () => {
  const query = getTuiCommandQuery('ov session g', commands);
  assert.deepEqual(
    query.matches.map((command) => command.name),
    ['session get'],
  );
  assert.equal(
    getTuiInlineCompletion('ov session g', query, query.matches[0]).value,
    'ov session get ',
  );
  const task = getTuiCommandQuery('ov task ', commands);
  assert.equal(task.canOpenPalette, true);
  assert.deepEqual(
    task.matches.map((command) => command.name),
    ['task list'],
  );
  assert.equal(
    getTuiCommandQuery('ov session get id', commands).matches.length,
    0,
  );
});

test('matching accepts repeated whitespace while preserving exact source slices and arguments', () => {
  const value = '  OV\t session  get\t"a b"';
  const match = matchTuiCommandInput(value, commands);
  assert.equal(match.command.name, 'session get');
  assert.equal(match.leadingWhitespace, '  ');
  assert.equal(match.prefix, 'OV\t session  get');
  assert.equal(match.rest, '\t"a b"');
  assert.equal(
    formatTuiCommandInput(value, commands),
    '  ov session get\t"a b"',
  );
  assert.equal(
    matchTuiCommandInput('ov  session ', commands).command.name,
    'session',
  );
  assert.equal(
    matchTuiCommandInput('ov session getter', commands).command.name,
    'session',
  );
});

test('slash discovery keeps multiword candidates, aliases and normalized whitespace until arguments begin', () => {
  const catalogue = commands.map((command) => ({
    ...command,
    aliases: [command.name.replace('session', 'chat')],
  }));
  for (const value of ['/session g', '/chat g', '  /SESSION\t g']) {
    const query = getTuiCommandQuery(value, catalogue);
    assert.equal(query.scope, '/');
    assert.equal(query.automatic, true);
    assert.deepEqual(
      query.matches.map((command) => command.name),
      ['session get'],
    );
    assert.equal(
      getTuiInlineCompletion(value, query, query.matches[0], true).suffix,
      'et',
    );
    assert.equal(
      getTuiInlineCompletion(value, query, query.matches[0], true).value.trim(),
      'ov session get',
    );
  }
  const discovered = getTuiCommandQuery('/get', catalogue);
  assert.ok(
    discovered.matches.some((command) => command.name === 'session get'),
  );
  const query = getTuiCommandQuery('/session ', catalogue);
  assert.deepEqual(
    query.matches.map((command) => command.name),
    ['session get', 'session list'],
  );
  assert.equal(
    getTuiInlineCompletion('/session ', query, query.matches[0], true),
    undefined,
  );
  assert.equal(
    getTuiCommandQuery('/session get', catalogue).matches[0].name,
    'session get',
  );
  for (const value of [
    '/session get ',
    '/session get demo',
    '/session unknown',
  ])
    assert.equal(getTuiCommandQuery(value, catalogue).matches.length, 0, value);
});
