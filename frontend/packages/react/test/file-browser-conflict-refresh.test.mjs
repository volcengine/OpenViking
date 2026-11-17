import assert from 'node:assert/strict';
import test from 'node:test';
import {
  fixture,
  tree,
  mount,
  target,
  rootUri,
  ok,
  click,
} from './helpers/preview-native-dom.mjs';

test('a fresh empty conflict index clears an older positive registry count', async () => {
  let conflicted = true;
  const fetch = fixture(
    target,
    true,
    ({ path, uri, query }) => {
      if (!path.endsWith('/fs/ls')) return;
      if (uri === rootUri)
        return ok([
          { uri: target, isDir: true, tags: ['compile_target=true'] },
        ]);
      if (query.tags && !conflicted) return ok([]);
      return ok([
        {
          uri: `${target}/file.md`,
          tags: [conflicted ? 'has_conflict=true' : 'has_conflict=false'],
        },
      ]);
    },
    [{ to: target, conflicts_num: 1 }],
  );
  const view = await mount(tree(fetch, target));
  const root = () =>
    [...view.container.querySelectorAll('[role="treeitem"]')].find(
      (node) =>
        node.querySelector('.ov-file-tree__label')?.textContent === 'wiki',
    );
  const marker = () =>
    root().querySelector('[aria-label="View pending Wiki conflicts"]');
  try {
    assert.ok(marker());
    await click(marker());
    assert.equal(fetch.countOf('/api/v1/compile/configs'), 1);
    conflicted = false;
    await click(view.container.querySelector('[aria-label="Refresh"]'));
    assert.equal(fetch.countOf('/api/v1/compile/configs'), 2);
    assert.equal(Boolean(marker()), false);
  } finally {
    await view.unmount();
  }
});
