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

function browser(fetch, props, queryClient) {
  return h(
    OpenVikingProvider,
    { fetch, baseURL: 'https://ov.test', queryClient },
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

test('preserves a saved nested expansion when a later listing page fails', async () => {
  const initial = [BETA, NESTED];
  const snapshots = [];
  let saved = initial;
  const open = (fetch) =>
    mount(
      browser(
        fetch,
        {
          defaultExpandedUris: saved,
          onExpandedUrisChange: (next) => {
            saved = [...next];
            snapshots.push(saved);
          },
        },
        new QueryClient({ defaultOptions: { queries: { retry: false } } }),
      ),
    );
  const isLaterBetaPage = ({ path, uri, query }) =>
    path === listPath && uri === BETA && Number(query.offset) > 0;
  let unavailable = true;
  const failed = recordingFetch((call) =>
    unavailable && isLaterBetaPage(call)
      ? Promise.resolve(new Response('Listing failed', { status: 500 }))
      : listing(call),
  );
  const view = await open(failed);
  try {
    assert.ok(failed.calls.some(isLaterBetaPage));
    assert.equal(row(view, NESTED), null);
    assert.deepEqual(saved, initial);
    assert.deepEqual(snapshots, []);

    unavailable = false;
    await act(() =>
      row(view, `${BETA}::error`).querySelector('button').click(),
    );
    await settle();
    assert.equal(expanded(view, NESTED), 'true');
    assert.deepEqual(snapshots.at(-1), initial);
  } finally {
    await view.unmount();
  }

  const recovered = recordingFetch(listing);
  const reloaded = await open(recovered);
  try {
    assert.equal(expanded(reloaded, NESTED), 'true');
    assert.ok(row(reloaded, NESTED_FILE));
    assert.deepEqual(snapshots.at(-1), initial);
  } finally {
    await reloaded.unmount();
  }
});
