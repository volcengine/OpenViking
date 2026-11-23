import test from 'node:test';
import assert from 'node:assert/strict';
import { act, h, mount } from './helpers/monitoring-dom.mjs';
import { runtime as r, ok } from './helpers/monitoring.mjs';

test('switching a shared search to fresh cache never reuses the previous input trace', async () => {
  const events = [],
    states = new Map();
  const queryClient = r.createQueryClient();
  queryClient.setDefaultOptions({
    queries: { retry: false, staleTime: 60_000 },
  });
  const filters = { scopes: [], levels: [] };
  let resolveRequest,
    calls = 0,
    priorData;
  const fetch = () => {
    calls++;
    return new Promise((resolve) => {
      resolveRequest = resolve;
    });
  };
  function Results({ crash, total }) {
    if (crash) throw new Error('cached search render failed');
    return h('p', null, total);
  }
  const Search = r.withMonitoring(({ id, query, crash }) => {
    const state = r.useFsSearch({
      client: r.useOvClient(),
      mode: 'find',
      query,
      rootUri: 'viking://resources',
      filters,
    });
    states.set(id, state);
    return h(
      r.MonitoringFlow,
      {
        operation: state.monitoring,
        unobservedInput: !!state.result && !state.monitoring,
      },
      h(
        r.ErrorBoundary,
        { monitoringRegion: 'search', message: 'render failed', onError() {} },
        h(Results, { crash, total: state.result?.total }),
      ),
    );
  }, 'FileBrowser');
  const monitoring = { onEvent: (event) => events.push(event) };
  const view = (query = 'alpha', crash = false) =>
    h(r.OpenVikingProvider, { queryClient, fetch, monitoring }, [
      h(Search, { key: 'first', id: 'first', query: 'alpha' }),
      h(Search, { key: 'second', id: 'second', query, crash, priorData }),
    ]);
  const until = async (check) => {
    for (let i = 0; i < 100 && !check(); i++)
      await act(() => new Promise((resolve) => setTimeout(resolve, 10)));
    assert.ok(check(), 'search did not settle');
  };
  const app = await mount(view());
  try {
    await until(
      () =>
        resolveRequest &&
        queryClient.getQueryCache().getAll()[0]?.getObserversCount() === 2,
    );
    await act(() =>
      resolveRequest(ok({ memories: [], resources: [], skills: [] })),
    );
    await until(
      () => states.get('second')?.monitoring?.span.node.status === 'ready',
    );
    const alphaTrace = states.get('second').monitoring.span.trace.traceId;
    const alpha = queryClient.getQueryCache().getAll()[0];
    priorData = states.get('second').result;
    const betaKey = [
      ...alpha.queryKey.slice(0, -1),
      { ...alpha.queryKey.at(-1), query: 'beta' },
    ];
    queryClient.setQueryData(betaKey, { ...alpha.state.data, total: 123 });
    const readyCount = events.filter(
      (event) => event.name === 'file_browser_search_find_ready',
    ).length;
    await app.render(view('beta'));
    await until(() => states.get('second')?.result?.total === 123);
    assert.equal(calls, 1, 'fresh cache must not trigger another request');
    assert.ok(
      !states.get('second').monitoring,
      'beta must not inherit alpha workflow',
    );
    assert.equal(
      events.filter((event) => event.name === 'file_browser_search_find_ready')
        .length,
      readyCount,
    );
    await app.render(view('beta', true));
    const failure = events.find(
      (event) => event.name === 'file_browser_render_error',
    );
    assert.ok(failure);
    assert.notEqual(failure.trace.traceId, alphaTrace);
    assert.equal(failure.params.traceSnapshot.coverage, 'partial');
    assert.ok(failure.params.traceSnapshot.gaps.includes('shared-query-input'));
    assert.equal(
      failure.params.traceSnapshot.links.some(
        (link) => link.traceId === alphaTrace,
      ),
      false,
    );
  } finally {
    await app.dispose();
    queryClient.clear();
  }
});
