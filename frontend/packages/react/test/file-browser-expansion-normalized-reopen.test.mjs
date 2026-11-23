import assert from 'node:assert/strict';
import test from 'node:test';
import { act, createElement as h } from 'react';
import { QueryClient } from '@tanstack/react-query';
import { OpenVikingProvider } from '@openviking/react/client';
import { FileBrowser } from '@openviking/react/modules/file-browser';
import { mount, ok, recordingFetch, settle } from './helpers/module-dom.mjs';

const homeAlias = 'viking://~';
const home = 'viking://user/default';
const root = `${homeAlias}/resources`;
const alias = `${root}/folder`;
const canonical = `${home}/resources/folder`;
const file = `${alias}/result.md`;
const listPath = '/api/v1/fs/ls';
const row = (view, uri) =>
  view.container.querySelector(`[data-file-tree-row-id="${uri}"]`);
const expanded = (view, uri) => row(view, uri)?.getAttribute('aria-expanded');
const copy = (snapshot) => JSON.parse(JSON.stringify(snapshot));

async function toggle(view, uri) {
  await act(() => row(view, uri).querySelector('[data-role="toggle"]').click());
  await settle();
}

async function refresh(view) {
  await act(() =>
    view.container.querySelector('[aria-label="Refresh"]').click(),
  );
  await settle();
}

test('an unresolved reopen survives normalized listings and serialized fold edits', async () => {
  let listed = `${alias}/`;
  let available = false;
  const fetch = recordingFetch(({ path, uri }) => {
    if (path === '/api/v1/fs/stat' && uri === homeAlias)
      return available
        ? ok({ uri: home, isDir: true })
        : new Response('Home unavailable', { status: 503 });
    if (path !== listPath) return ok('');
    return ok(uri === root ? [{ uri: listed, isDir: true }] : [{ uri: file }]);
  });
  let saved = { expandedUris: [], collapsedUris: [canonical] };
  const open = () =>
    mount(
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
          defaultExpansionSnapshot: copy(saved),
          onExpansionSnapshotChange: (snapshot) => {
            saved = copy(snapshot);
          },
        }),
      ),
    );
  const view = await open();
  try {
    assert.equal(expanded(view, listed), 'false');
    await toggle(view, listed);
    assert.equal(expanded(view, listed), 'true');
    assert.ok(row(view, file));
    assert.ok(saved.pendingEdits.some(({ expanded }) => expanded));

    listed = alias;
    await refresh(view);
    assert.equal(expanded(view, listed), 'true');
    assert.ok(row(view, file));
    assert.deepEqual(saved.collapsedUris, [canonical]);
  } finally {
    await view.unmount();
  }

  const reopened = await open();
  try {
    assert.equal(expanded(reopened, alias), 'true');
    assert.ok(row(reopened, file));
    await toggle(reopened, alias);
    assert.equal(expanded(reopened, alias), 'false');
    assert.equal(row(reopened, file), null);
  } finally {
    await reopened.unmount();
  }

  const folded = await open();
  try {
    assert.equal(expanded(folded, alias), 'false');
    assert.equal(row(folded, file), null);
    await toggle(folded, alias);
    assert.equal(expanded(folded, alias), 'true');
    available = true;
    const before = fetch.calls.length;
    await refresh(folded);
    assert.ok(fetch.calls.slice(before).some(({ uri }) => uri === homeAlias));
    assert.equal(expanded(folded, alias), 'true');
    assert.ok(row(folded, file));
    assert.deepEqual(saved.expandedUris, [alias]);
    assert.deepEqual(saved.collapsedUris, []);
    assert.deepEqual(saved.pendingEdits ?? [], []);
  } finally {
    await folded.unmount();
  }
});
