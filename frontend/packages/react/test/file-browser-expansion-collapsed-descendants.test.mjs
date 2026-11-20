import assert from 'node:assert/strict';
import test from 'node:test';
import { act, createElement as h } from 'react';
import { QueryClient } from '@tanstack/react-query';
import { OpenVikingProvider } from '@openviking/react/client';
import { FileBrowser } from '@openviking/react/modules/file-browser';
import { mount, ok, recordingFetch, settle } from './helpers/module-dom.mjs';

const home = 'viking://user/default';
const homeAlias = 'viking://~';
const root = `${homeAlias}/resources`;
const alias = `${root}/folder`;
const canonical = `${home}/resources/folder`;
const listPath = '/api/v1/fs/ls';
const child = (uri) => `${uri}/child`;
const file = (uri) => `${uri}/result.md`;
const directory = (uri) => ({ uri, isDir: true });
const row = (view, uri) =>
  view.container.querySelector(`[data-file-tree-row-id="${uri}"]`);
async function toggle(view, uri) {
  await act(() => row(view, uri).querySelector('[data-role="toggle"]').click());
  await settle();
}

function element(
  fetch,
  defaultExpansionSnapshot,
  onExpansionSnapshotChange,
  client,
) {
  return h(
    OpenVikingProvider,
    { fetch, baseURL: 'https://ov.test', queryClient: client },
    h(FileBrowser, {
      rootUri: root,
      dashboardKinds: [],
      defaultExpansionSnapshot,
      onExpansionSnapshotChange,
    }),
  );
}
const queryClient = () =>
  new QueryClient({ defaultOptions: { queries: { retry: false } } });

test('folding a parent retires its alias and descendant observers when the listed spelling changes', async () => {
  let spelling = alias;
  const fetch = recordingFetch(({ path, uri }) => {
    if (path === '/api/v1/fs/stat' && uri === homeAlias)
      return ok({ uri: home, isDir: true });
    if (path !== listPath) return ok('');
    if (uri === root) return ok([directory(spelling)]);
    if (uri === alias || uri === canonical) return ok([directory(child(uri))]);
    return ok([{ uri: file(uri) }]);
  });
  let saved = { expandedUris: [], collapsedUris: [] };
  const persist = (snapshot) => {
    saved = JSON.parse(JSON.stringify(snapshot));
  };
  const client = queryClient();
  const view = await mount(element(fetch, saved, persist, client));
  try {
    await toggle(view, alias);
    await toggle(view, child(alias));
    assert.ok(row(view, file(child(alias))));
    await toggle(view, alias);
    assert.equal(row(view, alias)?.getAttribute('aria-expanded'), 'false');
    assert.equal(row(view, child(alias)), null);

    spelling = canonical;
    await act(async () => client.invalidateQueries());
    await settle();
    assert.equal(row(view, canonical)?.getAttribute('aria-expanded'), 'false');
    for (let count = 0; count < 2; count += 1) {
      const before = fetch.calls.length;
      await act(async () => client.invalidateQueries());
      await settle();
      assert.deepEqual(
        fetch.calls
          .slice(before)
          .filter(({ path }) => path === listPath)
          .map(({ uri }) => uri),
        [root],
      );
    }
  } finally {
    await view.unmount();
  }

  const before = fetch.calls.length;
  const reloaded = await mount(element(fetch, saved, persist, queryClient()));
  try {
    assert.equal(
      row(reloaded, canonical)?.getAttribute('aria-expanded'),
      'false',
    );
    assert.ok(
      !fetch.calls
        .slice(before)
        .some(({ path, uri }) => path === listPath && uri !== root),
    );
    await toggle(reloaded, canonical);
    assert.equal(
      row(reloaded, canonical)?.getAttribute('aria-expanded'),
      'true',
    );
    if (
      row(reloaded, child(canonical))?.getAttribute('aria-expanded') !== 'true'
    )
      await toggle(reloaded, child(canonical));
    assert.ok(row(reloaded, file(child(canonical))));
    assert.ok(
      fetch.calls
        .slice(before)
        .some(({ path, uri }) => path === listPath && uri === child(canonical)),
    );
  } finally {
    await reloaded.unmount();
  }
});
