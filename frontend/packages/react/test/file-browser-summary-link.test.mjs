import assert from 'node:assert/strict';
import test from 'node:test';
import { act, createElement, createRef, useState } from 'react';

import {
  OpenVikingProvider,
  useFileBrowser,
  usePreviewTabs,
} from './helpers/internal-hooks.mjs';
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
  render,
  listing,
  command,
} from './helpers/file-browser-reveal.mjs';

const answer = (call) => {
  if (call.path === '/api/v1/content/abstract') return ok('L0 TEXT');
  if (call.path === '/api/v1/content/overview') return ok('L1 TEXT');
  return listing(call);
};

async function mountBrowser(controlled) {
  const api = { current: undefined };
  const changes = [];
  function Probe() {
    const [uri, setUri] = useState(null);
    api.current = useFileBrowser({
      rootUri: ROOT,
      currentUri: controlled ? uri : null,
      controlled,
      onUriChange(next) {
        changes.push(next);
        setUri(next);
      },
    });
    return null;
  }
  const view = await mount(
    createElement(
      OpenVikingProvider,
      { baseURL: 'https://ov.test', fetch: recordingFetch(answer) },
      createElement(Probe, null),
    ),
  );
  return { api, view, changes };
}
const open = async (api, uri) => {
  let result;
  await act(async () => {
    result = await api.current.actions.openResource(uri);
  });
  return result;
};
const directoryTab = (api) =>
  api.current.previewTabs?.find((tab) => tab.kind === 'directory');

for (const controlled of [false, true]) {
  test(`summary file URIs open the owning directory on L0 or L1 (${controlled ? 'controlled' : 'uncontrolled'})`, async () => {
    const { api, view, changes } = await mountBrowser(controlled);
    try {
      // DIR sits on the second page of ROOT, so locating it also needs paging.
      assert.equal(await open(api, `${DIR}/.overview.md`), true);
      assert.equal(api.current.currentUri, DIR);
      assert.equal(changes.at(-1), DIR);
      assert.equal(directoryTab(api).activeLevelKey, 'l1');
      assert.match(
        api.current.rows.find((row) => row.uri === DIR)?.id ?? '',
        /./,
      );
      assert.equal(await open(api, `${DIR}/.abstract.md`), true);
      assert.equal(directoryTab(api).activeLevelKey, 'l0');
      assert.equal(await open(api, `${DIR}/.overview.md`), true);
      assert.equal(directoryTab(api).activeLevelKey, 'l1');
    } finally {
      await view.unmount();
    }
  });
}

test('ordinary resources named like Object.prototype members are not summary files', async () => {
  const { api, view } = await mountBrowser(false);
  try {
    for (const name of ['constructor', 'toString', '__proto__']) {
      await open(api, `${DIR}/${name}`);
      assert.equal(api.current.currentUri, `${DIR}/${name}`);
    }
  } finally {
    await view.unmount();
  }
});

test('preview tab links resolve, drop anchors and navigate through the given opener', async () => {
  const opened = [];
  const windows = [];
  const originalOpen = window.open;
  window.open = (...args) => windows.push(args);
  const tabs = [
    { kind: 'file', key: `${DIR}/doc.md`, source: { content: '' } },
    { kind: 'directory', key: DIR, levels: [{ key: 'l0' }] },
    { kind: 'directory', key: `${DIR}/`, levels: [{ key: 'l0' }] },
    { kind: 'content', key: 'custom' },
  ];
  const api = { current: undefined };
  function Probe() {
    api.current = usePreviewTabs(
      { previewTabs: tabs },
      async (uri) => opened.push(uri) > 0,
    ).previewTabs;
    return null;
  }
  const view = await mount(createElement(Probe, null));
  try {
    const [file, directory, slashed, content] = api.current;
    assert.equal(content.operations, undefined);
    // Relative links resolve against the document, anchors are not part of the name.
    await file.operations.openUri('.overview.md#section');
    await file.operations.openUri(`${FILE}#frag`);
    await file.operations.openUri(`${DIR}/.abstract.md`);
    // Encoded reserved characters belong to the file name.
    await file.operations.openUri(`${DIR}/a%23b%3Fc.md`);
    // A directory summary resolves relative links against the directory itself.
    await directory.operations.openUri('./child.md');
    // A directory URI that already ends with a slash gains no second one.
    await slashed.operations.openUri('./child.md');
    assert.deepEqual(opened, [
      `${DIR}/.overview.md`,
      FILE,
      `${DIR}/.abstract.md`,
      `${DIR}/a#b?c.md`,
      `${DIR}/child.md`,
      `${DIR}/child.md`,
    ]);
    // External links open a new window; page anchors do nothing.
    await file.operations.openUri('https://example.com/a');
    await file.operations.openUri('#section');
    assert.equal(opened.length, 6);
    assert.equal(windows.length, 1);
    assert.equal(windows[0][0], 'https://example.com/a');
  } finally {
    window.open = originalOpen;
    await view.unmount();
  }
});

test('refresh rejects when re-revealing the selection fails', async () => {
  let failing = false;
  const fetch = recordingFetch((call) =>
    failing && call.path === '/api/v1/fs/ls' && Number(call.query.offset)
      ? Promise.resolve(new Response('boom', { status: 500 }))
      : answer(call),
  );
  const handle = createRef();
  const view = await mount(render(fetch, handle));
  try {
    assert.equal(await command(handle, FILE), true);
    failing = true;
    await assert.rejects(async () => {
      await act(async () => handle.current.refresh());
    });
  } finally {
    await view.unmount();
  }
});

test('refresh rejects when an expanded directory fails to reload', async () => {
  let failing = false;
  const fetch = recordingFetch((call) =>
    failing && call.path === '/api/v1/fs/ls' && call.uri === DIR
      ? Promise.resolve(new Response('boom', { status: 500 }))
      : answer(call),
  );
  const handle = createRef();
  const view = await mount(render(fetch, handle));
  try {
    assert.equal(await command(handle, FILE), true);
    failing = true;
    await assert.rejects(async () => {
      await act(async () => handle.current.refresh());
    });
  } finally {
    await view.unmount();
  }
});

test('a failing directory does not stop the remaining expanded directories from reloading', async () => {
  let failing = false;
  const fetch = recordingFetch((call) =>
    failing && call.path === '/api/v1/fs/ls' && call.uri === ROOT
      ? Promise.resolve(new Response('boom', { status: 500 }))
      : answer(call),
  );
  const handle = createRef();
  const view = await mount(render(fetch, handle));
  try {
    assert.equal(await command(handle, FILE), true);
    const before = fetch.countOf('/api/v1/fs/ls');
    failing = true;
    await assert.rejects(async () => {
      await act(async () => handle.current.refresh());
    });
    // ROOT fails first; DIR (expanded after it) must still be requested.
    const after = fetch.calls
      .slice(before)
      .filter((c) => c.path === '/api/v1/fs/ls');
    assert.ok(after.some((c) => c.uri === DIR));
  } finally {
    await view.unmount();
  }
});

test('a refresh interrupted by an account switch does not reveal with the old account', async () => {
  const gate = deferred();
  let gated = false;
  const fetch = recordingFetch((call) => {
    if (gated && call.path === '/api/v1/fs/ls' && call.uri === ROOT) {
      gated = false;
      return gate.promise;
    }
    return answer(call);
  });
  const handle = createRef();
  const view = await mount(render(fetch, handle, false, 'a'));
  try {
    assert.equal(await command(handle, FILE), true);
    gated = true;
    let refreshing;
    await act(async () => {
      refreshing = handle.current.refresh();
    });
    await view.render(render(fetch, handle, false, 'b'));
    await settle();
    const dirRequests = () =>
      fetch.calls.filter((c) => c.path === '/api/v1/fs/ls' && c.uri === DIR)
        .length;
    const before = dirRequests();
    await act(async () => gate.resolve(await ok([{ uri: DIR, isDir: true }])));
    await refreshing.catch(() => {});
    await settle();
    assert.equal(dirRequests(), before);
  } finally {
    await view.unmount();
  }
});

test('a controlled host that normalises the URI still gets the summary level', async () => {
  const api = { current: undefined };
  function Probe() {
    const [uri, setUri] = useState(null);
    api.current = useFileBrowser({
      rootUri: ROOT,
      currentUri: uri,
      controlled: true,
      // The host stores a trailing-slash spelling of what it was told.
      onUriChange: (next) => setUri(`${next}/`),
    });
    return null;
  }
  const view = await mount(
    createElement(
      OpenVikingProvider,
      { baseURL: 'https://ov.test', fetch: recordingFetch(answer) },
      createElement(Probe, null),
    ),
  );
  try {
    await open(api, `${DIR}/.overview.md`);
    assert.equal(api.current.currentUri, `${DIR}/`);
    assert.equal(directoryTab(api).activeLevelKey, 'l1');
  } finally {
    await view.unmount();
  }
});

test('manual level switching updates the controlled level', async () => {
  const { api, view } = await mountBrowser(false);
  try {
    await open(api, `${DIR}/.overview.md`);
    await act(async () => directoryTab(api).onActiveLevelChange('l0'));
    assert.equal(directoryTab(api).activeLevelKey, 'l0');
  } finally {
    await view.unmount();
  }
});

test('a later plain navigation does not inherit the summary level', async () => {
  const { api, view } = await mountBrowser(false);
  try {
    await open(api, `${DIR}/.overview.md`);
    await open(api, FILE);
    await open(api, DIR);
    assert.equal(directoryTab(api).activeLevelKey, undefined);
  } finally {
    await view.unmount();
  }
});

test('the public handle refreshes in place and keeps the selection', async () => {
  const fetch = recordingFetch(answer);
  const handle = createRef();
  const view = await mount(render(fetch, handle));
  try {
    assert.equal(await command(handle, FILE), true);
    const before = fetch.countOf('/api/v1/fs/ls');
    await act(async () => handle.current.refresh());
    await settle();
    assert.ok(fetch.countOf('/api/v1/fs/ls') > before);
    // The row lives on the second page of DIR, so refresh must re-reveal it.
    assert.match(
      view.container.querySelector(
        '[data-file-tree-row-id][aria-selected="true"]',
      ).textContent,
      /result.png/,
    );
  } finally {
    await view.unmount();
  }
});
