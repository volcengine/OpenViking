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

// Exercise generic matching without exposing implementation helpers publicly.
const source = new URL('../src/components/tui/', import.meta.url);
const output = await mkdtemp(join(tmpdir(), 'ov-tui-command-'));
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
const { filterTuiCommands, findTuiCommand, groupTuiCommands } = await import(
  pathToFileURL(join(output, 'commands.js'))
);
const { parseTuiCommandInput, resolveTuiCommandName, resolveTuiSkeleton } =
  await import(pathToFileURL(join(output, 'catalog.js')));
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
const groups = [{ key: 'custom', label: '宿主操作' }];

test('parses slash commands and normalizes command names without business knowledge', () => {
  assert.deepEqual(parseTuiCommandInput('/vendor-refresh "demo"'), {
    name: 'vendor-refresh',
    rest: ' "demo"',
    hasTrailingSpace: true,
  });
  assert.equal(parseTuiCommandInput('vendor-refresh demo'), null);
  assert.equal(
    resolveTuiCommandName('  /VENDOR-REFRESH viking://demo'),
    'vendor-refresh',
  );
});

test('matches host names, aliases and localized descriptions', () => {
  assert.equal(
    findTuiCommand('/refresh viking://demo', hostCommands)?.name,
    'vendor-refresh',
  );
  assert.equal(findTuiCommand('/find anything', hostCommands), undefined);
  assert.equal(filterTuiCommands('检索', hostCommands)[0].name, 'vendor-query');
  assert.equal(
    filterTuiCommands('reading', [
      {
        ...hostCommands[0],
        summary: 'Import',
        description: 'Import content for reading',
      },
    ]).length,
    0,
  );
  assert.equal(
    filterTuiCommands('refresh', hostCommands)[0].name,
    'vendor-refresh',
  );
});

test('keeps host group order and appends undeclared groups without empty sections', () => {
  const sections = groupTuiCommands(hostCommands, [
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
  assert.equal(resolveTuiSkeleton(hostCommands[0]), 'status');
  assert.equal(resolveTuiSkeleton(hostCommands[1]), 'search');
  assert.equal(resolveTuiSkeleton(undefined), 'generic');
  assert.equal(
    resolveTuiSkeleton({ name: 'find', description: '', usage: '/find' }),
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
  const html = renderToStaticMarkup(
    createElement(Tui, { ...props, commands: [next], locale: 'zh' }),
  );
  for (const text of ['brand-new-command', '宿主新增命令'])
    assert.ok(html.includes(text), text);
  assert.ok(!html.includes('class="ov-tui__hint"'));
  assert.ok(!html.includes('aria-selected="true"'));
  assert.ok(!html.includes('aria-activedescendant'));
  const empty = renderToStaticMarkup(
    createElement(Tui, { ...props, commands: [] }),
  );
  assert.ok(!empty.includes('role="listbox"'));
  assert.ok(!empty.includes('add-resource'));
});

test('locale changes generic UI, labels override it, and host command copy is preserved', () => {
  const chinese = renderToStaticMarkup(
    createElement(Tui, { ...props, locale: 'zh' }),
  );
  for (const text of ['宿主操作', '刷新宿主索引', '执行成功'])
    assert.ok(chinese.includes(text), text);
  const override = renderToStaticMarkup(
    createElement(Tui, {
      ...props,
      locale: 'zh',
      labels: {
        success: 'Custom status',
        allCommands: 'Custom catalogue',
      },
    }),
  );
  assert.ok(override.includes('Custom status'));
  assert.ok(override.includes('Custom catalogue'));
  assert.ok(!chinese.includes('aria-activedescendant'));
  const english = renderToStaticMarkup(createElement(Tui, props));
  assert.ok(english.includes('Succeeded'));
  assert.ok(english.includes('刷新宿主索引'));
});
