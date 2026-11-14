import assert from 'node:assert/strict';
import test from 'node:test';
import { act, createElement as h } from 'react';
import { QueryClient, dehydrate, hydrate } from '@tanstack/react-query';
import {
  mount,
  ok,
  deferred,
  recordingFetch,
  settle,
} from './helpers/module-dom.mjs';
const hooks = process.env.OV_UI_TEST_HOOKS;
const { OpenVikingProvider, useOvClient } = await import(
  `${hooks}/provider.js`
);
const { useFsTree } = await import(`${hooks}/fs-tree.js`);
const ROOT = 'viking://resources';
async function fixture(fetch, options = {}) {
  const queryClient =
    options.queryClient ??
    new QueryClient({
      defaultOptions: { queries: { retry: 1, retryDelay: 0, staleTime: 5000 } },
    });
  let tree, client;
  function Probe() {
    client = useOvClient();
    tree = useFsTree({ client, rootUri: ROOT });
    return null;
  }
  const view = await mount(
    h(OpenVikingProvider, { fetch, queryClient, ...options }, h(Probe)),
  );
  return {
    view,
    queryClient,
    get tree() {
      return tree;
    },
    get client() {
      return client;
    },
  };
}

test('a transient failure beyond the old exhausted window retries without rejecting previously fetched entries', async () => {
  let entries = Array.from({ length: 201 }, (_, i) => ({
    uri: `${ROOT}/old-${i}`,
  }));
  let failures = 0;
  const fetch = recordingFetch(({ query }) => {
    const offset = Number(query.offset);
    if (offset === 400 && failures++ === 0)
      return Promise.resolve(
        Response.json(
          {
            status: 'error',
            error: { code: 'FAILED', message: 'Temporary failure' },
          },
          { status: 503 },
        ),
      );
    return ok(
      entries.slice(offset, offset + 200),
      offset + 200 < entries.length,
    );
  });
  const state = await fixture(fetch);
  try {
    await act(() => state.tree.loadMore(ROOT));
    entries = Array.from({ length: 601 }, (_, i) => ({
      uri: `${ROOT}/file-${i}`,
    }));
    await act(() => state.tree.refresh());
    await settle();
    assert.equal(state.tree.error, null);
    assert.equal(state.tree.rows.length, 601);
    assert.equal(
      fetch.calls.filter((call) => call.query.offset === '400').length,
      2,
    );
  } finally {
    await state.view.unmount();
    state.queryClient.clear();
  }
});

test('a successful write supersedes an older background refresh for all shared consumers', async () => {
  const pending = deferred();
  let reads = 0,
    added = false,
    background;
  const fetch = recordingFetch(({ path }) => {
    if (path.endsWith('/mkdir')) {
      added = true;
      return ok({ uri: `${ROOT}/new` });
    }
    if (++reads === 2) return pending.promise;
    return ok([
      { uri: `${ROOT}/old` },
      ...(added ? [{ uri: `${ROOT}/new` }] : []),
    ]);
  });
  const state = await fixture(fetch);
  try {
    await act(async () => {
      background = state.tree.refresh();
    });
    await settle();
    await act(() =>
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
    await act(async () => pending.resolve(await ok([{ uri: `${ROOT}/old` }])));
    await background;
    await settle();
    assert.equal(
      state.tree.rows.some((row) => row.uri === `${ROOT}/new`),
      true,
    );
  } finally {
    pending.resolve(await ok([]));
    await background;
    await state.view.unmount();
    state.queryClient.clear();
  }
});

test('refreshing a cached empty listing keeps the active preview mounted', async () => {
  const { useFileBrowser } = await import(`${hooks}/file-browser.js`);
  const queryClient = new QueryClient();
  const pending = deferred();
  let reads = 0,
    browser,
    refresh;
  const fetch = recordingFetch(({ path }) => {
    if (path.endsWith('/ls')) return ++reads === 1 ? ok([]) : pending.promise;
    if (path.endsWith('/stat')) return ok({ isDir: false });
    return ok('keep preview');
  });
  function Probe() {
    browser = useFileBrowser({
      rootUri: ROOT,
      controlled: true,
      currentUri: `${ROOT}/hidden.md`,
    });
    return null;
  }
  const view = await mount(
    h(OpenVikingProvider, { fetch, queryClient }, h(Probe)),
  );
  try {
    assert.equal(browser.previewTabs?.[0]?.source?.content, 'keep preview');
    await act(async () => {
      refresh = queryClient.invalidateQueries();
    });
    await settle();
    assert.equal(browser.previewTabs?.[0]?.source?.content, 'keep preview');
  } finally {
    await act(async () => pending.resolve(await ok([])));
    await refresh;
    await view.unmount();
    queryClient.clear();
  }
});

test('restored host cache cannot collide with a different resolved account', async () => {
  const fetch = recordingFetch((call) =>
    ok([
      {
        uri: `${ROOT}/${new Headers(call.init.headers).get('X-OpenViking-Account')}`,
      },
    ]),
  );
  const first = await fixture(fetch, { accountId: 'A' });
  const cached = dehydrate(first.queryClient);
  await first.view.unmount();
  first.queryClient.clear();
  const queryClient = new QueryClient({
    defaultOptions: { queries: { staleTime: 5000 } },
  });
  hydrate(queryClient, cached);
  const next = await fixture(fetch, { queryClient, accountId: 'B' });
  try {
    assert.equal(next.tree.rows[0]?.uri, `${ROOT}/B`);
    assert.equal(fetch.calls.length, 2);
  } finally {
    await next.view.unmount();
    queryClient.clear();
  }
});
