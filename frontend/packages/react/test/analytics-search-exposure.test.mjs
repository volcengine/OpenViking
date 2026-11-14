import assert from 'node:assert/strict';
import test from 'node:test';
import { act, createElement as h } from 'react';
import { mount } from './helpers/module-dom.mjs';
import { exposureObserver } from './helpers/exposure-observer.mjs';

const hooks = process.env.OV_UI_TEST_HOOKS;
const { OpenVikingProvider } = await import(`${hooks}/provider.js`);
const { FileBrowserSearchResults } = await import(
  `${hooks}/browser-search-results.js`
);
const { resolveLabels } = await import(`${hooks}/browser-labels.js`);
const text = resolveLabels(undefined, 'en-US');

for (const hits of [
  [],
  [
    {
      uri: 'viking://resources/example.txt',
      contextType: 'resource',
      abstract: 'Example',
      score: 1,
      level: 0,
    },
  ],
]) {
  const event = `openviking_file_browser_search_${hits.length ? 'results' : 'empty'}_show`;
  test(`${event} stops during exit and restarts once on reopen`, async () => {
    const observer = exposureObserver();
    const events = [];
    const analytics = { onEvent: (value) => events.push(value.name) };
    const view = (open) =>
      h(
        OpenVikingProvider,
        { analytics },
        h(FileBrowserSearchResults, {
          open,
          mode: 'find',
          hits,
          listId: 'search-results',
          activeIndex: -1,
          query: 'example',
          onHighlight() {},
          onRetry() {},
          onSelectMode() {},
          onOpen() {},
          loading: false,
          error: null,
          searched: true,
          text,
        }),
      );
    const mounted = await mount(view(true));
    try {
      assert.equal(observer.active, 1);
      const target = mounted.container.querySelector(
        hits.length ? '[role="listbox"]' : '.ov-empty',
      );
      await mounted.render(view(false));
      assert.equal(target.isConnected, true);
      assert.equal(observer.active, 0);
      await act(async () => observer.expose());
      assert.deepEqual(events, []);
      await mounted.render(view(true));
      await act(async () => observer.expose());
      await mounted.render(view(true));
      await act(async () => observer.expose());
      assert.deepEqual(events, [event]);
      await mounted.render(view(false));
      await mounted.render(view(true));
      await act(async () => observer.expose());
      assert.deepEqual(events, [event, event]);
    } finally {
      await mounted.unmount();
      observer.restore();
    }
  });
}
