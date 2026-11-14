import assert from 'node:assert/strict';
import test from 'node:test';
import { act, createElement as h } from 'react';
import { QueryCache, QueryClient } from '@tanstack/react-query';
import {
  mount,
  deferred,
  ok,
  recordingFetch,
  settle,
} from './helpers/module-dom.mjs';
const hooks = process.env.OV_UI_TEST_HOOKS;
const { OpenVikingProvider, useOvClient } = await import(
  `${hooks}/provider.js`
);
const { useFsTree } = await import(`${hooks}/fs-tree.js`);
const { useSearchScopePage } = await import(`${hooks}/scope-page.js`);
const ROOT = 'viking://resources';
function setup(fetch, options = {}, onError) {
  const queryClient = new QueryClient({
    queryCache: new QueryCache({ onError }),
    defaultOptions: { queries: { retry: false, staleTime: 5000 } },
  });
  let tree, picker, client;
  function Probe() {
    client = useOvClient();
    tree = useFsTree({ client, rootUri: ROOT });
    picker = useSearchScopePage(ROOT);
    return null;
  }
  return {
    queryClient,
    element: () =>
      h(OpenVikingProvider, { fetch, queryClient, ...options }, h(Probe)),
    get tree() {
      return tree;
    },
    get picker() {
      return picker;
    },
    get client() {
      return client;
    },
  };
}

test('tree and picker share cache/inflight requests; closing one consumer leaves the other active', async () => {
  const pending = deferred();
  const fetch = recordingFetch(() => pending.promise);
  const state = setup(fetch);
  const view = await mount(state.element());
  try {
    await act(() => state.picker.load(ROOT));
    await settle();
    assert.equal(fetch.calls.length, 1);
    await act(() => state.picker.cancel());
    assert.equal(fetch.calls[0].init.signal.aborted, false);
    await act(async () =>
      pending.resolve(await ok([{ uri: `${ROOT}/docs`, isDir: true }])),
    );
    await settle();
    assert.equal(state.tree.rows[0].uri, `${ROOT}/docs`);
    await act(() => state.picker.load(ROOT));
    await settle();
    assert.deepEqual(state.picker.page.children, [`${ROOT}/docs`]);
    assert.equal(fetch.calls.length, 1);
    const cached = state.queryClient.getQueryCache().getAll();
    assert.equal(cached.length, 1);
    assert.equal(cached[0].state.data.pages[0].entries[0].uri, `${ROOT}/docs`);
  } finally {
    await view.unmount();
    state.queryClient.clear();
  }
});

test('resolved async identities isolate requests even when the identity function is unchanged', async () => {
  let account = 'A';
  const old = deferred();
  const fetch = recordingFetch((call) =>
    new Headers(call.init.headers).get('X-OpenViking-Account') === 'A'
      ? old.promise
      : ok([{ uri: `${ROOT}/B`, isDir: true }]),
  );
  const state = setup(fetch, {
    accountId: async () => account,
    apiKey: 'private-credential',
  });
  const view = await mount(state.element());
  try {
    account = 'B';
    await view.render(state.element());
    await act(async () =>
      old.resolve(await ok([{ uri: `${ROOT}/A`, isDir: true }])),
    );
    await settle();
    assert.deepEqual(
      state.tree.rows.map((row) => row.uri),
      [`${ROOT}/B`],
    );
    assert.equal(fetch.calls.length, 2);
    assert.equal(
      JSON.stringify(
        state.queryClient
          .getQueryCache()
          .getAll()
          .map((query) => query.queryKey),
      ).includes('private-credential'),
      false,
    );
  } finally {
    await view.unmount();
    state.queryClient.clear();
  }
});

test('mkdir invalidates shared directory caches; a later refresh failure retains data and API success', async () => {
  let added = false,
    broken = false;
  const fetch = recordingFetch((call) => {
    if (call.path.endsWith('/mkdir')) {
      added = true;
      return ok({ uri: `${ROOT}/new` });
    }
    if (broken)
      return Promise.resolve(new Response('refresh failed', { status: 503 }));
    return ok([
      { uri: `${ROOT}/old`, isDir: true },
      ...(added ? [{ uri: `${ROOT}/new`, isDir: true }] : []),
    ]);
  });
  const state = setup(fetch);
  const view = await mount(state.element());
  try {
    await act(async () =>
      state.client.request({
        method: 'POST',
        path: '/api/v1/fs/mkdir',
        body: { uri: `${ROOT}/new` },
      }),
    );
    await settle();
    assert.equal(
      state.tree.rows.some((row) => row.uri === `${ROOT}/new`),
      true,
    );
    broken = true;
    const result = await state.client.request({
      method: 'POST',
      path: '/api/v1/fs/mkdir',
      body: { uri: `${ROOT}/another` },
    });
    assert.equal(result.uri, `${ROOT}/new`);
    await settle();
    assert.equal(
      state.tree.rows.some((row) => row.uri === `${ROOT}/new`),
      true,
    );
    assert.ok(state.tree.error);
  } finally {
    await view.unmount();
    state.queryClient.clear();
  }
});

test('identity changes during query preparation do not report a spurious host cache error', async () => {
  let resolutions = 0;
  const errors = [];
  const fetch = recordingFetch((call) =>
    ok([
      {
        uri: `${ROOT}/${new Headers(call.init.headers).get('X-OpenViking-Account')}`,
        isDir: true,
      },
    ]),
  );
  const state = setup(
    fetch,
    { accountId: async () => (++resolutions === 1 ? 'A' : 'B') },
    (error) => errors.push(error),
  );
  const view = await mount(state.element());
  try {
    assert.equal(errors.length, 0);
    assert.equal(
      state.tree.rows.some((row) => row.uri === `${ROOT}/B`),
      true,
    );
  } finally {
    await view.unmount();
    state.queryClient.clear();
  }
});
