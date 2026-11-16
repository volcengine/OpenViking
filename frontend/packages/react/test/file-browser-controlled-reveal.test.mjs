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

import {
  ROOT,
  DIR,
  FILE,
  scrolled,
  listing,
} from './helpers/file-browser-reveal.mjs';

test('controlled URI changes reveal and scroll without emitting a selection change', async () => {
  scrolled.length = 0;
  const changes = [];
  const fetch = recordingFetch(listing);
  const element = (uri) =>
    controlledBrowser(fetch, uri, (next) => changes.push(next));
  const view = await mount(element(''));
  try {
    await view.render(element(FILE));
    await settle();
    assert.equal(
      view.container
        .querySelector('[data-file-tree-row-id][aria-selected="true"]')
        ?.getAttribute('data-file-tree-row-id'),
      FILE,
    );
    assert.deepEqual(scrolled, [FILE]);
    assert.deepEqual(changes, []);
    await view.render(element(FILE));
    assert.deepEqual(scrolled, [FILE]);
  } finally {
    await view.unmount();
  }
});

test('the initial controlled URI leaves initial tree expansion to defaultExpandedUri', async () => {
  scrolled.length = 0;
  const fetch = recordingFetch(listing);
  const view = await mount(controlledBrowser(fetch, FILE));
  try {
    assert.equal(fetch.countOf('/api/v1/fs/ls'), 1);
    assert.deepEqual(scrolled, []);
  } finally {
    await view.unmount();
  }
});

test('a controlled URI received during initial loading is revealed when the tree is ready', async () => {
  scrolled.length = 0;
  const initialPage = deferred();
  const fetch = recordingFetch((call) =>
    call.path === '/api/v1/fs/ls' &&
    call.uri === ROOT &&
    !Number(call.query.offset)
      ? initialPage.promise
      : listing(call),
  );
  const view = await mount(controlledBrowser(fetch, ''));
  try {
    await view.render(controlledBrowser(fetch, FILE));
    assert.deepEqual(scrolled, []);
    await act(async () =>
      initialPage.resolve(await ok([{ uri: `${ROOT}/first.png` }], true)),
    );
    await settle();
    assert.deepEqual(scrolled, [FILE]);
  } finally {
    await view.unmount();
  }
});

test('clearing a controlled URI cancels its unfinished tree navigation', async () => {
  scrolled.length = 0;
  const pending = deferred();
  const fetch = recordingFetch((call) =>
    call.path === '/api/v1/fs/ls' && call.uri === DIR
      ? pending.promise
      : listing(call),
  );
  const view = await mount(controlledBrowser(fetch, ''));
  try {
    await view.render(controlledBrowser(fetch, FILE));
    await settle();
    const signal = fetch.calls.find(
      (c) => c.path === '/api/v1/fs/ls' && c.uri === DIR,
    ).init.signal;
    await view.render(controlledBrowser(fetch, ''));
    assert.equal(signal.aborted, true);
    await act(async () => pending.resolve(await ok([{ uri: FILE }])));
    assert.deepEqual(scrolled, []);
  } finally {
    await view.unmount();
  }
});

function controlledBrowser(fetch, uri, onUriChange) {
  return h(
    OpenVikingProvider,
    { fetch, baseURL: 'https://ov.test' },
    h(FileBrowser, { rootUri: ROOT, dashboardKinds: [], uri, onUriChange }),
  );
}

test('a new controlled import result is revealed even if its cached root skips a virtual ancestor', async () => {
  let imported = false;
  const resultDir = `${ROOT}/virtual/nested`;
  const result = `${resultDir}/result.png`;
  const fetch = recordingFetch((call) => {
    if (call.path === '/api/v1/fs/ls' && call.uri === ROOT)
      return ok(imported ? [{ uri: resultDir, isDir: true }] : []);
    if (call.path === '/api/v1/fs/ls' && call.uri === resultDir)
      return ok([{ uri: result }]);
    return listing(call);
  });
  const view = await mount(controlledBrowser(fetch, ''));
  try {
    imported = true;
    await view.render(controlledBrowser(fetch, result));
    assert.equal(
      view.container
        .querySelector('[data-file-tree-row-id][aria-selected="true"]')
        ?.getAttribute('data-file-tree-row-id'),
      result,
    );
  } finally {
    await view.unmount();
  }
});
