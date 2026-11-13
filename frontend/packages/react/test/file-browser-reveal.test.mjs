import assert from 'node:assert/strict';
import test from 'node:test';
import { act, createElement as h, createRef } from 'react';
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
  render,
  listing,
  command,
} from './helpers/file-browser-reveal.mjs';

for (const controlled of [false, true]) {
  test(`public ref reveals later-page ancestors and target (${controlled ? 'controlled' : 'uncontrolled'})`, async () => {
    scrolled.length = 0;
    const fetch = recordingFetch(listing);
    const handle = createRef();
    const view = await mount(render(fetch, handle, controlled));
    try {
      assert.equal(await command(handle, FILE), true);
      const selected = view.container.querySelector(
        '[data-file-tree-row-id][aria-selected="true"]',
      );
      assert.match(selected.textContent, /result.png/);
      assert.match(
        view.container.querySelector('.ov-file-browser__preview-pane')
          .textContent,
        /result.png/,
      );
      assert.deepEqual(scrolled, [FILE]);
      assert.deepEqual(
        fetch.calls
          .filter((c) => c.path === '/api/v1/fs/ls' && Number(c.query.offset))
          .map((c) => c.uri),
        [ROOT, DIR],
      );
      assert.equal(await command(handle, FILE), true);
      assert.deepEqual(scrolled, [FILE, FILE]);
      assert.equal(
        fetch.calls.filter((c) => c.path === '/api/v1/fs/ls' && c.uri === DIR)
          .length,
        2,
      );
    } finally {
      await view.unmount();
    }
  });
}
test('preview-only opening skips tree requests and scrolling', async () => {
  scrolled.length = 0;
  const fetch = recordingFetch(listing);
  const handle = createRef();
  const view = await mount(render(fetch, handle));
  try {
    const count = fetch.countOf('/api/v1/fs/ls');
    assert.equal(await command(handle, FILE, { reveal: false }), true);
    assert.equal(fetch.countOf('/api/v1/fs/ls'), count);
    assert.equal(scrolled.length, 0);
  } finally {
    await view.unmount();
  }
});
test('missing and out-of-root targets resolve false without endless pagination', async () => {
  const fetch = recordingFetch(listing);
  const handle = createRef();
  const view = await mount(render(fetch, handle));
  try {
    assert.equal(await command(handle, `${DIR}/missing.png`), false);
    const count = fetch.countOf('/api/v1/fs/ls');
    assert.equal(await command(handle, 'viking://other/file.png'), false);
    assert.equal(fetch.countOf('/api/v1/fs/ls'), count);
  } finally {
    await view.unmount();
  }
});
test('a newer command cancels a slow reveal and only scrolls the new selection', async () => {
  scrolled.length = 0;
  const pending = deferred();
  const fetch = recordingFetch((call) =>
    call.path === '/api/v1/fs/ls' && call.uri === DIR
      ? pending.promise
      : listing(call),
  );
  const handle = createRef();
  const view = await mount(render(fetch, handle));
  try {
    let first;
    await act(async () => {
      first = handle.current.openResource(FILE);
    });
    await settle();
    const signal = fetch.calls.find(
      (c) => c.path === '/api/v1/fs/ls' && c.uri === DIR,
    ).init.signal;
    assert.equal(await command(handle, `${ROOT}/first.png`), true);
    assert.equal(await first, false);
    assert.equal(signal.aborted, true);
    await act(async () => pending.resolve(await ok([{ uri: FILE }])));
    await settle();
    assert.equal(scrolled.length, 1);
    assert.match(
      view.container.querySelector(
        '[data-file-tree-row-id][aria-selected="true"]',
      ).textContent,
      /first.png/,
    );
  } finally {
    await view.unmount();
  }
});

test('ordinary controlled URI changes preserve collapsed branches and do not scroll', async () => {
  scrolled.length = 0;
  const handle = createRef();
  const fetch = recordingFetch(listing);
  const element = (uri) =>
    h(
      OpenVikingProvider,
      { fetch, baseURL: 'https://ov.test' },
      h(FileBrowser, { ref: handle, rootUri: ROOT, dashboardKinds: [], uri }),
    );
  const view = await mount(element(''));
  try {
    const count = fetch.countOf('/api/v1/fs/ls');
    await view.render(element(FILE));
    assert.equal(fetch.countOf('/api/v1/fs/ls'), count);
    assert.equal(
      view.container.querySelector(
        '[data-file-tree-row-id][aria-selected="true"]',
      ),
      null,
    );
    assert.equal(scrolled.length, 0);
  } finally {
    await view.unmount();
  }
});

test('a new import result is revealed even if its cached root skips a virtual ancestor', async () => {
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
  const handle = createRef();
  const view = await mount(render(fetch, handle));
  try {
    imported = true;
    assert.equal(await command(handle, result), true);
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
