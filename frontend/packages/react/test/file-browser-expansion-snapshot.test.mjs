import assert from 'node:assert/strict';
import test from 'node:test';
import { act, createElement as h } from 'react';
import { OpenVikingProvider } from '@openviking/react/client';
import { FileBrowser } from '@openviking/react/modules/file-browser';
import {
  deferred,
  mount,
  ok,
  recordingFetch,
  settle,
} from './helpers/module-dom.mjs';

const ROOT = 'viking://resources';
const ALPHA = `${ROOT}/alpha`;
const BETA = `${ROOT}/beta`;
const NESTED = `${BETA}/nested`;
const ARCHIVE = `${ROOT}/archive`;
const LATER = `${ROOT}/later`;
const CURRENT_FILE = `${ALPHA}/current.md`;
const LATER_FILE = `${LATER}/target.md`;
const NESTED_FILE = `${NESTED}/report.md`;
const ARCHIVED_FILE = `${ARCHIVE}/archive.md`;
const folders = new Set([ROOT, ALPHA, BETA, NESTED, ARCHIVE, LATER]);
const directory = (uri) => ({ uri, isDir: true });
const listPath = '/api/v1/fs/ls';

function listing({ path, uri, query }) {
  if (path === '/api/v1/fs/stat') return ok({ isDir: folders.has(uri) });
  if (path !== listPath) return ok('# Body');
  const offset = Number(query.offset);
  if (uri === ROOT)
    return offset
      ? ok([directory(ARCHIVE)])
      : ok([directory(ALPHA), directory(BETA), directory(LATER)], true);
  if (uri === BETA)
    return offset
      ? ok([directory(NESTED)])
      : ok([{ uri: `${BETA}/first.md` }], true);
  if (uri === NESTED) return ok([{ uri: NESTED_FILE }]);
  if (uri === ARCHIVE) return ok([{ uri: ARCHIVED_FILE }]);
  if (uri === ALPHA)
    return offset
      ? ok([{ uri: CURRENT_FILE }])
      : ok([{ uri: `${ALPHA}/first.md` }], true);
  if (uri === LATER) return ok([{ uri: LATER_FILE }]);
  return ok([]);
}

function browser(fetch, props) {
  return h(
    OpenVikingProvider,
    { fetch, baseURL: 'https://ov.test' },
    h(FileBrowser, { rootUri: ROOT, dashboardKinds: [], ...props }),
  );
}

const row = (view, uri) =>
  view.container.querySelector(`[data-file-tree-row-id="${uri}"]`);
const expanded = (view, uri) => row(view, uri)?.getAttribute('aria-expanded');
async function toggle(view, uri) {
  await act(() => row(view, uri).querySelector('[data-role="toggle"]').click());
  await settle();
}

test('restores multiple saved folders across pages, then saves collapses without clearing the preview', async () => {
  const initial = [ALPHA, BETA, NESTED, ARCHIVE];
  const nested = deferred();
  const fetch = recordingFetch((call) =>
    call.path === listPath && call.uri === NESTED
      ? nested.promise
      : listing(call),
  );
  const snapshots = [];
  const onExpandedUrisChange = (uris) => snapshots.push([...uris]);
  const view = await mount(
    browser(fetch, {
      uri: CURRENT_FILE,
      defaultExpandedUris: initial,
      onExpandedUrisChange,
    }),
  );
  let saved;
  try {
    assert.ok(
      fetch.calls.some(({ path, uri }) => path === listPath && uri === NESTED),
      'requested the restored nested folder after scanning its parent pages',
    );
    assert.deepEqual(snapshots, []);
    await act(async () => nested.resolve(await ok([{ uri: NESTED_FILE }])));
    await settle();
    assert.deepEqual(new Set(snapshots.at(-1)), new Set(initial));
    assert.equal(
      row(view, CURRENT_FILE)?.getAttribute('aria-selected'),
      'true',
    );
    assert.ok(row(view, NESTED_FILE));
    assert.ok(row(view, ARCHIVED_FILE));
    assert.equal(expanded(view, LATER), 'false');

    await toggle(view, BETA);
    assert.equal(row(view, NESTED), null);
    assert.deepEqual(new Set(snapshots.at(-1)), new Set([ALPHA, ARCHIVE]));
    await toggle(view, ALPHA);
    saved = snapshots.at(-1);
    assert.deepEqual(saved, [ARCHIVE]);
  } finally {
    await view.unmount();
  }

  const reloadedFetch = recordingFetch(listing);
  const element = (uri) =>
    browser(reloadedFetch, {
      uri,
      defaultExpandedUris: saved,
      onExpandedUrisChange,
    });
  const reloaded = await mount(element(CURRENT_FILE));
  try {
    assert.equal(expanded(reloaded, ALPHA), 'false');
    assert.equal(expanded(reloaded, BETA), 'false');
    assert.ok(row(reloaded, ARCHIVED_FILE));
    assert.ok(
      reloaded.container.querySelector(
        `[role="tab"][data-key="${CURRENT_FILE}"][aria-selected="true"]`,
      ),
    );

    await reloaded.render(element(LATER_FILE));
    assert.equal(expanded(reloaded, ALPHA), 'false');
    assert.equal(expanded(reloaded, LATER), 'true');
    assert.equal(
      row(reloaded, LATER_FILE)?.getAttribute('aria-selected'),
      'true',
    );
    assert.deepEqual(new Set(snapshots.at(-1)), new Set([ARCHIVE, LATER]));
  } finally {
    await reloaded.unmount();
  }
});

test('an explicit empty snapshot overrides the single default expansion path', async () => {
  const fetch = recordingFetch(listing);
  const view = await mount(
    browser(fetch, { defaultExpandedUri: NESTED, defaultExpandedUris: [] }),
  );
  try {
    assert.equal(expanded(view, BETA), 'false');
    assert.deepEqual(
      fetch.calls.filter(({ path }) => path === listPath).map(({ uri }) => uri),
      [ROOT],
    );
  } finally {
    await view.unmount();
  }
});
