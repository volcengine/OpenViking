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

const concept = `${target}/concept`;
const deep = `${concept}/deep`;
const directory = (uri, tags = ['has_conflict=false']) => ({
  uri,
  isDir: true,
  tags,
});
const row = (name) =>
  [...document.querySelectorAll('[role="treeitem"]')].find(
    (node) => node.querySelector('.ov-file-tree__label')?.textContent === name,
  );
const pending = (name) =>
  Boolean(
    row(name)?.querySelector('[aria-label="View pending Wiki conflicts"]'),
  );
const toggle = async (name) =>
  click(row(name)?.querySelector('[data-role="toggle"]'));

test('descendant conflicts mark every collapsed Wiki ancestor across pages and clear after refresh', async () => {
  let conflicts = true;
  const fetch = fixture(target, true, ({ path, uri, query }) => {
    if (!path.endsWith('/fs/ls')) return;
    if (query.recursive === 'true' && query.tags === 'has_conflict=true') {
      assert.equal(uri, target);
      assert.equal(query.limit, '100');
      if (!conflicts) return ok([]);
      return query.offset === '0'
        ? ok(
            Array.from({ length: 100 }, (_, i) => ({
              uri: `${target}/other/${i}.md`,
            })),
            true,
          )
        : ok([
            { uri: `${deep}/conflict.md` },
            { uri: `${target}-old/safe/foreign.md` },
          ]);
    }
    if (uri === rootUri)
      return ok([
        directory(target, ['compile_target=true', 'has_conflict=false']),
        directory(`${target}-old`),
      ]);
    if (uri === target)
      return ok([directory(concept), directory(`${target}/safe`)]);
    if (uri === concept) return ok([directory(deep)]);
    if (uri === deep)
      return ok([{ uri: `${deep}/conflict.md`, tags: ['has_conflict=true'] }]);
    return ok([]);
  });
  const view = await mount(tree(fetch, null));
  try {
    assert.equal(pending('wiki'), true);
    assert.equal(row('concept'), undefined);
    assert.equal(pending('wiki-old'), false);
    await toggle('wiki');
    assert.equal(pending('concept'), true);
    assert.equal(pending('safe'), false);
    assert.equal(row('deep'), undefined);
    await toggle('concept');
    assert.equal(pending('deep'), true);
    await toggle('deep');
    assert.equal(pending('conflict.md'), true);
    await toggle('concept');
    await toggle('wiki');
    assert.equal(pending('wiki'), true);
    const indexed = () =>
      fetch.calls.filter((call) => call.query.recursive === 'true');
    assert.equal(
      indexed().length,
      2,
      'expanding and collapsing does not refetch the index',
    );
    conflicts = false;
    await click(document.querySelector('[aria-label="Refresh"]'));
    assert.equal(pending('wiki'), false);
    assert.equal(indexed().length, 3);
    await toggle('wiki');
    assert.equal(pending('concept'), false);
  } finally {
    await view.unmount();
  }
});

test('Wiki conflict indexes are disabled with Wiki dashboards and retry after a failed read', async () => {
  let fail = true;
  const fetch = fixture(
    target,
    true,
    ({ path, query }) => {
      if (path.endsWith('/fs/ls') && query.recursive === 'true')
        return fail
          ? Promise.resolve(
              new Response(
                JSON.stringify({
                  status: 'error',
                  error: {
                    code: 'INTERNAL',
                    message: 'Conflict index unavailable',
                  },
                }),
                { status: 500 },
              ),
            )
          : ok([{ uri: `${deep}/conflict.md` }]);
    },
    [{ to: target }],
  );
  const view = await mount(tree(fetch, null, { dashboardKinds: [] }));
  try {
    assert.equal(
      fetch.calls.some((call) => call.query.recursive === 'true'),
      false,
    );
    await view.render(tree(fetch, null));
    assert.equal(pending('wiki'), false);
    assert.match(view.container.textContent, /Conflict index unavailable/);
    fail = false;
    await click(document.querySelector('[aria-label="Refresh"]'));
    assert.equal(pending('wiki'), true);
  } finally {
    await view.unmount();
  }
});
