import assert from 'node:assert/strict';
import test from 'node:test';
import { createElement as h } from 'react';
import { QueryClient } from '@tanstack/react-query';
import { mount, ok, recordingFetch } from './helpers/module-dom.mjs';
const { OpenVikingProvider, useOvClient } = await import(
  `${process.env.OV_UI_TEST_HOOKS}/provider.js`
);
const { useOvQueryClient } = await import(
  `${process.env.OV_UI_TEST_HOOKS}/query-runtime.js`
);

test('QueryClient defaults, inheritance, external ownership and locale stability', async () => {
  const clients = {};
  const external = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: 42 } },
  });
  external.setQueryData(['host'], 'retained');
  function Probe({ name }) {
    clients[name] = useOvQueryClient();
    return null;
  }
  const element = (locale, queryClient) =>
    h(
      OpenVikingProvider,
      { locale, queryClient },
      h(Probe, { name: 'root' }),
      h(OpenVikingProvider, null, h(Probe, { name: 'inherited' })),
      h(
        OpenVikingProvider,
        { queryClient: external },
        h(Probe, { name: 'external' }),
      ),
    );
  const view = await mount(element('en-US'));
  const internal = clients.root;
  try {
    assert.equal(internal, clients.inherited);
    assert.deepEqual(internal.getDefaultOptions().queries, {
      retry: 1,
      staleTime: 5000,
    });
    await view.render(element('zh-CN'));
    assert.equal(clients.root, internal);
    assert.equal(clients.external, external);
    await view.render(element('en-US', external));
    assert.equal(clients.inherited, external);
    assert.deepEqual(external.getDefaultOptions().queries, {
      retry: false,
      staleTime: 42,
    });
  } finally {
    await view.unmount();
  }
  assert.equal(external.getQueryData(['host']), 'retained');
  external.clear();
  internal.clear();
});

test('per-interface queues merge through nested Providers and do not block other paths', async () => {
  let client, query;
  const pending = [];
  const active = new Map();
  const maxima = new Map();
  const fetch = recordingFetch((call) => {
    active.set(call.path, (active.get(call.path) ?? 0) + 1);
    maxima.set(
      call.path,
      Math.max(maxima.get(call.path) ?? 0, active.get(call.path)),
    );
    return new Promise((resolve) =>
      pending.push(async () => {
        active.set(call.path, active.get(call.path) - 1);
        resolve(await ok([]));
      }),
    );
  });
  function Probe() {
    client = useOvClient();
    query = useOvQueryClient();
    return null;
  }
  const view = await mount(
    h(
      OpenVikingProvider,
      { fetch },
      h(
        OpenVikingProvider,
        { requestConcurrency: { '/api/v1/fs/tree': 2 } },
        h(Probe),
      ),
    ),
  );
  try {
    const requests = [
      '/api/v1/fs/ls',
      '/api/v1/fs/tree',
      '/api/v1/fs/stat',
    ].flatMap((path) =>
      Array.from({ length: 5 }, (_, offset) =>
        client.request({ method: 'GET', path, query: { offset } }),
      ),
    );
    await new Promise((resolve) => setTimeout(resolve, 5));
    assert.equal(active.get('/api/v1/fs/ls'), 3);
    assert.equal(active.get('/api/v1/fs/tree'), 2);
    assert.equal(active.get('/api/v1/fs/stat'), 5);
    while (pending.length) {
      await pending.shift()();
      await new Promise((resolve) => setTimeout(resolve, 0));
    }
    await Promise.all(requests);
    assert.equal(maxima.get('/api/v1/fs/ls'), 3);
    assert.equal(maxima.get('/api/v1/fs/tree'), 2);
  } finally {
    await view.unmount();
    query.clear();
  }
});
