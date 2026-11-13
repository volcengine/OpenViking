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

test('ranked results highlight by keyboard and replace stale previews', async () => {
  const first = 'viking://resources/one.md';
  const second = 'viking://resources/two.md';
  const old = deferred();
  const opened = [];
  const fetch = recordingFetch((call) => {
    if (call.path === '/api/v1/search/find')
      return ok({
        resources: [
          { uri: second, score: 0.4 },
          { uri: first, score: 0.9 },
        ],
      });
    if (call.path === '/api/v1/content/read')
      return call.uri === first ? old.promise : ok('Second file body');
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
    const previous = fetch.calls.find(
      (call) => call.path === '/api/v1/content/read' && call.uri === first,
    );
    assert.equal(previous.query.limit, '20');
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

test('passing over results keeps the current preview until the pointer settles', async () => {
  const uris = ['one', 'two', 'three'].map(
    (name) => `viking://resources/${name}.md`,
  );
  const last = deferred();
  const fetch = recordingFetch(({ path, uri }) => {
    if (path === '/api/v1/search/find')
      return ok({
        resources: uris.map((uri) => ({ uri, abstract: `Summary for ${uri}` })),
      });
    if (path === '/api/v1/content/read')
      return uri === uris[2] ? last.promise : ok('First file body');
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
  try {
    await act(async () =>
      view.container.querySelector('.ov-file-browser__search-trigger').click(),
    );
    const modal = document.querySelector('.ov-file-browser-search');
    const input = modal.querySelector('input');
    await act(async () => {
      Object.getOwnPropertyDescriptor(
        window.HTMLInputElement.prototype,
        'value',
      ).set.call(input, 'file');
      input.dispatchEvent(new window.Event('input', { bubbles: true }));
    });
    await wait(450);
    await wait(150);
    const options = [...modal.querySelectorAll('[role="option"]')];
    const preview = modal.querySelector('aside');
    const pointer = (index, type) =>
      options[index].dispatchEvent(
        new window.MouseEvent(type, { bubbles: true }),
      );
    assert.match(preview.textContent, /First file body/);
    await act(async () => pointer(1, 'pointerover'));
    assert.match(preview.textContent, /First file body/);
    await act(async () => pointer(1, 'pointerout'));
    await act(async () => pointer(2, 'pointerover'));
    assert.match(preview.textContent, /First file body/);
    await wait(180);
    assert.match(
      preview.textContent,
      /Summary for viking:\/\/resources\/three\.md/,
    );
    assert.deepEqual(
      fetch.calls
        .filter((call) => call.path === '/api/v1/content/read')
        .map((call) => call.uri),
      [uris[0], uris[2]],
    );
    await act(async () => last.resolve(await ok('Third file body')));
    assert.match(preview.textContent, /Third file body/);
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
