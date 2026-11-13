import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { JSDOM } from 'jsdom';
import { CommandConsole } from '@openviking/react/components/command-console';

const render = (content, extra = {}) => {
  const html = renderToStaticMarkup(
    createElement(CommandConsole, {
      value: '',
      records: [{ id: 'one', command: 'custom', status: 'success', content }],
      ...extra,
    }),
  );
  return new JSDOM(html).window.document;
};

test('structured resource targets reject executable schemes and retain safe native navigation', () => {
  for (const uri of [
    'javascript:alert(1)',
    'data:text/html,bad',
    'JaVaScRiPt:alert(1)',
    'java\nscript:alert(1)',
  ]) {
    const contents = [
      { variant: 'search-list', items: [{ id: 'one', uri }] },
      { variant: 'resource-preview', title: 'Preview', uri, content: 'Body' },
      {
        variant: 'resource-preview',
        title: 'Preview',
        uri: 'viking://resources/a.md',
        navigationUri: uri,
        content: 'Body',
      },
      { variant: 'resource-tree', items: [{ id: 'one', title: 'File', uri }] },
    ];
    for (const content of contents) {
      const document = render(content);
      assert.ok(!document.querySelector('a'), JSON.stringify(content));
      assert.ok(
        document
          .querySelector('.ov-command-console__result-body')
          .textContent.trim(),
      );
    }
  }
  for (const uri of [
    'viking://resources/a.md',
    'https://example.com/a',
    'http://example.com/a',
  ]) {
    assert.equal(
      render({
        variant: 'resource-tree',
        items: [{ id: 'one', title: 'File', uri }],
      })
        .querySelector('a')
        .getAttribute('href'),
      uri,
    );
  }
});

test('JSON indentation preserves exact numeric tokens, negative zero, exponents, escapes and duplicate keys', () => {
  const text = String.raw`{"big":9007199254740993,"negative":-0,"huge":1e400,"precise":0.10000000000000001,"same":1,"same":2,"nested":[{},[],"a\"b"]}`;
  const document = render({ variant: 'text', text });
  const output = document.querySelector(
    '.ov-command-console__code-content',
  ).textContent;
  assert.equal(output.replace(/\s+/g, ''), text);
  assert.match(output, /\n {2}"big":/);
  assert.equal(
    render({ variant: 'text', text: '{invalid}' }).querySelector(
      '.ov-command-console__plain',
    ).textContent,
    '{invalid}',
  );
});

test('mixed resource tables preserve each title, URI target and metadata, including rows without details', () => {
  const document = render(
    {
      variant: 'resource-tree',
      items: [
        {
          id: 'a',
          title: 'a.md',
          uri: 'viking://resources/a.md',
          metaLabel: 'File A',
          detailRows: [{ label: 'Size', value: '2 KB' }],
        },
        {
          id: 'b',
          title: 'folder/',
          uri: 'viking://resources/folder',
          metaLabel: 'Folder B',
        },
      ],
    },
    { locale: 'zh-CN', labels: { resource: 'Entry' } },
  );
  assert.equal(document.querySelector('th').textContent, 'Entry');
  const rows = document.querySelectorAll('tbody tr');
  assert.equal(rows.length, 2);
  assert.match(rows[0].textContent, /a.mdFile A2 KB/);
  assert.match(rows[1].textContent, /folder\/Folder B/);
  assert.equal(
    rows[1].querySelector('a').getAttribute('href'),
    'viking://resources/folder',
  );
});

test('sentence punctuation is excluded from prose links but preserved in editor, command and code URIs', () => {
  const uri = 'viking://resources/a.md.';
  for (const format of ['plain', 'markdown']) {
    const document = render({ variant: 'text', format, text: `Open ${uri}` });
    assert.equal(
      document.querySelector('a').getAttribute('href'),
      uri.slice(0, -1),
    );
    assert.equal(
      document.querySelector('.ov-command-console__result-body').textContent,
      `Open ${uri}`,
    );
  }
  const document = render(
    { variant: 'text', format: 'markdown', text: `\`${uri}\`` },
    {
      value: `ov read ${uri}`,
      records: [
        {
          id: 'one',
          command: `ov read ${uri}`,
          status: 'success',
          content: { variant: 'text', format: 'markdown', text: `\`${uri}\`` },
        },
      ],
    },
  );
  for (const link of document.querySelectorAll('a'))
    assert.equal(link.getAttribute('href'), uri);
});
