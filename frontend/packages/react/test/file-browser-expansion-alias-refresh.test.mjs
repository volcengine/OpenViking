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

const homeAlias = 'viking://~';
const home = 'viking://user/default';
const root = `${homeAlias}/resources`;
const alias = `${root}/folder`;
const canonical = `${home}/resources/folder`;
const file = (uri) => `${uri}/result.md`;
const listPath = '/api/v1/fs/ls';
const homeRequest = ({ path, uri }) =>
  path === '/api/v1/fs/stat' && uri === homeAlias;

const row = (view, uri) =>
  view.container.querySelector(`[data-file-tree-row-id="${uri}"]`);

async function fixture(saved) {
  let spelling = alias;
  let failAlias = false;
  let pendingRoot;
  const fetch = recordingFetch((call) => {
    const { path, uri } = call;
    if (homeRequest(call)) return ok({ uri: home, isDir: true });
    if (path !== listPath) return ok('');
    if (uri === root) {
      const listRoot = () => ok([{ uri: spelling, isDir: true }]);
      return pendingRoot ? pendingRoot.promise.then(listRoot) : listRoot();
    }
    if (uri === alias && failAlias)
      return new Response('Old alias listing failed', { status: 503 });
    return ok([{ uri: file(uri) }]);
  });
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const snapshots = [];
  const view = await mount(
    h(
      OpenVikingProvider,
      { fetch, baseURL: 'https://ov.test', queryClient: client },
      h(FileBrowser, {
        rootUri: root,
        dashboardKinds: [],
        defaultExpandedUris: saved ? [saved] : [],
        onExpandedUrisChange: (uris) => snapshots.push([...uris]),
      }),
    ),
  );
  async function refresh() {
    await act(async () => client.invalidateQueries());
    await settle();
  }
  return {
    fetch,
    view,
    snapshots,
    refresh,
    async switchTo(uri, failOldAlias = false) {
      spelling = uri;
      failAlias = failOldAlias;
      if (!failOldAlias) return refresh();

      const pending = deferred();
      pendingRoot = pending;
      let invalidating;
      await act(() => {
        invalidating = client.invalidateQueries();
      });
      try {
        await settle();
        assert.ok(row(view, `${alias}::error`));
      } finally {
        pendingRoot = undefined;
        await act(async () => {
          pending.resolve();
          await invalidating;
        });
      }
      await settle();
    },
  };
}

function assertExpanded({ view, snapshots }, uri) {
  assert.equal(row(view, uri)?.getAttribute('aria-expanded'), 'true');
  assert.ok(row(view, file(uri)));
  assert.deepEqual(snapshots.at(-1), [uri]);
  assert.ok(snapshots.every((snapshot) => snapshot.length === 1));
}

test('resolves a cold alias when its refreshed listing uses canonical paths', async () => {
  const f = await fixture(alias);
  try {
    assertExpanded(f, alias);
    assert.equal(f.fetch.calls.filter(homeRequest).length, 0);

    await f.switchTo(canonical);
    assert.equal(f.fetch.calls.filter(homeRequest).length, 1);
    assertExpanded(f, canonical);
  } finally {
    await f.view.unmount();
  }
});

test('loads the canonical folder after its previous alias listing fails during refresh', async () => {
  const f = await fixture(canonical);
  try {
    assertExpanded(f, alias);
    assert.equal(f.fetch.calls.filter(homeRequest).length, 1);

    const before = f.fetch.calls.length;
    await f.switchTo(canonical, true);
    const listingUris = f.fetch.calls
      .slice(before)
      .filter(({ path }) => path === listPath)
      .map(({ uri }) => uri);
    assert.ok(listingUris.includes(alias));
    assert.ok(listingUris.includes(canonical));
    assertExpanded(f, canonical);
  } finally {
    await f.view.unmount();
  }
});

test('subsequent invalidations use only the folder URI currently listed', async () => {
  const f = await fixture(canonical);
  try {
    assertExpanded(f, alias);
    for (const uri of [canonical, alias]) {
      await f.switchTo(uri);
      assertExpanded(f, uri);
      const before = f.fetch.calls.length;
      await f.refresh();
      assert.deepEqual(
        f.fetch.calls
          .slice(before)
          .filter(
            ({ path, uri }) =>
              path === listPath && (uri === alias || uri === canonical),
          )
          .map(({ uri }) => uri),
        [uri],
      );
      assertExpanded(f, uri);
    }
  } finally {
    await f.view.unmount();
  }
});

test('a folded folder keeps neither spelling subscribed after the listing changes', async () => {
  const f = await fixture();
  try {
    const toggle = () =>
      act(() =>
        row(f.view, alias).querySelector('[data-role="toggle"]').click(),
      );
    await toggle();
    await settle();
    assert.equal(row(f.view, alias)?.getAttribute('aria-expanded'), 'true');
    assert.ok(row(f.view, file(alias)));
    await toggle();
    await settle();
    assert.equal(row(f.view, alias)?.getAttribute('aria-expanded'), 'false');

    await f.switchTo(canonical);
    assert.equal(
      row(f.view, canonical)?.getAttribute('aria-expanded'),
      'false',
    );
    const before = f.fetch.calls.length;
    await f.refresh();
    assert.deepEqual(
      f.fetch.calls
        .slice(before)
        .filter(({ path }) => path === listPath)
        .map(({ uri }) => uri),
      [root],
    );
    assert.equal(
      row(f.view, canonical)?.getAttribute('aria-expanded'),
      'false',
    );
  } finally {
    await f.view.unmount();
  }
});
