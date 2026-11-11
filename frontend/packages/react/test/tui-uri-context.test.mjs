import assert from 'node:assert/strict';
import { test } from 'node:test';
import { mount, record, click } from './helpers/tui-dom.mjs';

const proseContents = (text) => [
  { variant: 'search-list', summary: text, items: [] },
  {
    variant: 'search-list',
    items: [{ id: 'a', title: 'Item', preview: text }],
  },
  {
    variant: 'search-list',
    items: [{ id: 'a', title: 'Item', metaLabel: text }],
  },
  {
    variant: 'resource-preview',
    title: 'Item',
    description: text,
    content: '',
  },
  { variant: 'resource-preview', title: 'Item', meta: [text], content: '' },
  {
    variant: 'resource-tree',
    items: [{ id: 'a', title: 'Item', metaLabel: text }],
  },
  { variant: 'status', title: text, rows: [] },
  { variant: 'write-result', title: text, lines: [] },
  { variant: 'write-result', lines: [text] },
  { variant: 'text', text },
  { variant: 'text', format: 'markdown', text },
];

test('all descriptive text keeps sentence punctuation outside native links and callback targets', async () => {
  const first = 'viking://resources/a.md';
  const second = 'viking://resources/b.md';
  const text = `See ${first}, then continue. Read ${second}. Then retry.`;
  for (const content of proseContents(text)) {
    const opened = [];
    const view = await mount({
      records: [record(content)],
      onOpenUri: (uri) => opened.push(uri),
    });
    try {
      const body = view.container.querySelector('.ov-tui__result-body');
      const links = [...body.querySelectorAll('a')];
      assert.deepEqual(
        links.map((link) => link.getAttribute('href')),
        [first, second],
        JSON.stringify(content),
      );
      assert.equal(
        links[0].nextSibling.textContent.startsWith(', then continue.'),
        true,
      );
      assert.ok(body.textContent.includes(text));
      for (const link of links) await click(link);
      assert.deepEqual(opened, [first, second]);
    } finally {
      await view.dispose();
    }
  }
});

test('resource identities and raw structured values retain literal filename punctuation', async () => {
  const uri = 'viking://resources/a;b,(c)[d].md.';
  const contents = [
    { variant: 'search-list', items: [{ id: 'a', title: uri }] },
    { variant: 'search-list', items: [{ id: 'a', title: 'Item', uri }] },
    { variant: 'resource-preview', title: uri, content: '' },
    { variant: 'resource-preview', title: 'Item', uri, content: '' },
    { variant: 'resource-preview', title: 'Item', content: uri },
    { variant: 'resource-tree', title: uri, items: [] },
    { variant: 'resource-tree', items: [{ id: 'a', title: uri }] },
    { variant: 'resource-tree', items: [{ id: 'a', title: 'Item', uri }] },
    {
      variant: 'resource-tree',
      items: [
        { id: 'a', title: 'Item', detailRows: [{ label: 'URI', value: uri }] },
      ],
    },
    { variant: 'status', rows: [{ label: 'URI', value: uri }] },
  ];
  for (const content of contents) {
    const opened = [];
    const view = await mount({
      records: [record(content)],
      onOpenUri: (target) => opened.push(target),
    });
    try {
      const link = view.container.querySelector('.ov-tui__result-body a');
      assert.ok(link, JSON.stringify(content));
      assert.equal(link.getAttribute('href'), uri);
      await click(link);
      assert.deepEqual(opened, [uri]);
    } finally {
      await view.dispose();
    }
  }
});
