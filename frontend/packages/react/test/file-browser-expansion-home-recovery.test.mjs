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

const home = 'viking://user/default';
const homeAlias = 'viking://~';
const root = `${homeAlias}/resources`;
const alias = `${root}/folder`;
const canonical = `${home}/resources/folder`;
const child = `${alias}/child`;
const file = (uri) => `${uri}/result.md`;
const directory = (uri) => ({ uri, isDir: true });
const listPath = '/api/v1/fs/ls';
const homeRequest = ({ path, uri }) =>
  path === '/api/v1/fs/stat' && uri === homeAlias;
const row = (view, uri) =>
  view.container.querySelector(`[data-file-tree-row-id="${uri}"]`);
const expanded = (view, uri) => row(view, uri)?.getAttribute('aria-expanded');

function element(fetch, defaultExpansionSnapshot, onExpansionSnapshotChange) {
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

test('Refresh settles a single reopened alias after a home lookup failure and remount keeps it open', async () => {
  let available = false;
  const fetch = recordingFetch((call) => {
    if (homeRequest(call))
      return available
        ? ok({ uri: home, isDir: true })
        : new Response('Home unavailable', { status: 503 });
    if (call.path !== listPath) return ok('');
    return ok(call.uri === root ? [directory(alias)] : [{ uri: file(alias) }]);
  });
  let saved = { expandedUris: [], collapsedUris: [canonical] };
  const persist = (snapshot) => {
    saved = JSON.parse(JSON.stringify(snapshot));
  };
  const view = await mount(element(fetch, saved, persist));
  try {
    assert.ok(fetch.calls.some(homeRequest));
    assert.equal(expanded(view, alias), 'false');
    await act(() =>
      row(view, alias).querySelector('[data-role="toggle"]').click(),
    );
    await settle();
    assert.equal(expanded(view, alias), 'true');
    assert.ok(row(view, file(alias)));
    assert.ok(
      saved.pendingEdits?.some(
        ({ uri, expanded }) => uri === alias && expanded,
      ),
    );

    const before = fetch.calls.length;
    available = true;
    await act(() =>
      view.container.querySelector('[aria-label="Refresh"]').click(),
    );
    await settle();
    assert.ok(fetch.calls.slice(before).some(homeRequest));
    assert.equal(expanded(view, alias), 'true');
    assert.deepEqual(saved.collapsedUris, []);
    assert.deepEqual(saved.pendingEdits ?? [], []);
    assert.deepEqual(saved.expandedUris, [alias]);
  } finally {
    await view.unmount();
  }

  const reloaded = await mount(element(fetch, saved, persist));
  try {
    assert.equal(expanded(reloaded, alias), 'true');
    assert.ok(row(reloaded, file(alias)));
    assert.deepEqual(saved.collapsedUris, []);
  } finally {
    await reloaded.unmount();
  }
});

test('a delayed home lookup belonging to another user resumes the saved alias without refresh', async () => {
  const lookup = deferred();
  const fetch = recordingFetch((call) => {
    if (homeRequest(call)) return lookup.promise;
    if (call.path !== listPath) return ok('');
    if (call.uri === root) return ok([directory(alias)]);
    return ok(call.uri === alias ? [directory(child)] : [{ uri: file(child) }]);
  });
  let saved = { expandedUris: [alias, child], collapsedUris: [canonical] };
  const view = await mount(
    element(fetch, saved, (snapshot) => {
      saved = snapshot;
    }),
  );
  try {
    assert.ok(fetch.calls.some(homeRequest));
    assert.equal(expanded(view, alias), 'false');
    assert.ok(
      !fetch.calls.some(({ path, uri }) => path === listPath && uri === alias),
    );

    await act(async () =>
      lookup.resolve(await ok({ uri: 'viking://user/other', isDir: true })),
    );
    await settle();
    assert.equal(expanded(view, alias), 'true');
    assert.equal(expanded(view, child), 'true');
    assert.ok(row(view, file(child)));
    assert.ok(
      fetch.calls.some(({ path, uri }) => path === listPath && uri === child),
    );
    assert.deepEqual(saved.expandedUris, [alias, child]);
    assert.deepEqual(saved.collapsedUris, [canonical]);
    assert.deepEqual(saved.pendingEdits ?? [], []);
  } finally {
    await view.unmount();
  }
});
