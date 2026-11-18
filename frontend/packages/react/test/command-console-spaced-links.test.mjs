import assert from 'node:assert/strict';
import { test } from 'node:test';
import { mount, record, click } from './helpers/command-console-dom.mjs';

test('quoted input, JSON, inline code and structured results link the complete URI', async () => {
  const uri = 'viking://resources/dir with whitespace';
  const opened = [];
  const view = await mount({
    value: `ov read "${uri}"`,
    records: [
      record({ variant: 'text', text: JSON.stringify({ uri }) }),
      {
        ...record({ variant: 'text', text: `See "${uri}" and continue.` }),
        id: 'prose',
      },
      {
        ...record({
          variant: 'text',
          format: 'markdown',
          text: 'See `' + uri + '` and continue.',
        }),
        id: 'markdown',
      },
      {
        ...record({
          variant: 'status',
          rows: [{ label: 'root_uri', value: uri }],
        }),
        id: 'status',
      },
      {
        ...record({
          variant: 'search-list',
          items: [{ id: 'a', title: 'Item', uri, metaLabel: uri }],
        }),
        id: 'search',
      },
      {
        ...record({
          variant: 'resource-tree',
          items: [
            {
              id: 'a',
              title: 'Item',
              uri,
              detailRows: [{ label: 'URI', value: uri }],
            },
          ],
        }),
        id: 'tree',
      },
    ],
    onOpenUri: (target) => opened.push(target),
  });
  try {
    const links = [
      ...view.container.querySelectorAll(
        '.ov-command-console__result-body a, .ov-command-console__input-mirror a',
      ),
    ];
    assert.equal(links.length, 9);
    for (const link of links) {
      assert.equal(link.getAttribute('href'), uri);
      await click(link);
    }
    assert.deepEqual(opened, Array(9).fill(uri));
  } finally {
    await view.dispose();
  }
});
