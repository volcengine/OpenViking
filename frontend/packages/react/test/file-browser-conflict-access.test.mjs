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

const scenarios = [
  { deniedFirst: true, capped: false },
  { deniedFirst: false, capped: false },
  { deniedFirst: true, capped: true },
];

for (const { deniedFirst, capped } of scenarios) {
  test(`denied placeholders do not block readable siblings; denied-first=${deniedFirst}, capped-search=${capped}`, async () => {
    const fetch = fixture(target, true, ({ path, uri, body }) => {
      if (capped && path.endsWith('/search/find')) {
        assert.deepEqual(body, {
          query: '',
          target_uri: target,
          tags: ['has_conflict=true'],
          level: 2,
          limit: 100,
        });
        // The readable conflict is beyond the search result limit.
        return ok({
          resources: Array.from({ length: 100 }, (_, index) => ({
            uri: `${target}/indexed-${index}.md`,
            level: 2,
          })),
        });
      }
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
          { uri: privateUri, isDir: true, access: 'denied' },
          { uri: publicUri, isDir: true, access: 'container' },
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
      if (capped) assert.equal(fetch.countOf('/api/v1/search/find'), 1);
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
