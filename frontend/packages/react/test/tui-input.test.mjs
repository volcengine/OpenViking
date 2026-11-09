import assert from 'node:assert/strict';
import { after, test } from 'node:test';
import { mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { createRslib } from '@rslib/core';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { Tui } from '@openviking/react/components/tui';

const output = await mkdtemp(join(tmpdir(), 'ov-tui-input-'));
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
const { formatTuiCommandInput, matchTuiCommandInput, getTuiArgumentHints } =
  await import(pathToFileURL(join(output, 'input.js')));
const { getTuiCommandQuery, getTuiInlineCompletion } = await import(
  pathToFileURL(join(output, 'completion.js'))
);
after(() => rm(output, { recursive: true, force: true }));
const commands = [
  {
    name: 'read',
    inputText: 'host read',
    aliases: ['cat'],
    description: 'Read a resource',
    usage: 'host read <uri> [--offset <n>]',
    params: [{ label: '<uri>', description: 'Resource URI', required: true }],
    options: [{ token: '--offset <n>', description: 'Optional offset' }],
  },
];
const props = {
  records: [],
  value: 'host read ',
  commands,
  onValueChange() {},
};

const commandText =
  '<span class="ov-tui__command-prefix">host </span><span class="ov-tui__command-name">read</span>';

test('host input prefixes and aliases preserve arguments, whitespace and unknown text', () => {
  assert.equal(
    formatTuiCommandInput('  /cat "viking://demo/a b"\n', commands),
    '  host read "viking://demo/a b"\n',
  );
  assert.equal(
    formatTuiCommandInput('/unknown --raw', commands),
    '/unknown --raw',
  );
  assert.equal(
    matchTuiCommandInput('host read viking://demo', commands)?.rest,
    ' viking://demo',
  );
  assert.equal(matchTuiCommandInput('host reader', commands), undefined);
  const longer = {
    ...commands[0],
    name: 'read all',
    inputText: 'host read all',
  };
  assert.equal(
    matchTuiCommandInput('host read all demo', [commands[0], longer])?.command
      .name,
    'read all',
  );
});

test('confirmed commands use inline hints without opening the full parameter panel', () => {
  const html = renderToStaticMarkup(createElement(Tui, props));
  assert.ok(html.includes(commandText));
  assert.ok(html.includes('ov-tui__input-arguments'));
  assert.ok(html.includes('&lt;uri&gt;'));
  assert.ok(!html.includes('class="ov-tui__hint"'));
  assert.ok(!html.includes('role="listbox"'));
  assert.match(html, /<textarea[^>]*>host read <\/textarea>/);
});

test('inline placeholders disappear once real arguments are typed and never enter the value', () => {
  const html = renderToStaticMarkup(
    createElement(Tui, { ...props, value: 'host read viking://demo' }),
  );
  assert.ok(!html.includes('class="ov-tui__input-arguments"'));
  assert.ok(!html.includes('class="ov-tui__hint"'));
  assert.ok(html.includes(commandText));
  assert.match(html, /<textarea[^>]*>host read viking:\/\/demo<\/textarea>/);
});

test('slash discovery opens an unselected list without unsolicited details', () => {
  const html = renderToStaticMarkup(
    createElement(Tui, { ...props, value: '/' }),
  );
  assert.ok(html.includes('role="listbox"'));
  assert.ok(!html.includes('class="ov-tui__hint"'));
  assert.ok(!html.includes('aria-selected="true"'));
  assert.ok(!html.includes('aria-activedescendant'));
});

test('commands without positional arguments hint only explicitly required flags', () => {
  const command = {
    ...commands[0],
    params: [],
    options: [
      { token: '--target <uri>', description: 'Target', required: true },
      { token: '--verbose', description: 'Optional output' },
    ],
  };
  assert.deepEqual(getTuiArgumentHints(command), [
    { label: '--target <uri>', description: 'Target' },
  ]);
});

test('inline completion derives namespace tokens and command suffixes from host inputText', () => {
  for (const [value, suffix, accepted] of [
    ['h', 'ost', 'host'],
    ['host r', 'ead', 'host read '],
    ['  host  r', 'ead', '  host read '],
    ['/ca', 't', 'host read '],
  ]) {
    const query = getTuiCommandQuery(value, commands);
    const hint = getTuiInlineCompletion(value, query, query.matches[0]);
    assert.equal(hint?.suffix, suffix);
    assert.equal(hint?.value, accepted);
  }
  const query = getTuiCommandQuery('host ', commands);
  assert.equal(query.canOpenPalette, true);
  assert.equal(query.automatic, false);
  assert.equal(
    getTuiInlineCompletion('host ', query, query.matches[0]),
    undefined,
  );
  assert.equal(
    getTuiInlineCompletion('host ', query, query.matches[0], true)?.value,
    'host read ',
  );
});

test('completion stops at arguments and does not guess conflicting namespace prefixes', () => {
  for (const value of [
    '',
    'host read ',
    'host read resource',
    'host r\n',
    'unknown',
  ]) {
    const query = getTuiCommandQuery(value, commands);
    assert.equal(
      getTuiInlineCompletion(value, query, query.matches[0]),
      undefined,
    );
  }
  assert.equal(getTuiCommandQuery('/', []).automatic, false);
  const other = { ...commands[0], inputText: 'help read' };
  const ambiguous = getTuiCommandQuery('h', [...commands, other]);
  assert.equal(ambiguous.prefixTarget, undefined);
  assert.equal(ambiguous.canOpenPalette, true);
});

test('ghost text is visual only; ordinary CLI typing keeps the palette closed', () => {
  for (const [value, suffix] of [
    ['h', 'ost'],
    ['host r', 'ead'],
  ]) {
    const html = renderToStaticMarkup(createElement(Tui, { ...props, value }));
    assert.ok(html.includes(`ov-tui__input-completion">${suffix}</span>`));
    assert.ok(!html.includes('role="listbox"'));
    assert.ok(html.includes(`>${value}</textarea>`));
    assert.ok(!html.includes('class="ov-tui__input-arguments"'));
  }
});
