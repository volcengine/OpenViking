import assert from 'node:assert/strict';
import { after, test } from 'node:test';
import { mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { createRslib } from '@rslib/core';
import { pluginReact } from '@rsbuild/plugin-react';
import { Tui } from '@openviking/react/components/tui';

const command = {
  name: 'inspect',
  inputText: 'tool inspect',
  description: 'Inspect',
  usage: 'tool inspect <uri>',
  skeleton: 'status',
};
const render = (content, extra = {}) =>
  renderToStaticMarkup(
    createElement(Tui, {
      value: '',
      commands: [command],
      locale: 'zh',
      records: [
        {
          id: 'result',
          command: '/inspect viking://resources/demo',
          status: 'success',
          content,
          ...extra,
        },
      ],
    }),
  );

test('all result variants share a canonical command heading and an accessible execution state', () => {
  const contents = [
    { variant: 'text', text: 'Ready' },
    { variant: 'search-list', items: [] },
    { variant: 'resource-preview', title: 'Source', content: 'Body' },
    { variant: 'resource-tree', items: [] },
    { variant: 'status', rows: [{ label: 'Status', value: 'pending' }] },
    { variant: 'write-result', lines: ['Status: pending'] },
    { variant: 'command-help', sections: [] },
  ];
  for (const content of contents) {
    const html = render(content);
    const header = html.match(
      /<header class="ov-tui__result-head">([\s\S]*?)<\/header>/,
    )[1];
    assert.match(header, /ov-tui__result-command">tool inspect<\/span>/);
    assert.match(
      header,
      /data-status="success"[^>]*role="img"[^>]*aria-label="执行成功"/,
    );
    assert.doesNotMatch(header, /viking:\/\//);
    assert.match(html, /<article class="ov-tui__result">/);
    assert.doesNotMatch(html, /ov-tui__status-dot/);
  }
});

test('pending and failed records preserve their own state and body presentation', () => {
  const waiting = render(
    { variant: 'text', text: 'not ready' },
    { status: 'pending' },
  );
  assert.match(waiting, /aria-label="执行中"/);
  assert.match(waiting, /ov-tui__skeleton/);
  assert.doesNotMatch(waiting, /not ready/);
  const failed = render(
    { variant: 'text', format: 'markdown', text: '**Permission denied**' },
    { status: 'error' },
  );
  assert.match(
    failed,
    /data-status="error"[^>]*role="img"[^>]*aria-label="执行失败"/,
  );
  assert.match(failed, /<strong>Permission denied<\/strong>/);
});

test('duplicate command titles are hidden while resource and field titles are retained', () => {
  const hidden = render({
    variant: 'write-result',
    title: '**/inspect**',
    lines: ['accepted'],
  });
  assert.doesNotMatch(hidden, /ov-tui__card-title/);
  const visible = render({
    variant: 'status',
    title: 'Resource details',
    rows: [],
  });
  assert.match(visible, /ov-tui__card-title">Resource details<\/div>/);
  const markdown = render({
    variant: 'text',
    format: 'markdown',
    text: '**/inspect**\n\n- Status: pending\n- Stage: queued',
  });
  assert.doesNotMatch(markdown, /<strong>\/inspect/);
  assert.match(markdown, /<li>Status: pending<\/li>/);
  assert.match(markdown, /<li>Stage: queued<\/li>/);
});

test('JSON and Markdown fences use copyable code blocks and preserve URI links', () => {
  const uri = 'viking://resources/demo';
  const json = render({
    variant: 'text',
    text: JSON.stringify({ uri, text: '<script>bad()</script>' }),
  });
  assert.match(json, /ov-tui__code-block/);
  assert.match(json, /ov-tui__copy--inline/);
  assert.match(json, /href="viking:\/\/resources\/demo"/);
  assert.doesNotMatch(json, /<script>/);
  const markdown = render({
    variant: 'text',
    format: 'markdown',
    text: `\`\`\`json\n{"uri":"${uri}"}\n\`\`\``,
  });
  assert.match(markdown, /ov-tui__code-block/);
  assert.match(markdown, /href="viking:\/\/resources\/demo"/);
  assert.match(markdown, /复制/);
});

test('empty structured results expose the localized empty-result message', () => {
  for (const content of [
    { variant: 'search-list', items: [] },
    { variant: 'resource-tree', items: [] },
  ]) {
    assert.match(render(content), /暂无返回结果/);
  }
});

const output = await mkdtemp(join(tmpdir(), 'ov-tui-results-test-'));
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
                '../../../docs/docs/_demos/tui-demo-result.ts',
                import.meta.url,
              ),
            ),
            commands: fileURLToPath(
              new URL(
                '../../../docs/docs/_demos/tui-commands/index.ts',
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
const { createTuiDemoRecord } = await import(
  pathToFileURL(join(output, 'demo.js'))
);
const { createTuiCommands } = await import(
  pathToFileURL(join(output, 'commands.js'))
);

test('every demo command returns its structured result instead of a generic execution message', () => {
  for (const descriptor of createTuiCommands('zh')) {
    const result = createTuiDemoRecord(`${descriptor.inputText} demo`, 'zh');
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
  const compile = createTuiDemoRecord(
    'ov compile --from viking://resources/demo',
    'zh',
  );
  assert.equal(compile.status, 'success');
  assert.ok(compile.content.lines.includes('Status: pending'));
  assert.match(
    compile.content.lines.find((line) => line.startsWith('Request:')),
    /"mode":"incremental"/,
  );
});
