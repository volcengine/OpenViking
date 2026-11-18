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
const child = `${alias}/child`;
const other = `${root}/other`;
const file = (uri) => `${uri}/result.md`;
const directory = (uri) => ({ uri, isDir: true });
const homeRequest = ({ path, uri }) =>
  path === '/api/v1/fs/stat' && uri === homeAlias;

const row = (view, uri) =>
  view.container.querySelector(`[data-file-tree-row-id="${uri}"]`);
const expanded = (view, uri) => row(view, uri)?.getAttribute('aria-expanded');
async function toggle(view, uri) {
  await act(() => row(view, uri).querySelector('[data-role="toggle"]').click());
  await settle();
}

function browser(fetch, defaults, onExpansionSnapshotChange) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return (uri) =>
    h(
      OpenVikingProvider,
      { fetch, baseURL: 'https://ov.test', queryClient },
      h(FileBrowser, {
        rootUri: root,
        uri,
        dashboardKinds: [],
        ...defaults,
        onExpansionSnapshotChange,
      }),
    );
}

test('a saved collapse survives unresolved aliases and later controlled navigation', async () => {
  let homeAvailable = false;
  const fetch = recordingFetch((call) => {
    const { path, uri } = call;
    if (homeRequest(call))
      return homeAvailable
        ? ok({ uri: home, isDir: true })
        : new Response('Home unavailable', { status: 503 });
    if (path === '/api/v1/fs/stat')
      return ok({ uri, isDir: [root, alias, child, other].includes(uri) });
    if (path !== '/api/v1/fs/ls') return ok('');
    if (uri === root) return ok([directory(alias), directory(other)]);
    if (uri === alias) return ok([directory(child)]);
    return ok([{ uri: file(uri) }]);
  });
  let saved = {
    expandedUris: [canonical, `${canonical}/child`],
    collapsedUris: [],
  };
  const persist = (snapshot) => {
    saved = JSON.parse(JSON.stringify(snapshot));
  };
  const element = browser(
    fetch,
    { defaultExpandedUris: saved.expandedUris },
    persist,
  );
  const view = await mount(element());
  try {
    assert.ok(fetch.calls.some(homeRequest));
    assert.equal(expanded(view, alias), 'false');
    await toggle(view, alias);
    assert.equal(expanded(view, alias), 'true');
    assert.ok(row(view, child));
    await toggle(view, alias);
    assert.equal(expanded(view, alias), 'false');

    await view.render(element(file(other)));
    assert.equal(expanded(view, other), 'true');
    assert.equal(row(view, file(other))?.getAttribute('aria-selected'), 'true');
    assert.ok(saved.expandedUris.includes(other));
    assert.ok(saved.collapsedUris.includes(alias));
  } finally {
    await view.unmount();
  }

  const previousRequests = fetch.calls.length;
  homeAvailable = true;
  const reloaded = await mount(
    browser(fetch, { defaultExpansionSnapshot: saved }, persist)(),
  );
  try {
    assert.ok(fetch.calls.slice(previousRequests).some(homeRequest));
    assert.equal(expanded(reloaded, alias), 'false');
    assert.equal(row(reloaded, child), null);
    assert.equal(expanded(reloaded, other), 'true');
    assert.ok(row(reloaded, file(other)));
  } finally {
    await reloaded.unmount();
  }
});
