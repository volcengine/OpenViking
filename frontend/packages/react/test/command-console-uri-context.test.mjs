import assert from 'node:assert/strict';
import { test } from 'node:test';
import { mount, record, click, act } from './helpers/command-console-dom.mjs';

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
  for (const [first, second] of [
    ['viking://resources/a.md', 'viking://resources/b.md'],
    ['https://example.com/a.md', 'http://example.com/b.md'],
  ]) {
    const text = `See ${first}, then continue. Read ${second}. Then retry.`;
    for (const content of proseContents(text)) {
      const opened = [];
      const view = await mount({
        records: [record(content)],
        onOpenUri: (uri) => opened.push(uri),
      });
      try {
        const body = view.container.querySelector(
          '.ov-command-console__result-body',
        );
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
        if (content.format === 'markdown' && first.startsWith('http')) {
          // Markdown keeps its existing external-navigation behavior.
          assert.ok(links.every((link) => link.target === '_blank'));
          assert.equal(body.querySelector('a a'), null);
        } else {
          for (const link of links) await click(link);
          assert.deepEqual(opened, [first, second]);
        }
      } finally {
        await view.dispose();
      }
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
      const link = view.container.querySelector(
        '.ov-command-console__result-body a',
      );
      assert.ok(link, JSON.stringify(content));
      assert.equal(link.getAttribute('href'), uri);
      await click(link);
      assert.deepEqual(opened, [uri]);
    } finally {
      await view.dispose();
    }
  }
});

test('web URLs in the editor keep their exact value, native href and host callback without submitting', async () => {
  const uri = 'https://example.com/a;b,(c)[d].md?x=1&y=2';
  const value = `ov read "${uri}"`;
  const opened = [];
  const submitted = [];
  const view = await mount({
    value,
    onOpenUri: (target) => opened.push(target),
    onSubmit: (command) => submitted.push(command),
  });
  try {
    const link = view.container.querySelector(
      '.ov-command-console__input-mirror a',
    );
    assert.equal(link.getAttribute('href'), uri);
    await act(() => view.input.focus());
    const down = new window.MouseEvent('mousedown', {
      bubbles: true,
      cancelable: true,
    });
    await act(() => link.dispatchEvent(down));
    assert.equal(down.defaultPrevented, true);
    await act(() => link.click());
    assert.equal(document.activeElement, view.input);
    assert.equal(view.input.value, value);
    assert.deepEqual(opened, [uri]);
    assert.deepEqual(submitted, []);
  } finally {
    await view.dispose();
  }
});
