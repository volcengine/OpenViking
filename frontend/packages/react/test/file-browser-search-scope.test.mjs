import assert from 'node:assert/strict';
import test from 'node:test';
import { act, createElement as h } from 'react';
import { FileBrowser } from '@openviking/react/modules/file-browser';
import { OpenVikingProvider } from '@openviking/react/client';
import { mount, ok, recordingFetch, settle } from './helpers/module-dom.mjs';
for (const name of ['HTMLIFrameElement', 'SVGElement'])
  globalThis[name] = window[name];
globalThis.getComputedStyle = window.getComputedStyle.bind(window);

test('choosing a subdirectory limits the search and clearing restores the root', async () => {
  const rootUri = 'viking://resources/project';
  const child = `${rootUri}/%E4%B8%AD%E6%96%87%E7%9B%AE%E5%BD%95`;
  const childDisplay = `${rootUri}/中文目录`;
  const encodedSlashChild = `${rootUri}/%E9%A1%B9%E7%9B%AE%2F%E5%BD%92%E6%A1%A3`;
  const encodedSlashDisplay = `${rootUri}/项目%2F归档`;
  const fetch = recordingFetch(({ path, uri }) => {
    if (path === '/api/v1/fs/ls' && uri === rootUri)
      return ok([
        { uri: child, isDir: true },
        { uri: encodedSlashChild, isDir: true },
      ]);
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
    const menu = () =>
      document.querySelector('.ov-file-browser-search__scope-menu');
    const searchModal = modal();
    const scopeTrigger = searchModal.querySelector(
      '.ov-file-browser-search__scope-trigger',
    );
    await click(scopeTrigger);
    const scopeButton = menu().querySelector('button');
    await act(async () => {
      scopeButton.focus();
      assert.equal(document.activeElement, scopeButton);
      scopeButton.dispatchEvent(
        new window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true }),
      );
    });
    assert.equal(menu(), null, 'Escape closes the scope picker');
    assert.equal(searchModal.querySelector('.ov-modal--exit'), null);
    await click(scopeTrigger);

    const root = () => menu().querySelector(`label[title="${rootUri}"]`);
    await click(
      root()
        .closest('.ov-file-browser-search__scope-row')
        .querySelector('button'),
    );
    await settle();
    assert.ok(menu().querySelector(`[title="${encodedSlashDisplay}"]`));
    assert.equal(menu().querySelector(`[title="${rootUri}/项目/归档"]`), null);
    const childInput = () =>
      menu().querySelector(`[title="${childDisplay}"] input`);
    await click(childInput());
    assert.ok(root().classList.contains('ov-checkbox--indeterminate'));
    assert.deepEqual(targets(), [rootUri], 'draft changes do not run a search');
    const actions = () =>
      menu().querySelectorAll('.ov-file-browser-search__scope-actions button');
    await click(actions()[0]);
    assert.equal(menu(), null, 'cancel closes the draft');
    await click(scopeTrigger);
    await click(
      root()
        .closest('.ov-file-browser-search__scope-row')
        .querySelector('button'),
    );
    await settle();
    assert.equal(
      childInput().checked,
      false,
      'cancel preserves the applied selection',
    );
    await click(childInput());
    await click(actions()[1]);
    await settle();
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
      modal().querySelector('.ov-file-browser-search__options input'),
    );
    await settle();
    const childOption = modal().querySelector(
      `.ov-file-browser-search__options [title="${childDisplay}"]`,
    );
    assert.equal(childOption === null, true);
    assert.deepEqual(targets().at(-1), [rootUri]);
    await click(
      modal().querySelector('.ov-file-browser-search__advanced-clear'),
    );
    assert.equal(filterCount(), null);
    assert.equal(filterToggle().getAttribute('aria-label'), 'Advanced filters');
    assert.deepEqual(
      targets(),
      [rootUri, [child], [rootUri]],
      'restoring the root reuses its fresh cached search',
    );
  } finally {
    await view.unmount();
  }
});
