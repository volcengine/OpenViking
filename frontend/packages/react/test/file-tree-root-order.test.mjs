import assert from 'node:assert/strict';
import test from 'node:test';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { OpenVikingProvider } from '@openviking/react/client';
import { FileTree } from '@openviking/react/components/file-tree';
import { FileBrowser } from '@openviking/react/modules/file-browser';
import { mount, ok } from './helpers/module-dom.mjs';

function directory(id, uri, depth = 0) {
  return { kind: 'entry', type: 'directory', id, uri, name: id, depth };
}

test('FileTree can reorder context root branches without reordering children or moving root pagination into a branch', () => {
  const rows = Object.freeze([
    directory('system', 'viking://system'),
    directory('resource', 'viking://resource'),
    directory('archive', 'viking://resource/archive', 1),
    directory('agent', 'viking://agents'),
    directory('alice', 'viking://user/alice'),
    directory('z-child', 'viking://user/alice/z-child', 1),
    directory('a-child', 'viking://user/alice/a-child', 1),
    {
      kind: 'load-more',
      id: 'user-more',
      depth: 1,
      parentId: 'alice',
    },
    directory('bob', 'viking://users/bob'),
    directory('sessions', 'viking://sessions'),
    directory('extras', 'viking://extras'),
    {
      kind: 'load-more',
      id: 'root-more',
      depth: 0,
      parentId: 'viking://',
    },
  ]);
  const renderIds = (props) =>
    Array.from(
      renderToStaticMarkup(
        createElement(FileTree, { rows, ...props }),
      ).matchAll(/data-file-tree-row-id="([^"]+)"/g),
      (match) => match[1],
    );

  assert.deepEqual(
    renderIds(),
    rows.map(({ id }) => id),
  );
  assert.deepEqual(renderIds({ rootOrder: 'context' }), [
    'alice',
    'z-child',
    'a-child',
    'user-more',
    'bob',
    'sessions',
    'agent',
    'resource',
    'archive',
    'system',
    'extras',
    'root-more',
  ]);
});

test('FileBrowser displays user first even when fs/ls returns it last', async () => {
  const uris = ['viking://agent', 'viking://resources', 'viking://user/alice'];
  const host = await mount(
    createElement(
      OpenVikingProvider,
      {
        baseURL: 'https://ov.test',
        fetch: () => ok(uris.map((uri) => ({ uri, isDir: true }))),
      },
      createElement(FileBrowser, { dashboardKinds: [] }),
    ),
  );
  try {
    const ids = Array.from(
      host.container.querySelectorAll('[data-file-tree-row-id]'),
      (row) => row.getAttribute('data-file-tree-row-id'),
    );
    assert.deepEqual(ids, [uris[2], uris[0], uris[1]]);
  } finally {
    await host.unmount();
  }
});
