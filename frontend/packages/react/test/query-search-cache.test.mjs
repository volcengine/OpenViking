import assert from 'node:assert/strict';
import test from 'node:test';
import { act, createElement as h } from 'react';
import { QueryClient } from '@tanstack/react-query';
import { mount, ok, recordingFetch, settle } from './helpers/module-dom.mjs';
const hooks = process.env.OV_UI_TEST_HOOKS;
const { OpenVikingProvider, useOvClient } = await import(
  `${hooks}/provider.js`
);
const { useFsSearch } = await import(`${hooks}/fs-search.js`);
const { FileBrowserSearchResults } = await import(
  `${hooks}/browser-search-results.js`
);
const { resolveLabels } = await import(`${hooks}/browser-labels.js`);
const filters = {
  scopes: [],
  levels: [],
  timeField: '',
  since: '',
  until: '',
  caseInsensitive: false,
};

test('failed background search refresh retains cached results alongside retry feedback', async () => {
  let failed = false;
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: 5000 } },
  });
  const fetch = recordingFetch(() =>
    failed
      ? Promise.resolve(
          Response.json(
            {
              status: 'error',
              error: { code: 'FAILED', message: 'Refresh failed' },
            },
            { status: 503 },
          ),
        )
      : ok({ resources: [{ uri: 'viking://resources/cached.md' }] }),
  );
  function Probe() {
    const state = useFsSearch({
      client: useOvClient(),
      mode: 'find',
      query: 'cached',
      rootUri: 'viking://resources',
      filters,
    });
    return h(FileBrowserSearchResults, {
      open: true,
      searched: true,
      mode: 'find',
      query: 'cached',
      listId: 'results',
      activeIndex: 0,
      hits: state.result?.resources ?? [],
      loading: state.loading,
      error: state.error,
      text: resolveLabels(undefined, 'en-US'),
      onHighlight() {},
      onSelectMode() {},
      onOpen() {},
      onRetry: state.retry,
    });
  }
  const view = await mount(
    h(OpenVikingProvider, { queryClient, fetch }, h(Probe)),
  );
  try {
    assert.equal(view.container.querySelectorAll('[role="option"]').length, 1);
    failed = true;
    await act(() => queryClient.invalidateQueries());
    await settle();
    assert.match(
      view.container.querySelector('[role="alert"]').textContent,
      /Refresh failed/,
    );
    assert.equal(view.container.querySelectorAll('[role="option"]').length, 1);
    failed = false;
    await act(() =>
      [...view.container.querySelectorAll('button')]
        .find((button) => button.textContent === 'Retry')
        .click(),
    );
    await settle();
    assert.equal(view.container.querySelector('[role="alert"]'), null);
    assert.equal(view.container.querySelectorAll('[role="option"]').length, 1);
  } finally {
    await view.unmount();
    queryClient.clear();
  }
});

for (const mode of ['tree', 'search']) {
  test(`${mode} Retry directly recovers a failed identity source without a host rerender`, async () => {
    const { useFsTree } = await import(`${hooks}/fs-tree.js`);
    let broken = true,
      state;
    const queryClient = new QueryClient();
    const fetch = recordingFetch(({ path }) =>
      path.endsWith('/ls')
        ? ok([{ uri: 'viking://resources/recovered' }])
        : ok({ resources: [{ uri: 'viking://resources/recovered' }] }),
    );
    function Tree() {
      state = useFsTree({
        client: useOvClient(),
        rootUri: 'viking://resources',
      });
      return null;
    }
    function Search() {
      state = useFsSearch({
        client: useOvClient(),
        mode: 'find',
        query: 'recovered',
        rootUri: 'viking://resources',
        filters,
      });
      return null;
    }
    const view = await mount(
      h(
        OpenVikingProvider,
        {
          queryClient,
          fetch,
          apiKey: async () => {
            if (broken) throw new Error('Identity unavailable');
            return 'recovered-credential';
          },
        },
        h(mode === 'tree' ? Tree : Search),
      ),
    );
    try {
      assert.match(state.error.message, /Identity unavailable/);
      assert.equal(fetch.calls.length, 0);
      broken = false;
      await act(() => state.retry('viking://resources'));
      await settle();
      assert.equal(state.error, null);
      assert.equal(
        (state.rows ?? state.result.resources)[0].uri,
        'viking://resources/recovered',
      );
      assert.equal(fetch.calls.length, 1);
    } finally {
      await view.unmount();
      queryClient.clear();
    }
  });
}
