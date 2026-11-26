import assert from 'node:assert/strict';
import test from 'node:test';
import { act, createElement as h } from 'react';
import { FileBrowser } from '@openviking/react/modules/file-browser';
import { OpenVikingProvider } from '@openviking/react/client';
import {
  deferred,
  mount,
  ok,
  recordingFetch,
  settle,
} from './helpers/module-dom.mjs';

test('a deep-linked file keeps its preview when the restored tree discovers it', async () => {
  const root = 'viking://resources';
  const directory = `${root}/docs`;
  const file = `${directory}/profile.md`;
  const children = deferred();
  const repeatedRead = deferred();
  let reads = 0;
  const fetch = recordingFetch((call) => {
    if (call.path === '/api/v1/fs/ls') {
      return call.uri === directory
        ? children.promise
        : ok([{ uri: directory, isDir: true }]);
    }
    if (call.path === '/api/v1/fs/stat')
      return ok({ isDir: call.uri !== file });
    if (call.path === '/api/v1/content/read' && call.uri === file) {
      reads += 1;
      return reads === 1 ? ok('') : repeatedRead.promise;
    }
    return ok([]);
  });
  const view = await mount(
    h(
      OpenVikingProvider,
      { fetch, baseURL: 'https://ov.test' },
      h(FileBrowser, {
        rootUri: root,
        uri: file,
        defaultExpandedUri: directory,
        dashboardKinds: [],
      }),
    ),
  );
  try {
    assert.match(view.container.textContent, /This file is empty/);
    await act(async () =>
      children.resolve(await ok([{ uri: file, isDir: false }])),
    );
    await settle();
    assert.ok(
      view.container.querySelector(
        '[data-file-tree-row-id][aria-selected="true"]',
      ),
    );
    assert.match(view.container.textContent, /This file is empty/);
    assert.equal(reads, 1);
  } finally {
    await view.unmount();
  }
});
