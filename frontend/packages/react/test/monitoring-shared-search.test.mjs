import test from 'node:test';
import assert from 'node:assert/strict';
import { act, h, mount } from './helpers/monitoring-dom.mjs';
import { runtime as r } from './helpers/monitoring.mjs';

test('a shared search keeps reporting for its remaining subscribed component', async () => {
  const events = [],
    states = new Map();
  const queryClient = r.createQueryClient();
  queryClient.setDefaultOptions({ queries: { retry: false } });
  const filters = { scopes: [], levels: [] };
  let rejectRequest,
    calls = 0;
  const fetch = () => {
    calls++;
    return new Promise((_, reject) => {
      rejectRequest = reject;
    });
  };
  const Search = r.withMonitoring(({ id }) => {
    const state = r.useFsSearch({
      client: r.useOvClient(),
      mode: 'find',
      query: 'readme',
      rootUri: 'viking://resources',
      filters,
    });
    states.set(id, state);
    return h('p', null, state.error ? 'failed' : 'loading');
  }, 'FileBrowser');
  const config = {
    onEvent(event) {
      events.push(event);
    },
  };
  const view = (first) =>
    h(r.OpenVikingProvider, { queryClient, fetch, monitoring: config }, [
      first && h(Search, { id: 'first', key: 'first' }),
      h(Search, { id: 'remaining', key: 'remaining' }),
    ]);
  const until = async (check) => {
    for (let i = 0; i < 100 && !check(); i++)
      await act(() => new Promise((resolve) => setTimeout(resolve, 10)));
    assert.ok(check(), 'search did not settle');
  };
  const app = await mount(view(true));
  try {
    await until(
      () =>
        rejectRequest &&
        queryClient.getQueryCache().getAll()[0]?.getObserversCount() === 2,
    );
    assert.equal(calls, 1);
    await app.render(view(false));
    await act(() =>
      rejectRequest(new Error('shared search transport failure')),
    );
    await until(() => states.get('remaining')?.error);
    const failure = events.find(
      (event) => event.name === 'file_browser_search_find_fail',
    );
    assert.ok(failure);
    assert.equal(failure.source.module.name, 'FileBrowser');
    assert.ok(failure.params.traceSnapshot.gaps.includes('shared-query-input'));
  } finally {
    await app.dispose();
    queryClient.clear();
  }
});
