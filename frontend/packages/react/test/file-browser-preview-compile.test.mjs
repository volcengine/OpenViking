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

const fileTime = '2026-11-11T22:10:00.123Z';

for (const dashboardKinds of [undefined, []]) {
  test(`Compile preview uses file metadata and legacy ownership (${dashboardKinds ?? 'default'})`, async () => {
    const target = 'viking://resources/wiki';
    const chapter = `${target}/chapter.md`;
    const ordinary = `${target}/README.md`;
    const directory = `${target}/nested`;
    const unrelated = 'viking://resources/unrelated/nested';
    const legacy = 'viking://resources/legacy/chapter.md';
    const legacyTarget = 'viking://resources/legacy';

    const fetch = recordingFetch(({ path, uri }) => {
      if (path.endsWith('/fs/ls')) return ok([]);
      if (path.endsWith('/fs/stat'))
        return ok({ isDir: uri === directory, modTime: fileTime });
      if (path.endsWith('/fs/attrs'))
        return ok({
          uri,
          attrs: { tags: uri === target ? ['compile_target=true'] : [] },
        });
      if (path.endsWith('/compile/configs'))
        return ok({ targets: [{ to: legacyTarget }, { to: unrelated }] });
      if (path.endsWith('/content/read'))
        return ok(
          uri === chapter || uri === legacy
            ? 'Sample Markdown\n<!-- CONFLICTS_CONTENT\n-->'
            : 'Sample Markdown',
        );
      return ok('');
    });
    const page = (uri) =>
      h(
        OpenVikingProvider,
        { baseURL: 'https://ov.test', fetch },
        h(FileBrowser, { uri, dashboardKinds }),
      );
    const view = await mount(page(ordinary));
    const meta = () =>
      view.container.querySelector('.ov-file-browser__preview-meta')
        ?.textContent;
    const attrs = (uri) =>
      fetch.calls.filter(
        (call) => call.uri === uri && call.path.endsWith('/fs/attrs'),
      ).length;

    try {
      assert.match(meta(), /15 characters/);
      assert.doesNotMatch(meta(), /AI generated/);
      assert.equal(attrs(ordinary), dashboardKinds ? 0 : 1);
      assert.equal(attrs(target), 0);

      await view.render(page(directory));
      assert.doesNotMatch(meta(), /AI generated/);
      assert.equal(attrs(target), 0);

      await view.render(page(chapter));
      assert.match(meta(), /AI generated/);
      assert.equal(attrs(chapter), 1);
      assert.equal(attrs(target), 1);

      await view.render(page(legacy));
      assert.match(meta(), /AI generated/);
      assert.equal(attrs(legacy), 1);
    } finally {
      await view.unmount();
    }
  });
}

test('long Wiki preview renders before legacy ownership and finds a trailer beyond the line cap', async () => {
  const target = 'viking://resources/legacy';
  const uri = `${target}/long.md`;
  const registry = deferred();
  const first = Array.from({ length: 2000 }, () => 'body').join('\n');
  const fetch = recordingFetch(({ path, query }) => {
    if (path.endsWith('/fs/ls')) return ok([]);
    if (path.endsWith('/fs/stat')) return ok({ isDir: false });
    if (path.endsWith('/fs/attrs')) return ok({ attrs: { tags: [] } });
    if (path.endsWith('/compile/configs')) return registry.promise;
    return ok(
      query.limit === '-1'
        ? `${first}\nMore\n<!-- CONFLICTS_CONTENT\n-->`
        : first,
    );
  });
  const view = await mount(
    h(
      OpenVikingProvider,
      { baseURL: 'https://ov.test', fetch },
      h(FileBrowser, { uri, dashboardKinds: [] }),
    ),
  );
  const meta = () =>
    view.container.querySelector('.ov-file-browser__preview-meta')?.textContent;
  try {
    assert.match(meta(), /characters/);
    assert.doesNotMatch(meta(), /AI generated/);
    assert.equal(
      fetch.calls.filter((call) => call.query.limit === '-1').length,
      0,
    );
    await act(async () =>
      registry.resolve(await ok({ targets: [{ to: target }] })),
    );
    await settle();
    assert.match(meta(), /AI generated/);
    assert.equal(
      fetch.calls.filter((call) => call.path.endsWith('/content/read')).length,
      2,
    );
  } finally {
    await view.unmount();
  }
});

test('failed Compile ownership lookup leaves a short Markdown preview visible', async () => {
  const attrs = deferred();
  const uri = 'viking://resources/chapter.md';
  const fetch = recordingFetch(({ path }) => {
    if (path.endsWith('/fs/ls')) return ok([]);
    if (path.endsWith('/fs/stat')) return ok({ isDir: false });
    if (path.endsWith('/fs/attrs')) return attrs.promise;
    return ok('Chapter text\n<!-- CONFLICTS_CONTENT\n-->');
  });
  const view = await mount(
    h(
      OpenVikingProvider,
      { baseURL: 'https://ov.test', fetch },
      h(FileBrowser, { uri, dashboardKinds: [] }),
    ),
  );
  const meta = () =>
    view.container.querySelector('.ov-file-browser__preview-meta')?.textContent;
  try {
    assert.match(meta(), /characters/);
    await act(async () => attrs.resolve(new Response(null, { status: 500 })));
    await settle();
    assert.match(meta(), /characters/);
    assert.doesNotMatch(meta(), /AI generated/);
  } finally {
    await view.unmount();
  }
});

test('late Dashboard attrs do not reload directory summaries or non-Markdown files', async () => {
  for (const isDir of [true, false]) {
    const attrs = deferred();
    const uri = isDir
      ? 'viking://resources/dir.md'
      : 'viking://resources/notes.txt';
    const fetch = recordingFetch(({ path }) => {
      if (path.endsWith('/fs/ls')) return ok([]);
      if (path.endsWith('/fs/stat')) return ok({ isDir });
      if (path.endsWith('/fs/attrs')) return attrs.promise;
      return ok('Visible content');
    });
    const view = await mount(
      h(
        OpenVikingProvider,
        { baseURL: 'https://ov.test', fetch },
        h(FileBrowser, { uri }),
      ),
    );
    const reads = () =>
      fetch.calls.filter(
        (call) =>
          call.uri === uri &&
          (call.path.includes('/content/') || call.path.endsWith('/fs/stat')),
      ).length;
    try {
      assert.match(
        view.container.querySelector('.ov-file-browser__preview-meta')
          ?.textContent,
        /15 characters/,
      );
      const initialReads = reads();
      assert.equal(initialReads, isDir ? 3 : 2);
      await act(async () =>
        attrs.resolve(await ok({ uri, attrs: { tags: [] } })),
      );
      await settle();
      assert.equal(reads(), initialReads);
      assert.match(
        view.container.querySelector('.ov-file-browser__preview-meta')
          ?.textContent,
        /15 characters/,
      );
    } finally {
      await view.unmount();
    }
  }
});
