import assert from 'node:assert/strict';
import test from 'node:test';
import { act, createElement, createRef, useState } from 'react';

import {
  OpenVikingProvider,
  useFileBrowser,
} from './helpers/internal-hooks.mjs';
import { mount, ok, recordingFetch, settle } from './helpers/module-dom.mjs';
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
    assert.match(
      view.container.querySelector('.ov-file-browser__preview-pane')
        .textContent,
      /result.png/,
    );
  } finally {
    await view.unmount();
  }
});
