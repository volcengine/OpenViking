import assert from 'node:assert/strict';
import { test } from 'node:test';
import { mount, record, click } from './helpers/command-console-dom.mjs';

test('JSON URI fields and structured results link the complete URI', async () => {
  const uri = 'viking://resources/dir with whitespace';
  const opened = [];
  const view = await mount({
    records: [
      record({ variant: 'text', text: JSON.stringify({ uri }) }),
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
      ...view.container.querySelectorAll('.ov-command-console__result-body a'),
    ];
    assert.equal(links.length, 6);
    for (const link of links) {
      assert.equal(link.getAttribute('href'), uri);
      await click(link);
    }
    assert.deepEqual(opened, Array(6).fill(uri));
  } finally {
    await view.dispose();
  }
});

async function assertInlineCodeLinks(snippet, uris) {
  const opened = [];
  const view = await mount({
    records: [
      record({
        variant: 'text',
        format: 'markdown',
        text: '`' + snippet + '`',
      }),
    ],
    onOpenUri: (target) => opened.push(target),
  });
  try {
    const code = view.container.querySelector(
      '.ov-command-console__result-body code',
    );
    const links = [...code.querySelectorAll('a')];
    assert.equal(code.textContent, snippet);
    assert.deepEqual(
      links.map((link) => link.getAttribute('href')),
      uris,
    );
    for (const link of links) await click(link);
    assert.deepEqual(opened, uris);
  } finally {
    await view.dispose();
  }
}

test('markdown inline code links each URI to its own target', async () => {
  const uris = ['viking://resources/a.md', 'viking://resources/b.md'];
  await assertInlineCodeLinks(uris.join(' -> '), uris);
});

test('markdown inline code leaves a local destination outside the resource link', async () => {
  const uri = 'viking://resources/a.md';
  await assertInlineCodeLinks(`${uri} -> /tmp/a.md`, [uri]);
});

test('JSON message links only the resource URI before a local destination', async () => {
  const uri = 'viking://resources/a.md';
  const message = `${uri} -> /tmp/a.md`;
  const opened = [];
  const view = await mount({
    records: [record({ variant: 'text', text: JSON.stringify({ message }) })],
    onOpenUri: (target) => opened.push(target),
  });
  try {
    const result = view.container.querySelector(
      '.ov-command-console__code-content',
    );
    const links = [...result.querySelectorAll('a')];
    assert.deepEqual(JSON.parse(result.textContent), { message });
    assert.deepEqual(
      links.map((link) => link.getAttribute('href')),
      [uri],
    );
    await click(links[0]);
    assert.deepEqual(opened, [uri]);
  } finally {
    await view.dispose();
  }
});

test('quoted markdown inline code links each resource URI separately', async () => {
  const uris = ['viking://resources/a.md', 'viking://resources/b.md'];
  await assertInlineCodeLinks(`"${uris.join(' -> ')}"`, uris);
});
