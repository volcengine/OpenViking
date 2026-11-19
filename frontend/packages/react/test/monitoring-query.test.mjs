import test from 'node:test';
import assert from 'node:assert/strict';
import { useState } from 'react';
import { act, h, mount } from './helpers/monitoring-dom.mjs';
import { runtime as r } from './helpers/monitoring.mjs';
async function until(check) {
  for (let i = 0; i < 100 && !check(); i++)
    await act(() => new Promise((resolve) => setTimeout(resolve, 10)));
  assert.ok(check(), 'query did not settle');
}
function treeHarness() {
  let state;
  const Tree = r.withMonitoring(() => {
    state = r.useFsTree({
      client: r.useOvClient(),
      rootUri: 'viking://resources',
    });
    return h(
      'p',
      null,
      state.error ? 'failed' : state.isReady ? 'ready' : 'loading',
    );
  }, 'FileBrowser');
  return { Tree, state: () => state };
}
test('managed query clients preserve transport and async credentials without replay or cache metadata', async () => {
  const { Tree, state } = treeHarness(),
    events = [],
    requests = [];
  const queryClient = r.createQueryClient();
  const apiKey = async () => 'test-secret';
  const fetch = async (url, init) => {
    requests.push({ url, key: new Headers(init.headers).get('x-api-key') });
    return Response.json({ status: 'ok', result: [] });
  };
  const config = {
    onEvent(event) {
      events.push(event);
    },
  };
  const view = (enabled) =>
    h(
      r.OpenVikingProvider,
      {
        queryClient,
        apiKey,
        fetch,
        baseURL: 'https://example.test',
        monitoring: enabled ? config : false,
      },
      h(Tree),
    );
  const app = await mount(view(false));
  try {
    await until(() => state()?.isReady);
    assert.equal(requests.length, 1);
    assert.equal(requests[0].key, 'test-secret');
    assert.equal(events.length, 0);
    await app.render(view(true));
    assert.equal(requests.length, 1);
    assert.equal(events.length, 0);
    await act(() => state().refresh());
    await until(() =>
      events.some(
        (event) => event.name === 'file_browser_directory_load_ready',
      ),
    );
    assert.equal(requests.length, 2);
    assert.equal(requests[1].key, 'test-secret');
    const ready = events.find(
      (event) => event.name === 'file_browser_directory_load_ready',
    );
    assert.equal(ready.source.module.name, 'FileBrowser');
    assert.equal(
      events.filter((event) => event.name === 'client_request_success').length,
      1,
    );
    const cached = queryClient.getQueryCache().getAll()[0].state.data;
    assert.ok(cached.pages.length);
    assert.equal('monitoring' in cached.pages[0], false);
    assert.equal(JSON.stringify(events).includes('test-secret'), false);
  } finally {
    await app.dispose();
    queryClient.clear();
  }
});
test('query identity failures report once despite receiver renders and retain explicit retry lineage', async () => {
  const { Tree, state } = treeHarness(),
    events = [];
  const failure = new Error('private identity error');
  const apiKey = async () => {
    throw failure;
  };
  const config = {
    onEvent(event) {
      events.push(event);
      rerender?.((value) => value + 1);
    },
  };
  let rerender;
  function Host() {
    const [, setCount] = useState(0);
    rerender = setCount;
    return h(r.OpenVikingProvider, { apiKey, monitoring: config }, h(Tree));
  }
  const app = await mount(h(Host));
  try {
    await until(() => state()?.error);
    assert.equal(events.length, 1);
    assert.equal(events[0].name, 'file_browser_directory_load_fail');
    assert.equal(events[0].params.errorKind, 'configuration');
    await act(() => state().retry('viking://resources'));
    await until(() => events.length === 2);
    assert.equal(events[1].trace.trigger, 'retry');
    assert.notEqual(events[0].params.incidentId, events[1].params.incidentId);
    assert.equal(events[1].params.traceSnapshot.links[0].relation, 'retry-of');
    assert.equal(
      JSON.stringify(events).includes('private identity error'),
      false,
    );
  } finally {
    await app.dispose();
  }
});

test('a shared directory failure still identifies its remaining mounted consumer', async () => {
  const events = [],
    states = new Map();
  const queryClient = r.createQueryClient();
  queryClient.setDefaultOptions({ queries: { retry: false } });
  let rejectRequest,
    calls = 0;
  const fetch = () => {
    calls++;
    return new Promise((_, reject) => {
      rejectRequest = reject;
    });
  };
  const Tree = r.withMonitoring(({ id }) => {
    const state = r.useFsTree({
      client: r.useOvClient(),
      rootUri: 'viking://resources',
    });
    states.set(id, state);
    return h('p', null, `${id}: ${state.error ? 'failed' : 'loading'}`);
  }, 'FileBrowser');
  const config = {
    onEvent(event) {
      events.push(event);
    },
  };
  const view = (first) =>
    h(r.OpenVikingProvider, { fetch, queryClient, monitoring: config }, [
      first && h(Tree, { id: 'first', key: 'first' }),
      h(Tree, { id: 'remaining', key: 'remaining' }),
    ]);
  const app = await mount(view(true));
  try {
    await until(
      () =>
        rejectRequest &&
        queryClient.getQueryCache().getAll()[0]?.getObserversCount() === 2,
    );
    assert.equal(calls, 1);
    await app.render(view(false));
    await act(() => rejectRequest(new Error('shared transport failure')));
    await until(() => states.get('remaining')?.error);
    const failure = events.find(
      (event) => event.name === 'file_browser_directory_load_fail',
    );
    assert.ok(failure, 'the active consumer lost its shared query failure');
    assert.equal(failure.source.module.name, 'FileBrowser');
    assert.ok(failure.params.traceSnapshot.gaps.includes('shared-query-input'));
  } finally {
    await app.dispose();
    queryClient.clear();
  }
});
