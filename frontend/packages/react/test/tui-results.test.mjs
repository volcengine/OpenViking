import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
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
      locale: 'zh-CN',
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
