import assert from 'node:assert/strict';
import test from 'node:test';
import { act, createElement as h } from 'react';
import { FileBrowser } from '@openviking/react/modules/file-browser';
import { OpenVikingProvider } from '@openviking/react/client';
import { mount, ok, recordingFetch, settle } from './helpers/module-dom.mjs';
for (const name of ['HTMLIFrameElement', 'SVGElement'])
  globalThis[name] = window[name];
globalThis.getComputedStyle = window.getComputedStyle.bind(window);

test('ranked results highlight by keyboard and open the active one', async () => {
  const first = 'viking://resources/one.md';
  const second = 'viking://resources/two.md';
  const opened = [];
  const fetch = recordingFetch((call) => {
    if (call.path === '/api/v1/search/find')
      return ok({
        resources: [
          { uri: second, score: 0.4 },
          { uri: first, score: 0.9 },
        ],
      });
    return ok([]);
  });
  const view = await mount(
    h(
      OpenVikingProvider,
      { baseURL: 'https://ov.test', fetch },
      h(FileBrowser, { uri: '', onUriChange: (uri) => opened.push(uri) }),
    ),
  );
  const wait = (ms) =>
    act(() => new Promise((resolve) => setTimeout(resolve, ms)));
  const modal = () => document.querySelector('.ov-file-browser-search');
  try {
    await act(async () =>
      view.container.querySelector('.ov-file-browser__search-trigger').click(),
    );
    const field = modal().querySelector('input');
    await act(async () => {
      Object.getOwnPropertyDescriptor(
        window.HTMLInputElement.prototype,
        'value',
      ).set.call(field, 'file');
      field.dispatchEvent(new window.Event('input', { bubbles: true }));
    });
    await wait(450);
    await wait(140);
    assert.equal(
      modal()
        .querySelector('[role="option"][aria-selected="true"]')
        .textContent.includes('one.md'),
      true,
    );
    await act(async () =>
      field.dispatchEvent(
        new window.KeyboardEvent('keydown', {
          bubbles: true,
          cancelable: true,
          key: 'ArrowDown',
        }),
      ),
    );
    assert.equal(
      modal()
        .querySelector('[role="option"][aria-selected="true"]')
        .textContent.includes('two.md'),
      true,
    );
    await act(async () =>
      field.dispatchEvent(
        new window.KeyboardEvent('keydown', {
          bubbles: true,
          cancelable: true,
          key: 'Enter',
        }),
      ),
    );
    assert.deepEqual(opened, [second]);
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
    act(() => new Promise((resolve) => setTimeout(resolve, 450)));
  const queries = () =>
    fetch.calls
      .filter((call) => call.path === path)
      .map((call) => call.body.query);
  try {
    await act(async () =>
      view.container.querySelector('.ov-file-browser__search-trigger').click(),
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
