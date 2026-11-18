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
    await toggle(view, nested);
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
