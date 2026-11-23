import assert from 'node:assert/strict';
import test from 'node:test';
import { act, createElement as h } from 'react';
import { QueryClient } from '@tanstack/react-query';
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
const ls = '/api/v1/fs/ls';

test('explicit caller abort detaches its observer without cancelling another tree', async () => {
  const controller = new AbortController();
  const pending = deferred();
  const queryClient = new QueryClient();
  const trees = {};
  const fetch = recordingFetch(() => pending.promise);
  function Tree({ name, requestOptions }) {
    trees[name] = useFsTree({
      client: useOvClient(),
      rootUri: 'viking://resources',
      requestOptions,
    });
    return null;
  }
  const view = await mount(
    h(
      OpenVikingProvider,
      { fetch, queryClient },
      h(Tree, {
        name: 'cancelled',
        requestOptions: { signal: controller.signal },
      }),
      h(Tree, { name: 'active' }),
    ),
  );
  try {
    assert.equal(fetch.calls.length, 1);
    await act(() => controller.abort());
    assert.equal(fetch.calls[0].init.signal.aborted, false);
    await act(async () =>
      pending.resolve(
        await ok([{ uri: 'viking://resources/shared', isDir: true }]),
      ),
    );
    await settle();
    assert.equal(trees.active.rows[0].uri, 'viking://resources/shared');
  } finally {
    await view.unmount();
    queryClient.clear();
  }
});

test('queued abort never starts transport and does not consume a request slot', async () => {
  let client;
  const pending = deferred();
  const fetch = recordingFetch(() => pending.promise);
  const queryClient = new QueryClient();
  function Probe() {
    client = useOvClient();
    return null;
  }
  const view = await mount(
    h(
      OpenVikingProvider,
      { fetch, queryClient, requestConcurrency: { [ls]: 1 } },
      h(Probe),
    ),
  );
  try {
    const first = client.request({ method: 'GET', path: ls });
    const controller = new AbortController();
    const queued = client.request({
      method: 'GET',
      path: ls,
      options: { signal: controller.signal },
    });
    const rejected = assert.rejects(queued, { name: 'AbortError' });
    await settle();
    controller.abort();
    await rejected;
    assert.equal(fetch.calls.length, 1);
    pending.resolve(await ok([]));
    await first;
  } finally {
    await view.unmount();
    queryClient.clear();
  }
});

test('retry delays release the directory lane and actual retries rejoin it', async () => {
  let client;
  const blocker = deferred();
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: 1, retryDelay: 100 } },
  });
  const fetch = recordingFetch((call) =>
    call.uri === 'other'
      ? blocker.promise
      : fetch.calls.filter((item) => item.uri !== 'other').length === 1
        ? Promise.resolve(new Response('temporary', { status: 503 }))
        : ok([]),
  );
  function Probe() {
    client = useOvClient();
    useFsTree({ client, rootUri: 'viking://resources' });
    return null;
  }
  const view = await mount(
    h(
      OpenVikingProvider,
      { fetch, queryClient, requestConcurrency: { [ls]: 1 } },
      h(Probe),
    ),
  );
  try {
    const other = client.request({
      method: 'GET',
      path: ls,
      query: { uri: 'other' },
    });
    await settle();
    assert.equal(
      fetch.calls[1].uri,
      'other',
      'retry backoff must leave the lane available',
    );
    await act(() => new Promise((resolve) => setTimeout(resolve, 120)));
    assert.equal(
      fetch.calls.length,
      2,
      'retry queues behind the active request',
    );
    await act(async () => blocker.resolve(await ok([])));
    await other;
    await settle();
    assert.equal(fetch.calls.length, 3);
  } finally {
    await view.unmount();
    queryClient.clear();
  }
});

test('an already-aborted caller never starts directory transport on mount or later actions', async () => {
  const controller = new AbortController();
  controller.abort();
  const queryClient = new QueryClient();
  const fetch = recordingFetch(() => ok([]));
  let tree;
  function Probe() {
    tree = useFsTree({ client: useOvClient(), rootUri: 'viking://resources' });
    return null;
  }
  const view = await mount(
    h(
      OpenVikingProvider,
      { fetch, queryClient, signal: controller.signal },
      h(Probe),
    ),
  );
  try {
    assert.equal(fetch.calls.length, 0);
    await act(() => tree.open('viking://resources/docs'));
    await act(() => tree.refresh());
    assert.equal(fetch.calls.length, 0);
  } finally {
    await view.unmount();
    queryClient.clear();
  }
});
