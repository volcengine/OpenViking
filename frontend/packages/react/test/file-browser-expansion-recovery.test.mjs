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

const root = 'viking://resources';
const first = `${root}/first`;
const firstChild = `${first}/child`;
const second = `${root}/second`;
const secondChild = `${second}/child`;
const initial = [first, firstChild, second, secondChild];
const listPath = '/api/v1/fs/ls';
const directory = (uri) => ({ uri, isDir: true });
const file = (uri) => `${uri}/report.md`;

function listing({ path, uri, query }) {
  if (path === '/api/v1/fs/stat')
    return ok({ isDir: uri === root || initial.includes(uri) });
  if (path !== listPath) return ok('');
  if (uri === root) return ok([directory(first), directory(second)]);
  if (uri === first || uri === second) {
    return Number(query.offset) > 0
      ? ok([directory(`${uri}/child`)])
      : ok([{ uri: `${uri}/first.md` }], true);
  }
  return ok([{ uri: file(uri) }]);
}

const laterPage = ({ path, uri, query }, folder) =>
  path === listPath && uri === folder && Number(query.offset) > 0;
const listingFailure = () =>
  Promise.resolve(new Response('Listing failed', { status: 500 }));

function open(fetch, defaultExpandedUris, onExpandedUrisChange) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return mount(
    h(
      OpenVikingProvider,
      { fetch, baseURL: 'https://ov.test', queryClient },
      h(FileBrowser, {
        rootUri: root,
        dashboardKinds: [],
        defaultExpandedUris,
        onExpandedUrisChange,
      }),
    ),
  );
}

const row = (view, uri) =>
  view.container.querySelector(`[data-file-tree-row-id="${uri}"]`);
const expanded = (view, uri) => row(view, uri)?.getAttribute('aria-expanded');

test('Retry resumes a saved branch while another branch is still restoring', async () => {
  const pending = deferred();
  const secondFirstPage = ({ path, uri, query }) =>
    path === listPath && uri === second && Number(query.offset) === 0;
  const pendingPage = await listing({
    path: listPath,
    uri: second,
    query: { offset: 0 },
  });
  let firstUnavailable = true;
  const fetch = recordingFetch((call) => {
    if (firstUnavailable && laterPage(call, first)) return listingFailure();
    if (secondFirstPage(call)) return pending.promise;
    return listing(call);
  });
  const snapshots = [];
  const view = await open(fetch, initial, (next) => snapshots.push([...next]));
  try {
    assert.ok(fetch.calls.some((call) => laterPage(call, first)));
    assert.equal(fetch.calls.filter(secondFirstPage).length, 1);
    assert.deepEqual(snapshots, []);

    firstUnavailable = false;
    await act(() =>
      row(view, `${first}::error`).querySelector('button').click(),
    );
    await settle();

    await act(() => pending.resolve(pendingPage));
    await settle();

    assert.equal(fetch.calls.filter(secondFirstPage).length, 1);
    assert.equal(expanded(view, firstChild), 'true');
    assert.ok(row(view, file(firstChild)));
    assert.equal(expanded(view, secondChild), 'true');
    assert.ok(row(view, file(secondChild)));
    assert.deepEqual(snapshots.at(-1), initial);
    assert.ok(
      snapshots.every((snapshot) =>
        initial.every((uri) => snapshot.includes(uri)),
      ),
    );
  } finally {
    await view.unmount();
  }
});

test('collapsing a failed saved branch persists the collapse and retains other branches', async () => {
  let saved = initial;
  const snapshots = [];
  const persist = (next) => {
    saved = [...next];
    snapshots.push(saved);
  };
  const failed = recordingFetch((call) =>
    laterPage(call, first) || laterPage(call, second)
      ? listingFailure()
      : listing(call),
  );
  const view = await open(failed, saved, persist);
  try {
    assert.ok(row(view, `${first}::error`));
    assert.ok(row(view, `${second}::error`));
    assert.deepEqual(snapshots, []);

    await act(() =>
      row(view, first).querySelector('[data-role="toggle"]').click(),
    );
    await settle();
    assert.equal(expanded(view, first), 'false');
    assert.deepEqual(saved, [second, secondChild]);
  } finally {
    await view.unmount();
  }

  const recovered = recordingFetch(listing);
  const reloaded = await open(recovered, saved, persist);
  try {
    assert.equal(expanded(reloaded, first), 'false');
    assert.equal(row(reloaded, firstChild), null);
    assert.ok(
      !recovered.calls.some(
        ({ path, uri }) => path === listPath && uri === first,
      ),
    );
    assert.equal(expanded(reloaded, secondChild), 'true');
    assert.ok(row(reloaded, file(secondChild)));
    assert.deepEqual(saved, [second, secondChild]);
  } finally {
    await reloaded.unmount();
  }
});
