import assert from 'node:assert/strict';
import test from 'node:test';
import {
  fixture,
  tree,
  mount,
  target,
  rootUri,
  act,
  ok,
  click,
} from './helpers/preview-native-dom.mjs';
import { deferred } from './helpers/module-dom.mjs';

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

for (const [label, hasConflict] of [
  ['Open Wiki dashboard', false],
  ['View pending Wiki conflicts', true],
]) {
  test(`${label} shows the directory name while the preview is loading`, async () => {
    const summary = deferred();
    const fetch = fixture(
      target,
      true,
      ({ path, uri }) => {
        if (path.endsWith('/fs/ls') && uri === rootUri)
          return ok([
            directory(target, [
              'compile_target=true',
              `has_conflict=${hasConflict}`,
            ]),
          ]);
        if (uri === target && /\/content\/(abstract|overview)$/.test(path))
          return summary.promise.then(ok);
      },
      [{ to: target }],
    );
    const view = await mount(tree(fetch, undefined));
    const tabTitle = () =>
      document.querySelector(
        `[role="tab"][data-key="${target}"] .ov-file-preview__tab-title`,
      )?.textContent;
    try {
      await click(row('wiki')?.querySelector(`[aria-label="${label}"]`));
      assert.ok(fetch.calls.some(({ path }) => path.endsWith('/abstract')));
      assert.equal(tabTitle(), 'wiki');
      await act(async () => summary.resolve('Summary'));
      assert.equal(tabTitle(), 'wiki');
    } finally {
      await view.unmount();
    }
  });
}

test('descendant conflicts mark every collapsed Wiki ancestor across pages and clear after refresh', async () => {
  let conflicts = true;
  const fetch = fixture(target, true, ({ path, uri, query }) => {
    if (!path.endsWith('/fs/ls')) return;
    if (uri === rootUri)
      return ok([
        directory(target, ['compile_target=true', 'has_conflict=false']),
        directory(`${target}-old`),
      ]);
    if (uri === target) {
      const entries = [
        ...Array.from({ length: 100 }, (_, i) => ({
          uri: `${target}/${i}.md`,
        })),
        directory(concept),
        directory(`${target}/safe`),
      ];
      const offset = Number(query.offset ?? 0);
      const limit = Number(query.limit ?? query.node_limit ?? 1000);
      return ok(
        entries.slice(offset, offset + limit),
        offset + limit < entries.length,
      );
    }
    if (uri === concept) return ok([directory(deep)]);
    if (uri === deep)
      return ok([
        {
          uri: `${deep}/conflict.md`,
          tags: [conflicts ? 'has_conflict=true' : 'has_conflict=false'],
        },
      ]);
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
    assert.equal(row('deep').getAttribute('aria-expanded'), 'true');
    assert.equal(pending('conflict.md'), true);
    await toggle('concept');
    await toggle('wiki');
    assert.equal(pending('wiki'), true);
    const indexed = () =>
      fetch.calls.filter((call) => call.query.recursive === 'false');
    assert.equal(
      indexed().length,
      5,
      'expanding and collapsing does not refetch the index',
    );
    conflicts = false;
    await click(document.querySelector('[aria-label="Refresh"]'));
    assert.equal(pending('wiki'), false);
    assert.equal(indexed().length, 10);
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
      if (path.endsWith('/fs/ls') && query.recursive === 'false')
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
          : ok([{ uri: `${target}/conflict.md`, tags: ['has_conflict=true'] }]);
    },
    [{ to: target }],
  );
  const view = await mount(tree(fetch, null, { dashboardKinds: [] }));
  try {
    assert.equal(
      fetch.calls.some((call) => call.query.recursive === 'false'),
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
