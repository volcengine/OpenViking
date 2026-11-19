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
const sibling = `${root}/a-sibling`;
const parent = `${root}/folder`;
const canonicalParent = `${home}/resources/folder`;
const child = `${canonicalParent}/child`;
const listedChild = `${parent}/child`;
const current = `${sibling}/current.md`;
const target = current.replace(homeAlias, home);
const first = `${parent}/first.md`;
const result = `${listedChild}/result.md`;
const listPath = '/api/v1/fs/ls';
const directory = (uri) => ({ uri, isDir: true });
const row = (view, uri) =>
  view.container.querySelector(`[data-file-tree-row-id="${uri}"]`);

for (const foldWhilePending of [false, true]) {
  test(`reindexes mixed saved paths during sibling recovery (fold ${foldWhilePending})`, async () => {
    const siblingPage = deferred();
    let homeLookups = 0;
    const fetch = recordingFetch(({ path, uri, query }) => {
      if (path === '/api/v1/fs/stat' && uri === homeAlias) {
        homeLookups += 1;
        return homeLookups === 1
          ? new Response('Home unavailable', { status: 503 })
          : ok({ uri: home, isDir: true });
      }
      if (path === '/api/v1/fs/stat')
        return ok({ uri, isDir: !uri.endsWith('.md') });
      if (path !== listPath) return ok('Preview');
      if (uri === root) return ok([directory(sibling), directory(parent)]);
      if (uri === sibling) return siblingPage.promise;
      if (uri === parent)
        return Number(query.offset) > 0
          ? ok([directory(listedChild)])
          : ok([{ uri: first }], true);
      return ok([{ uri: result }]);
    });
    const snapshots = [];
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const element = (uri) =>
      h(
        OpenVikingProvider,
        { fetch, baseURL: 'https://ov.test', queryClient },
        h(FileBrowser, {
          rootUri: root,
          uri,
          dashboardKinds: [],
          defaultExpansionSnapshot: {
            expandedUris: [sibling, parent, child],
            collapsedUris: [],
          },
          onExpansionSnapshotChange: (snapshot) =>
            snapshots.push(JSON.parse(JSON.stringify(snapshot))),
        }),
      );
    const view = await mount(element());
    try {
      assert.equal(homeLookups, 1);
      assert.ok(
        fetch.calls.some(
          ({ path, uri }) => path === listPath && uri === sibling,
        ),
      );
      assert.ok(
        !fetch.calls.some(
          ({ path, uri }) => path === listPath && uri === parent,
        ),
      );

      // A later controlled selection resolves home while restoration is still
      // waiting for the first sibling. It must not replace the saved expansion.
      await view.render(element(target));
      assert.equal(homeLookups, 2);
      assert.ok(
        !fetch.calls.some(
          ({ path, uri }) => path === listPath && uri === parent,
        ),
      );
      const siblingCall = fetch.calls.find(
        ({ path, uri }) => path === listPath && uri === sibling,
      );
      assert.equal(siblingCall.init.signal.aborted, false);

      if (foldWhilePending) {
        await act(() =>
          row(view, sibling).querySelector('[data-role="toggle"]').click(),
        );
        await settle();
        assert.equal(
          row(view, sibling)?.getAttribute('aria-expanded'),
          'false',
        );
      }

      await act(async () => siblingPage.resolve(await ok([{ uri: current }])));
      await settle();
      if (foldWhilePending)
        assert.equal(
          row(view, sibling)?.getAttribute('aria-expanded'),
          'false',
        );
      assert.ok(row(view, first));
      assert.ok(
        fetch.calls.some(
          ({ path, uri, query }) =>
            path === listPath && uri === parent && Number(query.offset) > 0,
        ),
        'the saved child must restore from page two without Load more or Refresh',
      );
      assert.equal(
        row(view, listedChild)?.getAttribute('aria-expanded'),
        'true',
      );
      assert.ok(row(view, result));
      assert.deepEqual(
        new Set(snapshots.at(-1).expandedUris),
        new Set([...(foldWhilePending ? [] : [sibling]), parent, listedChild]),
      );
      for (const snapshot of snapshots)
        assert.ok(
          snapshot.expandedUris.some(
            (uri) => uri === child || uri === listedChild,
          ),
          'each published snapshot must retain the saved child during identity recovery',
        );
    } finally {
      siblingPage.resolve(await ok([{ uri: current }]));
      await view.unmount();
    }
  });
}
