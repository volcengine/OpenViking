import assert from 'node:assert/strict';
import test from 'node:test';
import { act, createElement as h } from 'react';
import { OpenVikingProvider } from '@openviking/react/client';
import { FileBrowser } from '@openviking/react/modules/file-browser';
import { mount, ok, recordingFetch, settle } from './helpers/module-dom.mjs';

const root = 'viking://~/resources';
const folder = 'viking://user/default/resources/folder';
const nested = `${folder}/nested`;
const file = `${nested}/result.md`;
const listPath = '/api/v1/fs/ls';

function fixture(folderUri = folder) {
  const listings = new Map([
    [root, [{ uri: folderUri, isDir: true }]],
    [folderUri, [{ uri: nested, isDir: true }]],
    [nested, [{ uri: file }]],
  ]);
  return recordingFetch(({ path, uri }) => {
    assert.equal(path, listPath);
    assert.ok(listings.has(uri), `unexpected directory request for ${uri}`);
    return ok(listings.get(uri));
  });
}

const browser = (fetch, props) =>
  h(
    OpenVikingProvider,
    { fetch, baseURL: 'https://ov.test' },
    h(FileBrowser, { rootUri: root, dashboardKinds: [], ...props }),
  );
const row = (view, uri) =>
  view.container.querySelector(`[data-file-tree-row-id="${uri}"]`);
const expanded = (view, uri) => row(view, uri)?.getAttribute('aria-expanded');

async function toggle(view, uri) {
  await act(() => row(view, uri).querySelector('[data-role="toggle"]').click());
  await settle();
}

test('restores a saved canonical branch under its aliased root', async () => {
  const snapshots = [];
  const onExpandedUrisChange = (uris) => snapshots.push([...uris]);
  const view = await mount(browser(fixture(), { onExpandedUrisChange }));
  try {
    await toggle(view, folder);
    // Opening the parent now expands this sole child automatically.
    assert.equal(expanded(view, nested), 'true');
    assert.deepEqual(snapshots.at(-1), [folder, nested]);
  } finally {
    await view.unmount();
  }

  const fetch = fixture();
  const restored = await mount(
    browser(fetch, {
      defaultExpandedUris: snapshots.at(-1),
      onExpandedUrisChange,
    }),
  );
  try {
    assert.equal(expanded(restored, folder), 'true');
    assert.equal(expanded(restored, nested), 'true');
    assert.ok(row(restored, file));
    assert.deepEqual(snapshots.at(-1), [folder, nested]);
    assert.deepEqual(
      fetch.calls.map(({ uri }) => uri),
      [root, folder, nested],
    );
  } finally {
    await restored.unmount();
  }
});

test('normalizes saved paths and uses the listed spelling to load folders', async () => {
  const rawFolder = `${folder}/`;
  const fetch = fixture(rawFolder);
  const snapshots = [];
  const view = await mount(
    browser(fetch, {
      defaultExpandedUris: [
        folder,
        `${nested}//`,
        'viking://user/default/resources-other/outside',
        'viking://user/other/resources/not-listed',
      ],
      onExpandedUrisChange: (uris) => snapshots.push([...uris]),
    }),
  );
  try {
    assert.ok(row(view, file));
    assert.deepEqual(snapshots.at(-1), [rawFolder, nested]);
    assert.deepEqual(
      fetch.calls.map(({ uri }) => uri),
      [root, rawFolder, nested],
    );
    await toggle(view, rawFolder);
    assert.equal(expanded(view, rawFolder), 'false');
    assert.equal(row(view, nested), null);
    assert.deepEqual(snapshots.at(-1), []);
  } finally {
    await view.unmount();
  }
});

test('does not republish a collapsed branch while its alias is unresolved', async () => {
  const aliasFolder = `${root}/folder`;
  const aliasNested = `${aliasFolder}/nested`;
  const other = 'viking://user/default/resources/other';
  const otherFile = `${other}/result.md`;
  let homeAvailable = false;
  const fetch = recordingFetch(({ path, uri }) => {
    if (path === '/api/v1/fs/stat' && uri === 'viking://~')
      return homeAvailable
        ? ok({ uri: 'viking://user/default', isDir: true })
        : new Response('Home unavailable', { status: 503 });
    if (path !== listPath) return ok('');
    if (uri === root)
      return ok([
        { uri: aliasFolder, isDir: true },
        { uri: other, isDir: true },
      ]);
    if (uri === aliasFolder) return ok([{ uri: aliasNested, isDir: true }]);
    return ok(uri === other ? [{ uri: otherFile }] : []);
  });
  const snapshots = [];
  const onExpandedUrisChange = (uris) => snapshots.push([...uris]);
  const open = (defaultExpandedUris) =>
    mount(browser(fetch, { defaultExpandedUris, onExpandedUrisChange }));
  const view = await open([folder, nested, other]);
  try {
    assert.ok(row(view, aliasFolder));
    assert.ok(row(view, otherFile));
    assert.ok(fetch.calls.some(({ uri }) => uri === 'viking://~'));
    assert.deepEqual(snapshots, []);

    await toggle(view, aliasFolder);
    assert.equal(expanded(view, aliasFolder), 'true');
    assert.ok(row(view, aliasNested));
    const beforeCollapse = snapshots.length;
    await toggle(view, aliasFolder);
    assert.equal(expanded(view, aliasFolder), 'false');

    homeAvailable = true;
    await act(() =>
      view.container.querySelector('[aria-label="Refresh"]').click(),
    );
    await settle();
    assert.ok(fetch.calls.filter(({ uri }) => uri === 'viking://~').length > 1);
    assert.equal(expanded(view, aliasFolder), 'false');
    assert.deepEqual(snapshots.at(-1), [other]);
    for (const snapshot of snapshots.slice(beforeCollapse))
      assert.deepEqual(snapshot, [other]);
  } finally {
    await view.unmount();
  }

  const reloaded = await open(snapshots.at(-1));
  try {
    assert.equal(expanded(reloaded, aliasFolder), 'false');
    assert.equal(row(reloaded, aliasNested), null);
    assert.ok(row(reloaded, otherFile));
    assert.deepEqual(snapshots.at(-1), [other]);
  } finally {
    await reloaded.unmount();
  }
});
