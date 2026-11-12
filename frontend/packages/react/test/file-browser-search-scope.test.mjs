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

test('the built-in dialog cancels the previous account search and rejects its late result', async () => {
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
      view.container.querySelector('.ov-file-tree__search').click(),
    );
    await settle();
    const modal = () => document.querySelector('.ov-file-browser-search');
    await act(async () => {
      const input = modal().querySelector('input');
      Object.getOwnPropertyDescriptor(
        window.HTMLInputElement.prototype,
        'value',
      ).set.call(input, 'secret');
      input.dispatchEvent(new window.Event('input', { bubbles: true }));
    });
    const waitForQuery = () =>
      act(() => new Promise((resolve) => setTimeout(resolve, 350)));
    await waitForQuery();
    await view.render(render('B'));
    await waitForQuery();
    assert.equal(searches[0].init.signal.aborted, true);
    await act(async () =>
      old.resolve(await ok({ memories: [{ uri: 'viking://a-secret.md' }] })),
    );
    await settle();
    assert.equal(
      modal().querySelector('[role="option"]').getAttribute('title'),
      'viking://b.md',
    );
    assert.doesNotMatch(modal().textContent, /a-secret/);
    assert.equal(searches.length, 2);
  } finally {
    await view.unmount();
  }
});

test('moving the highlighted result aborts the old snippet and keeps the new preview', async () => {
  const first = 'viking://resources/one.md';
  const second = 'viking://resources/two.md';
  const old = deferred();
  const fetch = recordingFetch((call) => {
    if (call.path === '/api/v1/search/find')
      return ok({ resources: [{ uri: first }, { uri: second }] });
    if (call.path === '/api/v1/content/read')
      return call.uri === first ? old.promise : ok('Second file body');
    return ok([]);
  });
  const view = await mount(
    h(
      OpenVikingProvider,
      { baseURL: 'https://ov.test', fetch },
      h(FileBrowser),
    ),
  );
  const wait = (ms) =>
    act(() => new Promise((resolve) => setTimeout(resolve, ms)));
  const modal = () => document.querySelector('.ov-file-browser-search');
  try {
    await act(async () =>
      view.container.querySelector('.ov-file-tree__search').click(),
    );
    const field = modal().querySelector('input');
    await act(async () => {
      Object.getOwnPropertyDescriptor(
        window.HTMLInputElement.prototype,
        'value',
      ).set.call(field, 'file');
      field.dispatchEvent(new window.Event('input', { bubbles: true }));
    });
    await wait(350);
    await wait(140);
    const previous = fetch.calls.find(
      (call) => call.path === '/api/v1/content/read' && call.uri === first,
    );
    assert.equal(previous.query.limit, '20');
    await act(async () =>
      field.dispatchEvent(
        new window.KeyboardEvent('keydown', {
          bubbles: true,
          cancelable: true,
          key: 'ArrowDown',
        }),
      ),
    );
    assert.equal(previous.init.signal.aborted, true);
    await wait(150);
    assert.match(
      modal().querySelector('aside').textContent,
      /Second file body/,
    );
    await act(async () => old.resolve(await ok('Stale first body')));
    assert.doesNotMatch(
      modal().querySelector('aside').textContent,
      /Stale first body/,
    );
  } finally {
    await view.unmount();
  }
});

test('composing Chinese input waits for confirmation before scheduling search', async () => {
  const path = '/api/v1/search/find';
  const fetch = recordingFetch((call) =>
    call.path === path ? ok({ resources: [] }) : ok([]),
  );
  const view = await mount(
    h(
      OpenVikingProvider,
      { baseURL: 'https://ov.test', fetch },
      h(FileBrowser),
    ),
  );
  const pause = () =>
    act(() => new Promise((resolve) => setTimeout(resolve, 350)));
  const queries = () =>
    fetch.calls
      .filter((call) => call.path === path)
      .map((call) => call.body.query);
  try {
    await act(async () =>
      view.container.querySelector('.ov-file-tree__search').click(),
    );
    const input = document.querySelector('.ov-file-browser-search input');
    const compose = (name, data = '') =>
      input.dispatchEvent(
        new window.CompositionEvent(name, { data, bubbles: true }),
      );
    const setInput = (value, isComposing) => {
      Object.getOwnPropertyDescriptor(
        window.HTMLInputElement.prototype,
        'value',
      ).set.call(input, value);
      input.dispatchEvent(
        new window.InputEvent('input', { bubbles: true, isComposing }),
      );
    };
    await act(async () => {
      compose('compositionstart');
      setInput('wendang', true);
    });
    await pause();
    assert.deepEqual(queries(), []);
    await act(async () => {
      setInput('文档', true);
      compose('compositionend', '文档');
    });
    await pause();
    assert.deepEqual(queries(), ['文档']);
  } finally {
    await view.unmount();
  }
});
