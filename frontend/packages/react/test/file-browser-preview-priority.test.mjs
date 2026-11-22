import assert from 'node:assert/strict';
import test from 'node:test';
import { act, createElement as h, useMemo } from 'react';
import {
  OpenVikingProvider,
  useFileBrowser,
} from './helpers/internal-hooks.mjs';
import {
  deferred,
  mount,
  ok,
  recordingFetch,
  settle,
} from './helpers/module-dom.mjs';

test('listed memory Markdown reads once, reuses the body and invalidates on refresh, save and scope changes', async () => {
  const root = 'viking://user/alice/memories';
  const profile = `${root}/profile.md`;
  const soul = `${root}/soul.md`;
  const modTime = '2026-11-11T22:10:00.123Z';
  let browser;
  let body = 'Original profile';
  const fetch = recordingFetch(({ path, uri, body: write }) => {
    if (path.endsWith('/fs/ls'))
      return ok([profile, soul].map((uri) => ({ uri, isDir: false, modTime })));
    if (path.endsWith('/content/read'))
      return ok(uri === profile ? body : 'Original soul');
    if (path.endsWith('/content/write')) {
      body = write.content;
      return ok({});
    }
    if (path.endsWith('/fs/stat')) return ok({ isDir: false, modTime });
    throw new Error(`Unexpected request: ${path}`);
  });
  function Probe({ uri, accountId }) {
    const requestOptions = useMemo(() => ({ accountId }), [accountId]);
    browser = useFileBrowser({
      rootUri: root,
      controlled: true,
      currentUri: uri,
      requestOptions,
    });
    return null;
  }
  const page = (uri, accountId = 'one') =>
    h(OpenVikingProvider, { fetch }, h(Probe, { uri, accountId }));
  const view = await mount(page(profile));
  const tab = () => browser.previewTabs?.[0];
  const reads = () => fetch.countOf('/api/v1/content/read');
  try {
    assert.equal(tab().source.content, 'Original profile');
    assert.equal(browser.previewUpdatedAt, modTime);
    assert.equal(fetch.countOf('/api/v1/fs/stat'), 1);
    assert.equal(fetch.countOf('/api/v1/fs/attrs'), 0);
    assert.equal(reads(), 1);

    await view.render(page(soul));
    await view.render(page(profile));
    assert.equal(reads(), 2);
    assert.equal(tab().source.content, 'Original profile');

    body = 'Refreshed profile';
    await act(async () => browser.actions.refresh());
    await settle();
    assert.equal(reads(), 3);
    assert.equal(tab().source.content, 'Refreshed profile');

    await act(async () => {
      await tab().editing.save('Saved profile');
      tab().editing.onSave?.();
    });
    await settle();
    assert.equal(reads(), 4);
    assert.equal(tab().source.content, 'Saved profile');

    body = 'Other account profile';
    await view.render(page(profile, 'two'));
    assert.equal(reads(), 5);
    assert.equal(tab().source.content, 'Other account profile');
  } finally {
    await view.unmount();
  }
});

const fileTime = '2026-11-11T22:10:00.123Z';

test('a controlled deep link reads its preview while the root tree is pending', async () => {
  const root = 'viking://resources';
  const uri = `${root}/chapter.md`;
  const listing = deferred();
  const fetch = recordingFetch(({ path }) => {
    if (path.endsWith('/fs/ls')) return listing.promise;
    if (path.endsWith('/fs/stat')) return ok({ isDir: false });
    if (path.endsWith('/content/read')) return ok('Chapter text');
    return ok({});
  });
  let browser;
  function Probe() {
    browser = useFileBrowser({
      rootUri: root,
      controlled: true,
      currentUri: uri,
    });
    return null;
  }
  const view = await mount(h(OpenVikingProvider, { fetch }, h(Probe)));
  try {
    assert.equal(fetch.countOf('/api/v1/fs/ls'), 1);
    assert.equal(fetch.countOf('/api/v1/fs/stat'), 1);
    assert.equal(fetch.countOf('/api/v1/content/read'), 1);
    assert.equal(browser.previewTabs?.[0]?.source?.content, 'Chapter text');

    await act(async () =>
      listing.resolve(await ok([{ uri, isDir: false, modTime: fileTime }])),
    );
    await settle();
    assert.equal(fetch.countOf('/api/v1/content/read'), 1);
  } finally {
    await view.unmount();
  }
});

test('a listed file reads its body while optional stat metadata is pending', async () => {
  const root = 'viking://resources';
  const uri = `${root}/chapter.md`;
  const stat = deferred();
  const fetch = recordingFetch(({ path }) => {
    if (path.endsWith('/fs/ls')) return ok([{ uri, isDir: false }]);
    if (path.endsWith('/fs/stat')) return stat.promise;
    if (path.endsWith('/content/read')) return ok('Chapter text');
    return ok({});
  });
  let browser;
  function Probe() {
    browser = useFileBrowser({
      rootUri: root,
      controlled: true,
      currentUri: uri,
    });
    return null;
  }
  const view = await mount(h(OpenVikingProvider, { fetch }, h(Probe)));
  try {
    assert.equal(fetch.countOf('/api/v1/fs/stat') > 0, true);
    assert.equal(fetch.countOf('/api/v1/content/read'), 1);
    assert.equal(browser.previewTabs?.[0]?.source?.content, 'Chapter text');
    await act(async () => stat.resolve(await ok({ isDir: false })));
  } finally {
    await view.unmount();
  }
});

test('a listed Markdown file reads its body while Wiki attrs are pending', async () => {
  const root = 'viking://resources';
  const uri = `${root}/chapter.md`;
  const attrs = deferred();
  const fetch = recordingFetch(({ path }) => {
    if (path.endsWith('/fs/ls'))
      return ok([{ uri, isDir: false, modTime: fileTime }]);
    if (path.endsWith('/fs/attrs')) return attrs.promise;
    if (path.endsWith('/content/read')) return ok('Chapter text');
    if (path.endsWith('/fs/stat'))
      return ok({ isDir: false, modTime: fileTime });
    return ok({});
  });
  let browser;
  function Probe() {
    browser = useFileBrowser({
      rootUri: root,
      controlled: true,
      currentUri: uri,
    });
    return null;
  }
  const view = await mount(h(OpenVikingProvider, { fetch }, h(Probe)));
  const body = () => browser.previewTabs?.[0]?.source?.content;
  try {
    assert.equal(fetch.countOf('/api/v1/fs/attrs'), 1);
    assert.equal(body(), 'Chapter text');
    assert.equal(browser.previewUpdatedAt, fileTime);
    assert.equal(fetch.countOf('/api/v1/fs/stat'), 1);
    await act(async () =>
      attrs.resolve(
        await ok({ uri, attrs: { tags: ['compile_target=true'] } }),
      ),
    );
    await settle();
    assert.equal(fetch.countOf('/api/v1/content/read'), 1);
    assert.equal(body(), 'Chapter text');
  } finally {
    await view.unmount();
  }
});
