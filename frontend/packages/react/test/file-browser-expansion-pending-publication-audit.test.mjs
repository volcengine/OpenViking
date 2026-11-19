import assert from 'node:assert/strict';
import test from 'node:test';
import { act, createElement as h } from 'react';
import { QueryClient } from '@tanstack/react-query';
import { OpenVikingProvider } from '@openviking/react/client';
import { FileBrowser } from '@openviking/react/modules/file-browser';
import {
  deferred,
  mount,
  ok,
  recordingFetch,
  settle,
} from './helpers/module-dom.mjs';

const root = 'viking://~/resources';
const parent = `${root}/folder`;
const child = `${parent}/child`;
const sibling = `${root}/sibling`;
const leaf = `${child}/result.md`;
const siblingLeaf = `${sibling}/result.md`;
const homeAlias = 'viking://~';
const listPath = '/api/v1/fs/ls';
const directory = (uri) => ({ uri, isDir: true });
const row = (view, uri) =>
  view.container.querySelector(`[data-file-tree-row-id="${uri}"]`);
const expanded = (view, uri) => row(view, uri)?.getAttribute('aria-expanded');

function browser(fetch, defaultExpansionSnapshot, onExpansionSnapshotChange) {
  return h(
    OpenVikingProvider,
    {
      fetch,
      baseURL: 'https://ov.test',
      queryClient: new QueryClient({
        defaultOptions: { queries: { retry: false } },
      }),
    },
    h(FileBrowser, {
      rootUri: root,
      dashboardKinds: [],
      defaultExpansionSnapshot,
      onExpansionSnapshotChange,
    }),
  );
}

test('a fold is persisted before another saved branch finishes loading page 2', async (t) => {
  const home = deferred();
  const secondPage = deferred();
  let recovered = false;
  const fetch = recordingFetch(({ path, uri, query }) => {
    if (path === '/api/v1/fs/stat' && uri === homeAlias)
      return recovered
        ? ok({ uri: 'viking://user/other', isDir: true })
        : home.promise;
    if (path !== listPath) return ok('');
    if (uri === root) return ok([directory(parent), directory(sibling)]);
    if (uri === parent) {
      if (Number(query.offset) > 0)
        return recovered ? ok([directory(child)]) : secondPage.promise;
      return ok([{ uri: `${parent}/first.md` }], true);
    }
    return ok([{ uri: uri === sibling ? siblingLeaf : leaf }]);
  });
  let saved = {
    expandedUris: [parent, child, sibling],
    collapsedUris: ['viking://user/default/resources/folder'],
  };
  const snapshots = [];
  const persist = (snapshot) => {
    saved = JSON.parse(JSON.stringify(snapshot));
    snapshots.push(saved);
  };
  const view = await mount(browser(fetch, saved, persist));
  try {
    assert.equal(expanded(view, sibling), 'true');
    assert.ok(row(view, siblingLeaf));
    await act(async () =>
      home.resolve(await ok({ uri: 'viking://user/other', isDir: true })),
    );
    await settle();
    const held = fetch.calls.find(
      ({ path, uri, query }) =>
        path === listPath && uri === parent && Number(query.offset) > 0,
    );
    assert.ok(held, 'automatic restoration has requested page 2');
    assert.equal(held.init.signal.aborted, false);
    assert.equal(row(view, child), null);

    const beforeFold = snapshots.length;
    await act(() =>
      row(view, sibling).querySelector('[data-role="toggle"]').click(),
    );
    await settle();
    assert.equal(expanded(view, sibling), 'false');
    assert.equal(held.init.signal.aborted, false);
    await t.test('reports the fold before the pending page responds', () => {
      assert.ok(snapshots.length > beforeFold);
      assert.ok(saved.collapsedUris.includes(sibling));
      assert.deepEqual(new Set(saved.expandedUris), new Set([parent, child]));
    });
  } finally {
    await view.unmount();
  }

  recovered = true;
  const reloaded = await mount(browser(fetch, saved, persist));
  try {
    await t.test(
      'JSON restoration retains the fold and the unresolved saved child',
      () => {
        assert.equal(expanded(reloaded, sibling), 'false');
        assert.equal(row(reloaded, siblingLeaf), null);
        assert.equal(expanded(reloaded, child), 'true');
        assert.ok(row(reloaded, leaf));
        assert.deepEqual(new Set(saved.expandedUris), new Set([parent, child]));
      },
    );
  } finally {
    await reloaded.unmount();
    secondPage.resolve(await ok([directory(child)]));
  }
});
