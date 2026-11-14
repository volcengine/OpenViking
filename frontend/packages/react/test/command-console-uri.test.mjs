import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createElement } from 'react';
import { renderMarkup } from './helpers/csr-render.mjs';
import { CommandConsole } from '@openviking/react/components/command-console';

const value = 'ov cp "viking://resources/a.md" viking://resources/b.md';
const props = {
  value,
  records: [{ id: 'uri', command: value, status: 'success' }],
  onValueChange() {},
  onOpenUri() {},
};

test('input and command echoes render URI anchors without changing the native value', () => {
  const html = renderMarkup(createElement(CommandConsole, props));
  for (const uri of ['viking://resources/a.md', 'viking://resources/b.md']) {
    assert.equal(html.split(`href="${uri}"`).length - 1, 2);
  }
  assert.match(
    html,
    /<textarea[^>]*>ov cp "viking:\/\/resources\/a.md" viking:\/\/resources\/b.md<\/textarea>/,
  );
  assert.doesNotMatch(
    html,
    /class="ov-command-console__input-mirror"[^>]*aria-hidden/,
  );
  assert.doesNotMatch(html, /<label[^>]*class="ov-command-console__input-row"/);
});

test('literal URI links preserve punctuation and native targets while ignoring non-resource text', () => {
  const text = 'VIKING://resources/a.md, error: denied; javascript:alert(1)';
  const html = renderMarkup(
    createElement(CommandConsole, {
      value: text,
      records: [],
      onValueChange() {},
    }),
  );
  assert.match(html, /href="VIKING:\/\/resources\/a.md,"/);
  assert.doesNotMatch(html, /href="(?:javascript|error):/);
  assert.match(html, /error: denied; javascript:alert/);
});

test('status fields and write-result lines use the same URI links', () => {
  const uri = 'viking://resources/a.md';
  const records = [
    {
      id: 'status',
      command: 'ov stat',
      status: 'success',
      content: { variant: 'status', rows: [{ label: 'URI', value: uri }] },
    },
    {
      id: 'write',
      command: 'ov write',
      status: 'success',
      content: { variant: 'write-result', lines: [`Written: ${uri}`] },
    },
  ];
  const html = renderMarkup(
    createElement(CommandConsole, { ...props, value: '', records }),
  );
  assert.equal(html.split(`href="${uri}"`).length - 1, 2);
});

test('result headings, summaries and embedded tree values also link URIs', () => {
  const uri = 'viking://resources/a.md';
  const contents = [
    { variant: 'status', title: uri, rows: [] },
    { variant: 'write-result', title: uri, lines: [] },
    { variant: 'resource-preview', title: uri, description: uri, content: '' },
    {
      variant: 'resource-tree',
      title: uri,
      items: [
        {
          id: 'row',
          title: 'a',
          detailRows: [{ label: 'URI', value: `File: ${uri}` }],
        },
      ],
    },
    { variant: 'search-list', summary: `Scope: ${uri}`, items: [] },
    {
      variant: 'resource-tree',
      items: [{ id: 'uri', title: uri, uri, metaLabel: uri }],
    },
  ];
  const records = contents.map((content, id) => ({
    id: String(id),
    command: 'custom',
    status: 'success',
    content,
  }));
  const html = renderMarkup(
    createElement(CommandConsole, { ...props, value: '', records }),
  );
  assert.equal(html.split(`href="${uri}"`).length - 1, 9);
  for (const [, body] of html.matchAll(/<button\b[^>]*>([^]*?)<\/button>/g)) {
    assert.doesNotMatch(body, /<(?:a|button)\b/);
  }
});

test('Markdown links URI text and aliases while retaining the default URL filter', () => {
  const uri = 'viking://resources/a.md';
  const text = `# ${uri}\n\nPlain ${uri}, **${uri}**\n\n- ${uri}\n\n[Resource](${uri})\n\n| URI |\n| --- |\n| ${uri} |\n\n[unsafe](javascript:alert%281%29)`;
  const html = renderMarkup(
    createElement(CommandConsole, {
      ...props,
      value: '',
      records: [
        {
          id: 'markdown',
          command: 'custom',
          status: 'success',
          content: { variant: 'text', format: 'markdown', text },
        },
      ],
    }),
  );
  assert.equal(html.split(`href="${uri}"`).length - 1, 6);
  assert.match(html, /<a href="viking:\/\/resources\/a.md"[^>]*>Resource<\/a>/);
  assert.doesNotMatch(html, /href="javascript:/);
  for (const [, body] of html.matchAll(/<a\b[^>]*>([^]*?)<\/a>/g)) {
    assert.doesNotMatch(body, /<a\b/);
  }
});
