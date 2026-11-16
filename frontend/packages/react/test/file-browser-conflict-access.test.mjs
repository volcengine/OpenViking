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

const publicUri = `${target}/public`;
const privateUri = `${target}/private`;
const file = `${publicUri}/conflict.md`;
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

for (const deniedFirst of [true, false]) {
  test(`denied placeholders do not block readable siblings; denied-first=${deniedFirst}`, async () => {
    const fetch = fixture(target, true, ({ path, uri }) => {
      if (!path.endsWith('/fs/ls')) return;
      if (uri === rootUri)
        return ok([
          {
            uri: target,
            isDir: true,
            tags: ['compile_target=true', 'has_conflict=false'],
          },
        ]);
      if (uri === target) {
        // A container cannot be previewed itself but permits listing readable children.
        const entries = [
          { uri: publicUri, isDir: true, access: 'container' },
          { uri: privateUri, isDir: true, access: 'denied' },
        ];
        return ok(deniedFirst ? entries : entries.toReversed());
      }
      if (uri === privateUri)
        return Promise.resolve(new Response('Forbidden', { status: 403 }));
      if (uri === publicUri)
        return ok([{ uri: file, tags: ['has_conflict=true'] }]);
      return ok([]);
    });
    const view = await mount(tree(fetch, null));
    try {
      assert.equal(pending('wiki'), true);
      assert.equal(
        fetch.calls.some((call) => call.uri === privateUri),
        false,
      );
      assert.ok(
        fetch.calls.some(
          (call) => call.uri === publicUri && call.query.recursive === 'false',
        ),
      );
      assert.doesNotMatch(
        view.container.textContent,
        /Forbidden|Failed to load dashboard/,
      );
      await toggle('wiki');
      assert.equal(row('private').getAttribute('aria-disabled'), 'true');
      await toggle('public');
      assert.equal(pending('conflict.md'), true);
      assert.equal(pending('wiki'), true);
      assert.equal(
        fetch.calls.some((call) => call.uri === privateUri),
        false,
      );
    } finally {
      await view.unmount();
    }
  });
}
