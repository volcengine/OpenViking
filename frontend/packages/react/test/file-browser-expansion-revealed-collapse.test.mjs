import assert from 'node:assert/strict';
import test from 'node:test';
import { act, createElement as h } from 'react';
import { QueryClient } from '@tanstack/react-query';
import { OpenVikingProvider } from '@openviking/react/client';
import { FileBrowser } from '@openviking/react/modules/file-browser';
import { mount, ok, recordingFetch, settle } from './helpers/module-dom.mjs';

const root = 'viking://resources';
const retained = `${root}/retained`;
const retainedChild = `${retained}/child`;
const revealed = `${root}/revealed`;
const revealedChild = `${revealed}/child`;
const initial = [retained, retainedChild, revealed, revealedChild];
const file = (uri) => `${uri}/report.md`;
const directory = (uri) => ({ uri, isDir: true });
const listPath = '/api/v1/fs/ls';

function listing({ path, uri, query }) {
  if (path === '/api/v1/fs/stat')
    return ok({ isDir: uri === root || initial.includes(uri) });
  if (path !== listPath) return ok('');
  const later = Number(query.offset) > 0;
  if (uri === root)
    return later ? ok([directory(revealed)]) : ok([directory(retained)], true);
  if (uri === retained)
    return later
      ? ok([directory(retainedChild)])
      : ok([{ uri: file(retained) }], true);
  if (uri === revealed) return ok([directory(revealedChild)]);
  return ok([{ uri: file(uri) }]);
}

function browser(fetch, defaultExpandedUris, onExpandedUrisChange) {
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
        defaultExpandedUris,
        onExpandedUrisChange,
      }),
    );
}

const row = (view, uri) =>
  view.container.querySelector(`[data-file-tree-row-id="${uri}"]`);
const expanded = (view, uri) => row(view, uri)?.getAttribute('aria-expanded');
const laterPage = ({ path, uri, query }, folder) =>
  path === listPath && uri === folder && Number(query.offset) > 0;

test('persists a collapse after navigation reveals a saved branch on a recovered page', async () => {
  let rootPageUnavailable = true;
  const fetch = recordingFetch((call) =>
    (rootPageUnavailable && laterPage(call, root)) || laterPage(call, retained)
      ? Promise.resolve(new Response('Listing failed', { status: 500 }))
      : listing(call),
  );
  let saved = initial;
  const persist = (next) => {
    saved = [...next];
  };
  const element = browser(fetch, saved, persist);
  const view = await mount(element());
  try {
    assert.ok(row(view, `${root}::error`));
    assert.ok(row(view, `${retained}::error`));
    assert.equal(row(view, revealed), null);
    assert.deepEqual(saved, initial);

    rootPageUnavailable = false;
    const target = file(revealedChild);
    await view.render(element(target));
    assert.equal(expanded(view, revealed), 'true');
    assert.equal(expanded(view, revealedChild), 'true');
    assert.equal(row(view, target)?.getAttribute('aria-selected'), 'true');

    await act(() =>
      row(view, revealed).querySelector('[data-role="toggle"]').click(),
    );
    await settle();
    assert.equal(expanded(view, revealed), 'false');
    assert.deepEqual(saved, [retained, retainedChild]);
  } finally {
    await view.unmount();
  }

  const recovered = recordingFetch(listing);
  const reloaded = await mount(browser(recovered, saved, persist)());
  try {
    assert.ok(row(reloaded, file(retainedChild)));
    await act(() =>
      row(reloaded, `${root}::more`).querySelector('button').click(),
    );
    await settle();
    assert.equal(expanded(reloaded, revealed), 'false');
    assert.equal(row(reloaded, revealedChild), null);
    assert.ok(
      !recovered.calls.some(
        ({ path, uri }) => path === listPath && uri === revealed,
      ),
    );
    assert.deepEqual(saved, [retained, retainedChild]);
  } finally {
    await reloaded.unmount();
  }
});
