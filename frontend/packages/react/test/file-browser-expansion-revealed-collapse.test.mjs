import assert from 'node:assert/strict';
import test from 'node:test';
import { act, createElement as h } from 'react';
import { QueryClient } from '@tanstack/react-query';
import { OpenVikingProvider } from '@openviking/react/client';
import { FileBrowser } from '@openviking/react/modules/file-browser';
import { mount, ok, recordingFetch, settle } from './helpers/module-dom.mjs';

const home = 'viking://user/default';
const homeAlias = 'viking://~';
const listPath = '/api/v1/fs/ls';
const file = (uri) => `${uri}/report.md`;
const directory = (uri) => ({ uri, isDir: true });
const laterPage = ({ path, uri, query }, folder) =>
  path === listPath && uri === folder && Number(query.offset) > 0;

function queryClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } });
}

function browser(fetch, rootUri, defaultExpandedUris, persist, client) {
  return (uri) =>
    h(
      OpenVikingProvider,
      { fetch, baseURL: 'https://ov.test', queryClient: client },
      h(FileBrowser, {
        rootUri,
        uri,
        dashboardKinds: [],
        defaultExpandedUris,
        onExpandedUrisChange: persist,
      }),
    );
}

const row = (view, uri) =>
  view.container.querySelector(`[data-file-tree-row-id="${uri}"]`);
const expanded = (view, uri) => row(view, uri)?.getAttribute('aria-expanded');

for (const { title, root = 'viking://resources', alias, removeOnRefresh } of [
  {
    title:
      'persists a collapse after navigation reveals a saved branch on a recovered page',
  },
  {
    title:
      'drops a saved canonical branch when its recovered alias is collapsed',
    root: `${home}/resources`,
    alias: true,
  },
  {
    title:
      'keeps the persisted collapse when an invalidated root omits the row',
    removeOnRefresh: true,
  },
]) {
  test(title, async () => {
    const retained = `${root}/retained`;
    const retainedChild = `${retained}/child`;
    const revealed = `${root}/revealed`;
    const revealedChild = `${revealed}/child`;
    const listed = alias ? revealed.replace(home, homeAlias) : revealed;
    const listedChild = `${listed}/child`;
    const initial = [retained, retainedChild, revealed, revealedChild];
    const expected = [retained, retainedChild];

    function listing({ path, uri, query }, omitRevealed = false) {
      if (path === '/api/v1/fs/stat') {
        if (uri === homeAlias) return ok({ uri: home, isDir: true });
        return ok({
          uri,
          isDir: [root, ...initial, listed, listedChild].includes(uri),
        });
      }
      if (path !== listPath) return ok('');
      const later = Number(query.offset) > 0;
      if (uri === root)
        return omitRevealed
          ? ok([directory(retained)])
          : later
            ? ok([directory(listed)])
            : ok([directory(retained)], true);
      if (uri === retained)
        return later
          ? ok([directory(retainedChild)])
          : ok([{ uri: file(retained) }], true);
      if (uri === listed) return ok([directory(listedChild)]);
      return ok([{ uri: file(uri) }]);
    }

    let rootPageUnavailable = true;
    let omitRevealed = false;
    const fetch = recordingFetch((call) =>
      (rootPageUnavailable && laterPage(call, root)) ||
      laterPage(call, retained)
        ? Promise.resolve(new Response('Listing failed', { status: 500 }))
        : listing(call, omitRevealed),
    );
    let saved = initial;
    const snapshots = [];
    const persist = (next) => {
      saved = [...next];
      snapshots.push(saved);
    };
    const client = queryClient();
    const element = browser(fetch, root, saved, persist, client);
    const view = await mount(element());
    try {
      assert.ok(row(view, `${root}::error`));
      assert.ok(row(view, `${retained}::error`));
      assert.equal(row(view, listed), null);
      assert.deepEqual(saved, initial);

      rootPageUnavailable = false;
      await view.render(element(file(revealedChild)));
      assert.equal(expanded(view, listed), 'true');
      assert.equal(expanded(view, listedChild), 'true');
      assert.equal(
        row(view, file(listedChild))?.getAttribute('aria-selected'),
        'true',
      );

      await act(() =>
        row(view, listed).querySelector('[data-role="toggle"]').click(),
      );
      await settle();
      assert.equal(expanded(view, listed), 'false');
      assert.deepEqual(saved, expected);

      if (removeOnRefresh) {
        const previouslyPublished = snapshots.length;
        omitRevealed = true;
        await act(async () => client.invalidateQueries());
        await settle();
        assert.equal(row(view, listed), null);
        assert.deepEqual(saved, expected);
        for (const snapshot of snapshots.slice(previouslyPublished))
          assert.deepEqual(snapshot, expected);
      }
    } finally {
      await view.unmount();
    }

    const recovered = recordingFetch(listing);
    const reloaded = await mount(
      browser(recovered, root, saved, persist, queryClient())(),
    );
    try {
      assert.ok(row(reloaded, file(retainedChild)));
      await act(() =>
        row(reloaded, `${root}::more`).querySelector('button').click(),
      );
      await settle();
      assert.equal(expanded(reloaded, listed), 'false');
      assert.equal(row(reloaded, listedChild), null);
      assert.ok(
        !recovered.calls.some(
          ({ path, uri }) =>
            path === listPath && (uri === listed || uri === revealed),
        ),
      );
      assert.deepEqual(saved, expected);
    } finally {
      await reloaded.unmount();
    }
  });
}

test('keeps an expanded alias visible when refresh returns its canonical URI', async () => {
  const root = `${home}/resources`;
  const alias = `${homeAlias}/resources/folder`;
  const folder = `${root}/folder`;
  const snapshots = [];
  const client = queryClient();
  let listed = alias;
  const fetch = recordingFetch(({ path, uri, query }) => {
    if (path === '/api/v1/fs/stat' && uri === homeAlias)
      return ok({ uri: home, isDir: true });
    if (path !== listPath) return ok('');
    if (uri === root) return ok([directory(listed)]);
    return Number(query.offset) > 0
      ? ok([{ uri: `${folder}/next.md` }])
      : ok([{ uri: file(folder) }], true);
  });
  const persist = (next) => snapshots.push([...next]);
  const view = await mount(browser(fetch, root, [folder], persist, client)());
  try {
    assert.equal(expanded(view, alias), 'true');
    assert.ok(row(view, file(folder)));
    assert.deepEqual(snapshots.at(-1), [alias]);

    listed = folder;
    await act(async () => client.invalidateQueries());
    await settle();
    assert.equal(expanded(view, folder), 'true');
    assert.ok(row(view, file(folder)));
    assert.deepEqual(snapshots.at(-1), [folder]);
    await act(() =>
      row(view, `${folder}::more`).querySelector('button').click(),
    );
    await settle();
    assert.ok(fetch.calls.some((call) => laterPage(call, folder)));
    assert.ok(row(view, `${folder}/next.md`));
  } finally {
    await view.unmount();
  }
});
