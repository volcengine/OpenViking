import assert from 'node:assert/strict';
import test from 'node:test';
import { act, createElement as h } from 'react';
import { QueryClient } from '@tanstack/react-query';
import { OpenVikingProvider } from '@openviking/react/client';
import { FileBrowser } from '@openviking/react/modules/file-browser';
import { mount, ok, recordingFetch, settle } from './helpers/module-dom.mjs';

const hooks = process.env.OV_UI_TEST_HOOKS;
const { OpenVikingProvider: HookProvider, useOvClient } = await import(
  `${hooks}/provider.js`
);
const { useFsTree } = await import(`${hooks}/fs-tree.js`);
const ROOT = 'viking://resources';
const denied = `${ROOT}/restricted.md`;
const container = `${ROOT}/shared-container`;
const readable = `${container}/readable.md`;
const row = (view, uri) =>
  view.container.querySelector(`[data-file-tree-row-id="${uri}"]`);

test('hiding denied entries is opt-in and keeps traversal-only directories usable', async () => {
  const fetch = recordingFetch((call) =>
    ok(
      call.path === '/api/v1/fs/ls'
        ? call.uri === ROOT
          ? [
              { uri: denied, access: 'denied' },
              { uri: container, isDir: true, access: 'container' },
            ]
          : [{ uri: readable, access: 'read' }]
        : '',
    ),
  );
  const element = (hideDeniedEntries) =>
    h(
      OpenVikingProvider,
      { fetch, accountId: 'demo' },
      h(FileBrowser, { rootUri: ROOT, hideDeniedEntries, dashboardKinds: [] }),
    );
  const view = await mount(element(undefined));
  try {
    assert.equal(row(view, denied).getAttribute('aria-disabled'), 'true');
    const calls = fetch.calls.length;
    await view.render(element(true));
    assert.equal(row(view, denied) === null, true);
    assert.equal(fetch.calls.length, calls);
    assert.ok(row(view, container));
    await act(async () =>
      row(view, container).querySelector('[data-role="toggle"]').click(),
    );
    await settle();
    assert.ok(row(view, readable));
    await view.render(element(false));
    assert.equal(row(view, denied).getAttribute('aria-disabled'), 'true');
  } finally {
    await view.unmount();
  }
});

test('a page containing only denied entries retains pagination and permission metadata', async () => {
  const first = [{ uri: `${ROOT}/first.md`, access: 'read' }];
  const middle = Array.from({ length: 200 }, (_, index) => ({
    uri: `${ROOT}/denied-${index}.md`,
    access: 'denied',
  }));
  const last = [{ uri: `${ROOT}/last.md`, access: 'read' }];
  const offsets = [];
  const fetch = recordingFetch((call) => {
    const offset = Number(call.query.offset);
    offsets.push(offset);
    return Promise.resolve(
      Response.json({
        status: 'ok',
        result: offset === 0 ? first : offset === 200 ? middle : last,
        has_more: offset < 400,
      }),
    );
  });
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  let state;
  function Probe() {
    const client = useOvClient();
    state = useFsTree({ client, rootUri: ROOT, hideDeniedEntries: true });
    return null;
  }
  const view = await mount(h(HookProvider, { fetch, queryClient }, h(Probe)));
  try {
    await act(() => state.loadMore(ROOT));
    await settle();
    assert.equal(state.error, null);
    assert.deepEqual(
      state.rows
        .filter((entry) => entry.kind === 'entry')
        .map(({ uri }) => uri),
      [first[0].uri],
    );
    assert.ok(state.rows.some(({ kind }) => kind === 'load-more'));
    assert.equal(state.accessOf(middle[0].uri), 'denied');
    const query = queryClient.getQueryCache().getAll()[0];
    assert.deepEqual(query.state.data.pages[1].entries, middle);
    await act(() => state.loadMore(ROOT));
    await settle();
    assert.deepEqual(offsets, [0, 200, 400]);
    assert.deepEqual(
      state.rows
        .filter((entry) => entry.kind === 'entry')
        .map(({ uri }) => uri),
      [first[0].uri, last[0].uri],
    );
    assert.equal(
      state.rows.some(({ kind }) => kind === 'load-more'),
      false,
    );
    assert.deepEqual(query.state.data.pageParams, [0, 200, 400]);
    assert.deepEqual(middle[0], {
      uri: `${ROOT}/denied-0.md`,
      access: 'denied',
    });
  } finally {
    await view.unmount();
    queryClient.clear();
  }
});
