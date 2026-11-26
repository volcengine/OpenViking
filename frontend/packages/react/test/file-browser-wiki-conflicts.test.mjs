import assert from 'node:assert/strict';
import test from 'node:test';
import { act, createElement as h } from 'react';
import { OpenVikingProvider } from './helpers/internal-hooks.mjs';
import { useFileBrowser } from './helpers/internal-hooks.mjs';
import { mount, ok, recordingFetch, settle } from './helpers/module-dom.mjs';

test('FileBrowser summarizes Wiki conflicts before expansion and refreshes without assuming failed queries are empty', async () => {
  const root = 'viking://resources';
  const wiki = `${root}/wiki`;
  const child = `${wiki}/analysis`;
  const file = `${child}/notes.md`;
  const empty = `${wiki}/other`;
  let response = 'pending';
  let browser;
  function Tree() {
    browser = useFileBrowser({ rootUri: root, currentUri: null });
    return null;
  }
  const fetch = recordingFetch(({ path, uri, body, init }) => {
    if (path.endsWith('/ls')) {
      if (uri === root)
        return ok([{ uri: wiki, isDir: true, tags: ['compile_target=true'] }]);
      if (uri === wiki)
        return ok([
          { uri: child, isDir: true, tags: [] },
          { uri: empty, isDir: true, tags: [] },
        ]);
      if (uri === child) return ok([{ uri: file, isDir: false }]);
      return ok([]);
    }
    if (path.endsWith('/search/find')) {
      assert.equal(init.method, 'POST');
      assert.deepEqual(body, {
        query: '',
        target_uri: body.target_uri,
        tags: ['has_conflict=true'],
        level: 2,
        limit: 100,
      });
      if (response === 'failed')
        return Promise.resolve(new Response('', { status: 503 }));
      return ok({
        resources: response === 'pending' ? [{ uri: file, level: 2 }] : [],
      });
    }
    return ok({});
  });
  const view = await mount(h(OpenVikingProvider, { fetch }, h(Tree)));
  const queries = () =>
    fetch.calls
      .filter(({ path }) => path.endsWith('/search/find'))
      .map(({ body }) => body.target_uri);
  const status = (uri) => browser.dashboards.get(uri)?.pendingConflicts;
  const expand = async (uri) => {
    const row = browser.rows.find((item) => item.uri === uri);
    assert.equal(row.expanded, false);
    await act(async () => browser.actions.toggleExpand(row));
    await settle();
  };
  try {
    assert.equal(status(wiki), true);
    assert.deepEqual(queries(), [wiki]);
    await expand(wiki);
    assert.equal(status(child), true);
    assert.equal(status(empty), false);
    assert.deepEqual(queries(), [wiki]);
    await expand(child);
    assert.equal(status(file), true);
    assert.deepEqual(queries(), [wiki, child]);

    response = 'failed';
    await act(async () => browser.refreshWikiConflicts());
    await settle();
    assert.equal(status(wiki), undefined);
    assert.equal(status(child), undefined);

    response = 'resolved';
    await act(async () => browser.refreshWikiConflicts());
    await settle();
    assert.equal(status(wiki), false);
    assert.equal(status(child), false);
    assert.equal(status(file), false);
  } finally {
    await view.unmount();
  }
});

test('newly listed conflict tags update an empty search result until the next conflict refresh', async () => {
  const root = 'viking://resources';
  const wiki = `${root}/wiki`;
  const file = `${wiki}/fresh.md`;
  let browser;
  function Tree() {
    browser = useFileBrowser({ rootUri: root, currentUri: null });
    return null;
  }
  const fetch = recordingFetch(({ path, uri }) => {
    if (path.endsWith('/search/find')) return ok({ resources: [], total: 0 });
    if (path.endsWith('/ls') && uri === root)
      return ok([{ uri: wiki, isDir: true, tags: ['compile_target=true'] }]);
    if (path.endsWith('/ls') && uri === wiki)
      return ok([{ uri: file, tags: ['has_conflict=true'] }]);
    return ok({});
  });
  const view = await mount(h(OpenVikingProvider, { fetch }, h(Tree)));
  const status = (uri) => browser.dashboards.get(uri)?.pendingConflicts;
  try {
    assert.equal(fetch.countOf('/api/v1/search/find'), 1);
    assert.equal(status(wiki), false);

    await act(async () =>
      browser.actions.toggleExpand(
        browser.rows.find((row) => row.uri === wiki),
      ),
    );
    await settle();
    assert.equal(fetch.countOf('/api/v1/search/find'), 1);
    assert.equal(status(wiki), true);
    assert.equal(status(file), true);

    await act(async () => browser.refreshWikiConflicts());
    await settle();
    assert.equal(fetch.countOf('/api/v1/search/find'), 2);
    assert.deepEqual(browser.rows.find((row) => row.uri === file)?.tags, [
      'has_conflict=true',
    ]);
    assert.equal(status(wiki), false);
    assert.equal(status(file), false);
  } finally {
    await view.unmount();
  }
});
