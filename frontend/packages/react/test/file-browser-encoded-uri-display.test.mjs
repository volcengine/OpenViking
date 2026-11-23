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

const root = 'viking://resources';
const folderName = '%E8%B5%84%E6%96%99%20%25%2F%E6%A1%A3%E6%A1%88';
const fileName = '%E6%8A%A5%E5%91%8A%20%25%2F%E5%91%A8%E6%8A%A5.md';
const folder = `${root}/${folderName}`;
const file = `${folder}/${fileName}`;
const literalFolderName = '中文目录';
const literalFolder = `${root}/${literalFolderName}`;
const folderLabel = '资料 %/档案';
const fileLabel = '报告 %/周报.md';
const row = (view, uri) =>
  view.container.querySelector(`[data-file-tree-row-id="${uri}"]`);
const tabTitle = (view, uri = file) =>
  view.container.querySelector(
    `[role="tab"][data-key="${uri}"] .ov-file-preview__tab-title`,
  )?.textContent;

for (const status of [200, 503]) {
  test(`an unknown preview uses the resolved type for stat status ${status}`, async () => {
    const stat = deferred();
    const body = deferred();
    const fetch = recordingFetch(({ path, uri }) => {
      if (path === '/api/v1/fs/stat' && uri === file) return stat.promise;
      if (path === '/api/v1/content/read') return body.promise;
      return ok([]);
    });
    const view = await mount(
      h(
        OpenVikingProvider,
        { fetch, baseURL: 'https://ov.test' },
        h(FileBrowser, { rootUri: root, uri: file, dashboardKinds: [] }),
      ),
    );
    const icon = () =>
      view.container.querySelector(
        `[role="tab"][data-key="${file}"] .ov-file-preview__tab-icon svg`,
      );
    try {
      assert.equal(tabTitle(view), fileLabel);
      assert.equal(icon(), null);
      await act(async () =>
        stat.resolve(
          status === 200
            ? await ok({ uri: file, isDir: false })
            : new Response(null, { status }),
        ),
      );
      await settle();
      assert.equal(tabTitle(view), fileLabel);
      assert.equal(Boolean(icon()), status === 200);
      assert.ok(
        fetch.calls.some(({ path }) => path === '/api/v1/content/read'),
      );
      await act(async () => body.resolve(await ok('')));
      await settle();
      assert.ok(icon());
    } finally {
      body.resolve(await ok(''));
      await view.unmount();
    }
  });
}

test('FileBrowser keeps raw names in the tree and decodes preview labels', async (t) => {
  const body = deferred();
  const summary = deferred();
  const selected = [];
  const fetch = recordingFetch(({ path, uri }) => {
    if (path === '/api/v1/fs/ls')
      return ok(
        uri === root
          ? [
              { uri: folder, isDir: true },
              { uri: literalFolder, isDir: true },
            ]
          : [{ uri: file, isDir: false }],
      );
    if (path === '/api/v1/fs/stat')
      return ok({ uri, isDir: uri === root || uri === folder });
    if (path === '/api/v1/content/read') return body.promise;
    if (/\/content\/(abstract|overview)$/.test(path))
      return summary.promise.then(ok);
    return ok('');
  });
  const view = await mount(
    h(
      OpenVikingProvider,
      { fetch, baseURL: 'https://ov.test' },
      h(FileBrowser, {
        rootUri: root,
        dashboardKinds: [],
        defaultExpandedUris: [folder],
        onUriChange: (uri) => selected.push(uri),
      }),
    ),
  );
  try {
    await t.test('tree names preserve encoded and literal URI segments', () => {
      const names = [folder, literalFolder, file].map(
        (uri) =>
          row(view, uri)?.querySelector('.ov-file-tree__label')?.textContent,
      );
      assert.deepEqual(names, [folderName, literalFolderName, fileName]);
      assert.equal(
        view.container.querySelectorAll('[role="treeitem"]').length,
        3,
      );
    });

    await act(() => row(view, file).click());
    await settle();
    assert.equal(selected.at(-1), file);
    assert.ok(
      fetch.calls.some(
        ({ path, uri }) => path === '/api/v1/fs/stat' && uri === file,
      ),
    );
    assert.ok(
      fetch.calls.some(
        ({ path, uri }) => path === '/api/v1/content/read' && uri === file,
      ),
    );
    await t.test('loading preview tab shows the decoded file name', () => {
      assert.equal(tabTitle(view), fileLabel);
      assert.ok(
        view.container.querySelector(
          `[role="tab"][data-key="${file}"] .ov-file-preview__tab-icon svg`,
        ),
      );
    });

    await act(async () => body.resolve(await ok('')));
    await settle();
    await t.test('loaded tab and breadcrumb keep the same decoded name', () => {
      assert.equal(tabTitle(view), fileLabel);
      assert.equal(
        view.container.querySelector('.ov-file-preview__breadcrumb-current')
          ?.textContent,
        fileLabel,
      );
      const ancestors = [
        ...view.container.querySelectorAll('.ov-file-preview__breadcrumb-link'),
      ];
      assert.equal(ancestors.length, 2);
      assert.equal(ancestors.at(-1)?.textContent, folderLabel);
    });

    const ancestors = view.container.querySelectorAll(
      '.ov-file-preview__breadcrumb-link',
    );
    await act(() => ancestors[ancestors.length - 1].click());
    await settle();
    assert.equal(selected.at(-1), folder);
    await t.test('loading directory tab shows its decoded name', () => {
      assert.equal(tabTitle(view, folder), folderLabel);
      assert.ok(
        view.container.querySelector(
          `[role="tab"][data-key="${folder}"] .ov-file-preview__tab-icon svg`,
        ),
      );
    });
    assert.ok(
      fetch.calls.some(
        ({ path, uri }) =>
          /\/content\/(abstract|overview)$/.test(path) && uri === folder,
      ),
    );
    await act(() => summary.resolve(''));
    await settle();
    await t.test('loaded directory tab preserves its decoded name', () => {
      assert.equal(tabTitle(view, folder), folderLabel);
    });
    assert.ok(
      fetch.calls.some(
        ({ path, uri }) => path === '/api/v1/fs/ls' && uri === folder,
      ),
    );
    for (const call of fetch.calls) {
      if (
        /\/(fs\/(stat|ls)|content\/(read|abstract|overview))$/.test(call.path)
      )
        assert.ok(
          [root, folder, file].includes(call.uri),
          `unexpected rewritten URI ${call.uri}`,
        );
    }
  } finally {
    summary.resolve('');
    body.resolve(await ok(''));
    await view.unmount();
  }
});
