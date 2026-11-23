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

const row = (name) =>
  [...document.querySelectorAll('[role="treeitem"]')].find(
    (node) => node.querySelector('.ov-file-tree__label')?.textContent === name,
  );
const pending = (name) =>
  Boolean(
    row(name)?.querySelector('[aria-label="View pending Wiki conflicts"]'),
  );
const toggle = (name) =>
  click(row(name)?.querySelector('[data-role="toggle"]'));
const directory = (uri, tags = ['has_conflict=false']) => ({
  uri,
  isDir: true,
  tags,
});

test('conflicts beyond the native recursive depth limit mark ancestors before they are expanded', async () => {
  const directories = [target];
  for (const segment of ['a', 'b', 'c', 'd'])
    directories.push(`${directories.at(-1)}/${segment}`);
  const file = `${directories.at(-1)}/conflict.md`;
  const fetch = fixture(target, true, ({ path, uri, query }) => {
    if (!path.endsWith('/fs/ls')) return;
    if (uri === rootUri)
      return ok([directory(target, ['compile_target=true'])]);
    // A filtered recursive listing stops after three levels on the native server.
    // Its empty result and has_more=false do not cover this depth-five conflict.
    if (query.recursive === 'true') return ok([]);
    const index = directories.indexOf(uri);
    if (index < 0) return ok([]);
    if (query.recursive === 'false') {
      assert.equal(query.include_tags, 'true');
      assert.equal(
        query.tags,
        undefined,
        'untagged directories must still be traversed',
      );
    }
    return ok(
      index + 1 < directories.length
        ? [directory(directories[index + 1])]
        : [
            { uri: file, tags: ['has_conflict=true'] },
            { uri: `${target}-old/foreign.md`, tags: ['has_conflict=true'] },
          ],
    );
  });
  const view = await mount(tree(fetch, null));
  try {
    assert.equal(pending('wiki'), true);
    assert.equal(row('a'), undefined);
    await toggle('wiki');
    for (const name of ['wiki', 'a', 'b', 'c', 'd']) {
      assert.equal(pending(name), true, name);
      assert.equal(row(name).getAttribute('aria-expanded'), 'true', name);
    }
    assert.equal(pending('conflict.md'), true);
    await toggle('wiki');
    assert.equal(pending('wiki'), true);
    const reads = fetch.calls.filter(
      (call) => call.query.recursive === 'false',
    );
    assert.deepEqual(
      reads.map((call) => call.uri),
      directories,
    );
  } finally {
    await view.unmount();
  }
});

test('newly visible conflict tags override an older empty subtree index and clear on refresh', async () => {
  let conflicted = false;
  const fetch = fixture(target, true, ({ path, uri }) => {
    if (!path.endsWith('/fs/ls')) return;
    if (uri === rootUri)
      return ok([
        directory(target, ['compile_target=true', 'has_conflict=false']),
      ]);
    return ok([
      {
        uri: `${target}/fresh.md`,
        tags: [conflicted ? 'has_conflict=true' : 'has_conflict=false'],
      },
    ]);
  });
  const view = await mount(tree(fetch, null));
  try {
    assert.equal(pending('wiki'), false);
    conflicted = true;
    await toggle('wiki');
    assert.equal(pending('fresh.md'), true);
    assert.equal(pending('wiki'), true);
    assert.equal(
      fetch.calls.filter((call) => call.query.recursive === 'false').length,
      1,
    );
    conflicted = false;
    await click(document.querySelector('[aria-label="Refresh"]'));
    assert.equal(pending('fresh.md'), false);
    assert.equal(pending('wiki'), false);
  } finally {
    await view.unmount();
  }
});
