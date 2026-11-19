import assert from 'node:assert/strict';
import test from 'node:test';
import { act, createElement as h } from 'react';
import { FileBrowser } from '@openviking/react/modules/file-browser';
import { OpenVikingProvider } from '@openviking/react/client';
import {
  deferred,
  mount,
  ok,
  recordingFetch,
  settle,
} from './helpers/module-dom.mjs';
for (const name of ['HTMLIFrameElement', 'SVGElement'])
  globalThis[name] = window[name];
globalThis.getComputedStyle = window.getComputedStyle.bind(window);

test('switching accounts cancels the previous search and rejects its late result', async () => {
  const old = deferred();
  const searches = [];
  const fetch = recordingFetch((call) => {
    if (call.path !== '/api/v1/search/find') return ok([]);
    searches.push(call);
    return new Headers(call.init.headers).get('X-OpenViking-Account') === 'A'
      ? old.promise
      : ok({ memories: [{ uri: 'viking://b.md' }] });
  });
  const render = (accountId) =>
    h(
      OpenVikingProvider,
      { baseURL: 'https://ov.test', fetch, accountId },
      h(FileBrowser),
    );
  const view = await mount(render('A'));
  try {
    await act(async () =>
      view.container.querySelector('.ov-file-browser__search-trigger').click(),
    );
    await settle();
    const modal = () => document.querySelector('.ov-file-browser-search');
    await act(async () => {
      const input = modal().querySelector('.ov-file-browser-search__input');
      Object.getOwnPropertyDescriptor(
        window.HTMLInputElement.prototype,
        'value',
      ).set.call(input, 'secret');
      input.dispatchEvent(new window.Event('input', { bubbles: true }));
    });
    await act(async () => new Promise((resolve) => setTimeout(resolve, 450)));
    await settle();

    await view.render(render('B'));
    await settle();
    assert.equal(searches[0].init.signal.aborted, true);

    await act(async () =>
      old.resolve(await ok({ memories: [{ uri: 'viking://a-secret.md' }] })),
    );
    await settle();
    assert.match(modal().textContent, /viking:\/\/b\.md/);
    assert.doesNotMatch(modal().textContent, /a-secret/);
    assert.equal(searches.length, 2);
  } finally {
    await view.unmount();
  }
});

test('choosing a subdirectory limits the search and clearing restores the root', async () => {
  const rootUri = 'viking://resources/project';
  const child = `${rootUri}/%E4%B8%AD%E6%96%87%E7%9B%AE%E5%BD%95`;
  const childDisplay = `${rootUri}/中文目录`;
  const fetch = recordingFetch(({ path, uri }) => {
    if (path === '/api/v1/fs/ls' && uri === rootUri)
      return ok([{ uri: child, isDir: true }]);
    if (path === '/api/v1/search/find') return ok({ resources: [] });
    return ok([]);
  });
  const view = await mount(
    h(
      OpenVikingProvider,
      { baseURL: 'https://ov.test', fetch },
      h(FileBrowser, {
        rootUri,
        labels: {
          searchAdvancedLabelWithCount: (label, count) =>
            `${label} — ${count} selected`,
        },
      }),
    ),
  );
  const modal = () => document.querySelector('.ov-file-browser-search');
  const click = (element) => act(async () => element.click());
  const targets = () =>
    fetch.calls
      .filter((call) => call.path === '/api/v1/search/find')
      .map((call) => call.body.target_uri);
  try {
    await click(
      view.container.querySelector('.ov-file-browser__search-trigger'),
    );
    const input = modal().querySelector('.ov-file-browser-search__input');
    await act(async () => {
      Object.getOwnPropertyDescriptor(
        window.HTMLInputElement.prototype,
        'value',
      ).set.call(input, 'notes');
      input.dispatchEvent(new window.Event('input', { bubbles: true }));
    });
    await act(async () => new Promise((resolve) => setTimeout(resolve, 450)));
    await click(
      modal().querySelector('.ov-file-browser-search__advanced-toggle'),
    );
    await click(
      modal().querySelector('.ov-file-browser-search__scope-trigger'),
    );
    const menu = () =>
      modal().querySelector('.ov-file-browser-search__scope-menu');
    await click(menu().querySelector(`[title="${rootUri}"]`));
    await click(menu().querySelector(`[title="${childDisplay}"]`));
    await click(
      menu().querySelector(`[title="${childDisplay}"][aria-pressed]`),
    );
    const selectedScope = modal().querySelector(
      `.ov-file-browser-search__options [title="${childDisplay}"]`,
    );
    assert.equal(selectedScope?.textContent, childDisplay);
    assert.equal(selectedScope?.getAttribute('title'), childDisplay);
    assert.deepEqual(targets().at(-1), [child]);
    const filterToggle = () =>
      modal().querySelector('.ov-file-browser-search__advanced-toggle');
    const filterCount = () =>
      filterToggle().querySelector('.ov-file-browser-search__filter-count');
    assert.equal(filterCount().textContent, '1');
    assert.equal(
      filterToggle().getAttribute('aria-label'),
      'Advanced filters — 1 selected',
    );
    await click(
      modal().querySelector('.ov-file-browser-search__advanced-close'),
    );
    assert.equal(filterToggle().getAttribute('aria-expanded'), 'false');
    assert.equal(filterCount().textContent, '1');
    assert.deepEqual(targets().at(-1), [child]);
    await click(filterToggle());
    await click(
      modal().querySelector('.ov-file-browser-search__advanced-clear'),
    );
    assert.equal(filterCount(), null);
    assert.equal(filterToggle().getAttribute('aria-label'), 'Advanced filters');
    assert.deepEqual(
      targets(),
      [rootUri, [child]],
      'restoring the root reuses its fresh cached search',
    );
  } finally {
    await view.unmount();
  }
});
