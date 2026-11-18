import assert from 'node:assert/strict';
import test from 'node:test';
import { act, createElement as h } from 'react';
import { VikingBotSessionActions } from '@openviking/react/components/viking-bot';
import { FileBrowser } from '@openviking/react/modules/file-browser';
import { OpenVikingProvider } from '@openviking/react/client';
import { mount, ok, recordingFetch, settle } from './helpers/module-dom.mjs';

const key = (node, value) =>
  act(async () =>
    node.dispatchEvent(
      new window.KeyboardEvent('keydown', {
        key: value,
        bubbles: true,
        cancelable: true,
      }),
    ),
  );
const click = (node) => act(async () => node.click());
const menu = () => document.querySelector('.ov-select__popover');

test('history retains loading, errors and pagination while selection closes once', async () => {
  let opens = 0;
  let pages = 0;
  const selected = [];
  const sessions = {
    items: [{ id: 'first', title: 'First', updatedAt: '2026-11-09T03:04:00Z' }],
    hasMore: true,
    onOpenHistory: () => opens++,
    onLoadMore: () => pages++,
    onSelect: (id) => selected.push(id),
    onNew() {},
  };
  const render = (extra = {}) =>
    h(VikingBotSessionActions, { sessions: { ...sessions, ...extra } });
  const view = await mount(render());
  try {
    const trigger = view.container.querySelector('[aria-haspopup="listbox"]');
    await key(trigger, 'ArrowDown');
    assert.equal(opens, 1);
    assert.match(document.activeElement.textContent, /First/);
    const loadMore = [...menu().querySelectorAll('button')].find(
      (n) => n.textContent === 'Load more',
    );
    await click(loadMore);
    assert.equal(pages, 1);
    assert.ok(menu(), 'pagination keeps the menu open');
    await view.render(render({ isLoading: true }));
    assert.ok(menu().querySelector('[role="status"]'));
    assert.ok([...menu().querySelectorAll('button')].some((n) => n.disabled));
    await view.render(render({ error: 'History unavailable' }));
    assert.equal(
      menu().querySelector('[role="alert"]').textContent,
      'History unavailable',
    );
    await view.render(
      render({ items: [...sessions.items, { id: 'second', title: 'Second' }] }),
    );
    assert.equal(opens, 1, 'rerenders do not reload history');
    await key(menu().querySelector('button'), 'ArrowDown');
    assert.equal(document.activeElement.textContent, 'Second');
    await click(document.activeElement);
    assert.deepEqual(selected, ['second']);
    assert.equal(menu(), null);
    assert.equal(document.activeElement, trigger);
  } finally {
    await view.unmount();
  }
});

test('search mode uses the menu inside the dialog without clearing the query or closing the dialog', async () => {
  const uri = 'viking://resources/profile.md';
  const fetch = recordingFetch((call) => {
    if (call.path === '/api/v1/search/find')
      return ok({ resources: [{ uri }] });
    if (call.path === '/api/v1/search/grep')
      return ok({ matches: [{ uri, line: 1, content: 'profile' }] });
    return ok([]);
  });
  const view = await mount(
    h(
      OpenVikingProvider,
      { fetch },
      h(FileBrowser, { rootUri: 'viking://resources' }),
    ),
  );
  try {
    await click(
      view.container.querySelector('.ov-file-browser__search-trigger'),
    );
    await settle();
    const dialog = document.querySelector('.ov-file-browser-search');
    const input = dialog.querySelector('input[role="combobox"]');
    await act(async () => {
      Object.getOwnPropertyDescriptor(
        window.HTMLInputElement.prototype,
        'value',
      ).set.call(input, 'profile');
      input.dispatchEvent(new window.Event('input', { bubbles: true }));
    });
    const trigger = () =>
      dialog.querySelector('.ov-file-browser-search__switch');
    await act(async () => new Promise((resolve) => setTimeout(resolve, 450)));
    await settle();
    await key(trigger(), 'ArrowDown');
    assert.equal(menu().querySelectorAll('[role="option"]').length, 4);
    assert.equal(
      menu()
        .querySelector('[aria-selected="true"]')
        .textContent.startsWith('find'),
      true,
    );
    await key(document.activeElement, 'Escape');
    assert.equal(menu(), null);
    assert.ok(dialog.isConnected, 'Escape closes the menu before the modal');
    await key(trigger(), 'ArrowDown');
    const grep = [...menu().querySelectorAll('button')].find((n) =>
      n.textContent.startsWith('grep'),
    );
    await click(grep);
    assert.equal(menu(), null);
    assert.ok(dialog.isConnected);
    assert.equal(document.activeElement, input);
    await settle();
    assert.equal(
      dialog.querySelector('.ov-file-browser-search__mode-chip').textContent,
      'grep',
    );
    assert.equal(input.value, 'profile');
  } finally {
    await view.unmount();
  }
});
