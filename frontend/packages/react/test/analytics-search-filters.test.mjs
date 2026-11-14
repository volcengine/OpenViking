import assert from 'node:assert/strict';
import test from 'node:test';
import { act, createElement as h } from 'react';
import { QueryClient } from '@tanstack/react-query';
import { mount, settle } from './helpers/module-dom.mjs';

const hooks = process.env.OV_UI_TEST_HOOKS;
const { OpenVikingProvider } = await import(`${hooks}/provider.js`);
const { useFsSearch } = await import(`${hooks}/fs-search.js`);
const rootUri = 'viking://resources/example';
const defaults = {
  scopes: [],
  levels: [],
  timeField: '',
  since: '',
  until: '',
  caseInsensitive: false,
};

for (const mode of ['find', 'search', 'grep', 'glob']) {
  const semantic = mode === 'find' || mode === 'search';
  for (const [name, override, expected] of [
    ['default root fallback', {}, false],
    ['explicit root selection', { scopes: [rootUri] }, true],
    ['level', { levels: [1] }, semantic],
    ['start date', { since: '2026-01-01' }, semantic],
    ['end date', { until: '2026-12-31' }, semantic],
    ['time field without dates', { timeField: 'created_at' }, false],
    ['case insensitive', { caseInsensitive: true }, mode === 'grep'],
  ]) {
    test(`${mode} ${name}: all operation stages report effective has_filters=${expected}`, async () => {
      const events = [];
      let fail = false;
      const client = {
        async request() {
          if (fail) throw new Error('Simulated search failure');
          return { resources: [], matches: [] };
        },
      };
      const filters = { ...defaults, ...override };
      let search;
      function Probe() {
        search = useFsSearch({
          client,
          mode,
          query: 'example',
          filters,
          rootUri,
        });
        return null;
      }
      const analytics = { onEvent: (event) => events.push(event) };
      const queryClient = new QueryClient({
        defaultOptions: { queries: { retry: false } },
      });
      const mounted = await mount(
        h(OpenVikingProvider, { analytics, queryClient }, h(Probe)),
      );
      try {
        fail = true;
        await act(() => search.retry());
        await settle();
        assert.deepEqual(
          events.map((event) => event.name),
          [
            'openviking_file_browser_search_submit',
            'openviking_file_browser_search_success',
            'openviking_file_browser_search_submit',
            'openviking_file_browser_search_fail',
          ],
        );
        assert.deepEqual(
          events.map((event) => event.params.has_filters),
          [expected, expected, expected, expected],
        );
        assert.ok(events.every((event) => event.params.mode === mode));
      } finally {
        await mounted.unmount();
        queryClient.clear();
      }
    });
  }
}
