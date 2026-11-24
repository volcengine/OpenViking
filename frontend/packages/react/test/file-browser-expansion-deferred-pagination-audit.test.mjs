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
const root = `${homeAlias}/resources`;
const parent = `${root}/folder`;
const child = `${parent}/child`;
const sibling = `${root}/sibling`;
const siblingFile = `${sibling}/result.md`;
const firstFile = `${parent}/first.md`;
const resultFile = `${child}/result.md`;
const folded = 'viking://user/default/resources/folder';
const listPath = '/api/v1/fs/ls';
const directory = (uri) => ({ uri, isDir: true });
const row = (view, uri) =>
  view.container.querySelector(`[data-file-tree-row-id="${uri}"]`);
const expanded = (view, uri) => row(view, uri)?.getAttribute('aria-expanded');

for (const [foldWhilePending, holdSibling] of [
  [false, false],
  [true, false],
  [false, true],
  [true, true],
]) {
  test(`identity recovery restores page 2 (fold ${foldWhilePending}, pending sibling ${holdSibling})`, async () => {
    const lookup = deferred();
    const siblingPage = deferred();
    const siblingCalls = () =>
      fetch.calls.filter(
        ({ path, uri }) => path === listPath && uri === sibling,
      );
    const fetch = recordingFetch(({ path, uri, query }) => {
      if (path === '/api/v1/fs/stat' && uri === homeAlias)
        return lookup.promise;
      if (path !== listPath) return ok('');
      if (uri === root)
        return ok([
          directory(parent),
          ...(holdSibling ? [directory(sibling)] : []),
        ]);
      if (uri === sibling) return siblingPage.promise;
      if (uri === parent)
        return Number(query.offset) > 0
          ? ok([directory(child)])
          : ok([{ uri: firstFile }], true);
      return ok([{ uri: resultFile }]);
    });
    const snapshots = [];
    const view = await mount(
      h(
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
          defaultExpansionSnapshot: {
            expandedUris: [parent, child, ...(holdSibling ? [sibling] : [])],
            collapsedUris: [folded],
          },
          onExpansionSnapshotChange: (snapshot) =>
            snapshots.push(JSON.parse(JSON.stringify(snapshot))),
        }),
      ),
    );
    const toggle = async () => {
      await act(() =>
        row(view, parent).querySelector('[data-role="toggle"]').click(),
      );
      await settle();
    };
    try {
      assert.ok(
        fetch.calls.some(
          ({ path, uri }) => path === '/api/v1/fs/stat' && uri === homeAlias,
        ),
      );
      assert.equal(expanded(view, parent), 'false');
      assert.ok(
        !fetch.calls.some(
          ({ path, uri }) => path === listPath && uri === parent,
        ),
      );
      if (foldWhilePending) {
        await toggle();
        assert.equal(expanded(view, parent), 'true');
        assert.ok(row(view, firstFile));
        await toggle();
        assert.equal(expanded(view, parent), 'false');
      }

      await act(async () =>
        lookup.resolve(await ok({ uri: 'viking://user/other', isDir: true })),
      );
      await settle();
      if (holdSibling) {
        assert.equal(siblingCalls().length, 1);
        assert.equal(siblingCalls()[0].init.signal.aborted, false);
        await act(async () =>
          siblingPage.resolve(await ok([{ uri: siblingFile }])),
        );
        await settle();
        assert.equal(siblingCalls().length, 1);
        assert.ok(row(view, siblingFile));
      }
      const latest = snapshots.at(-1);
      if (foldWhilePending) {
        assert.equal(expanded(view, parent), 'false');
        assert.equal(row(view, child), null);
        assert.ok(latest.collapsedUris.includes(parent));
        assert.ok(!latest.expandedUris.includes(parent));
        assert.ok(
          !fetch.calls.some(
            ({ path, uri }) => path === listPath && uri === child,
          ),
        );
      } else {
        assert.equal(expanded(view, parent), 'true');
        assert.equal(expanded(view, child), 'true');
        assert.ok(row(view, resultFile));
        assert.ok(
          fetch.calls.some(
            ({ path, uri, query }) =>
              path === listPath && uri === parent && Number(query.offset) > 0,
          ),
        );
        assert.ok(
          fetch.calls.some(
            ({ path, uri }) => path === listPath && uri === child,
          ),
        );
        assert.deepEqual(
          new Set(latest.expandedUris),
          new Set([parent, child, ...(holdSibling ? [sibling] : [])]),
        );
      }
    } finally {
      await view.unmount();
    }
  });
}
