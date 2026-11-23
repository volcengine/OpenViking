import assert from 'node:assert/strict';
import { after, test } from 'node:test';
import { mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { createRslib } from '@rslib/core';
import { createElement } from 'react';
import { renderMarkup } from './helpers/csr-render.mjs';
import { CommandConsole } from '@openviking/react/components/command-console';

// Exercise generic matching without exposing implementation helpers publicly.
const source = new URL('../src/components/command-console/', import.meta.url);
const output = await mkdtemp(join(tmpdir(), 'ov-command-console-command-'));
const builder = await createRslib({
  config: {
    performance: { buildCache: false },
    lib: [
      {
        format: 'esm',
        dts: false,
        source: {
          entry: {
            commands: fileURLToPath(new URL('command-definitions.ts', source)),
            catalog: fileURLToPath(new URL('command-catalog.ts', source)),
          },
        },
      },
    ],
    output: { target: 'node', distPath: { root: output } },
  },
});
await builder.build();
await writeFile(join(output, 'package.json'), '{"type":"module"}');
const {
  filterCommandConsoleCommands,
  findCommandConsoleCommand,
  groupCommandConsoleCommands,
} = await import(pathToFileURL(join(output, 'commands.js')));
const {
  parseCommandConsoleCommandInput,
  resolveCommandConsoleCommandName,
  resolveCommandConsoleSkeleton,
} = await import(pathToFileURL(join(output, 'catalog.js')));
after(() => rm(output, { recursive: true, force: true }));

const hostCommands = [
  {
    name: 'vendor-refresh',
    aliases: ['refresh'],
    group: 'custom',
    description: '刷新宿主索引',
    usage: '/vendor-refresh <uri>',
    skeleton: 'status',
    params: [{ label: '<uri>', description: '目标索引 URI', required: true }],
    examples: ['/vendor-refresh viking://demo'],
  },
  {
    name: 'vendor-query',
    group: 'extra',
    description: '检索宿主数据',
    usage: '/vendor-query <query>',
    skeleton: 'search',
  },
];

const find = (input) => findCommandConsoleCommand(input, hostCommands);
const filter = (query) => filterCommandConsoleCommands(query, hostCommands);
const groups = [{ key: 'custom', label: '宿主操作' }];

test('parses slash commands and normalizes command names without business knowledge', () => {
  assert.deepEqual(parseCommandConsoleCommandInput('/vendor-refresh "demo"'), {
    name: 'vendor-refresh',
    rest: ' "demo"',
    hasTrailingSpace: true,
  });
  assert.equal(parseCommandConsoleCommandInput('vendor-refresh demo'), null);
  assert.equal(
    resolveCommandConsoleCommandName('  /VENDOR-REFRESH viking://demo'),
    'vendor-refresh',
  );
});

test('matches host names, aliases and localized descriptions', () => {
  assert.equal(find('/refresh viking://demo')?.name, 'vendor-refresh');
  assert.equal(find('/find anything'), undefined);
  assert.equal(filter('检索')[0].name, 'vendor-query');
  assert.equal(
    filterCommandConsoleCommands('reading', [
      {
        ...hostCommands[0],
        summary: 'Import',
        description: 'Import content for reading',
      },
    ]).length,
    0,
  );
  assert.equal(filter('refresh')[0].name, 'vendor-refresh');
});

test('keeps host group order and appends undeclared groups without empty sections', () => {
  const sections = groupCommandConsoleCommands(hostCommands, [
    { key: 'empty', label: 'Empty' },
    ...groups,
  ]);
  assert.deepEqual(
    sections.map(({ group }) => group.key),
    ['custom', 'extra'],
  );
  assert.equal(sections[0].group.label, '宿主操作');
  assert.equal(
    sections.flatMap(({ commands }) => commands).length,
    hostCommands.length,
  );
});

test('pending shapes come only from the host description', () => {
  assert.equal(resolveCommandConsoleSkeleton(hostCommands[0]), 'status');
  assert.equal(resolveCommandConsoleSkeleton(hostCommands[1]), 'search');
  assert.equal(resolveCommandConsoleSkeleton(undefined), 'generic');
  assert.equal(
    resolveCommandConsoleSkeleton({
      name: 'find',
      description: '',
      usage: '/find',
    }),
    'generic',
  );
});

const props = {
  records: [
    {
      id: 'demo',
      command: '/vendor-refresh',
      status: 'success',
      content: { variant: 'text', text: 'demo' },
    },
  ],
  value: '/',
  commands: hostCommands,
  commandGroups: groups,
  onValueChange() {},
};

test('a new host command is discoverable without selecting it or opening its details', () => {
  const next = {
    name: 'brand-new-command',
    group: 'custom',
    description: '宿主新增命令',
    usage: '/brand-new-command [limit]',
    params: [
      {
        label: '[limit]',
        description: '数量',
        required: false,
        defaultValue: '25',
      },
    ],
    examples: ['/brand-new-command 25'],
  };
  const html = renderMarkup(
    createElement(CommandConsole, {
      ...props,
      commands: [next],
      locale: 'zh-CN',
    }),
  );
  for (const text of ['brand-new-command', '宿主新增命令'])
    assert.ok(html.includes(text), text);
  assert.ok(!html.includes('class="ov-command-console__hint"'));
  assert.ok(!html.includes('aria-selected="true"'));
  assert.ok(!html.includes('aria-activedescendant'));
  const empty = renderMarkup(
    createElement(CommandConsole, { ...props, commands: [] }),
  );
  assert.ok(!empty.includes('role="listbox"'));
  assert.ok(!empty.includes('add-resource'));
});

test('locale changes generic UI, labels override it, and host command copy is preserved', () => {
  const chinese = renderMarkup(
    createElement(CommandConsole, { ...props, locale: 'zh-CN' }),
  );
  for (const text of ['宿主操作', '刷新宿主索引', '执行成功'])
    assert.ok(chinese.includes(text), text);
  const override = renderMarkup(
    createElement(CommandConsole, {
      ...props,
      locale: 'zh-CN',
      labels: {
        success: 'Custom status',
        allCommands: 'Custom catalogue',
      },
    }),
  );
  assert.ok(override.includes('Custom status'));
  assert.ok(override.includes('Custom catalogue'));
  assert.ok(!chinese.includes('aria-activedescendant'));
  const english = renderMarkup(createElement(CommandConsole, props));
  assert.ok(english.includes('Succeeded'));
  assert.ok(english.includes('刷新宿主索引'));
});
