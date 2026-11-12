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
HTMLElement.prototype.scrollIntoView = function () {};
globalThis.CSS = { escape: (value) => value };
const scope = 'viking://resources';
const first = `${scope}/profile-a.png`;
const second = `${scope}/profile-b.png`;
const requestOptions = { userId: 'search-user' };
const render = (fetch, locale = 'en-US', onUriChange) =>
  h(
    OpenVikingProvider,
    { fetch, baseURL: 'https://ov.test' },
    h(FileBrowser, {
      locale,
      rootUri: scope,
      requestOptions,
      dashboardKinds: [],
      onUriChange,
    }),
  );
const trigger = () => document.querySelector('.ov-file-tree__search');
const dialog = () => document.querySelector('.ov-file-browser-search');
const input = () => dialog().querySelector('input[name="query"]');
const calls = (fetch) =>
  fetch.calls.filter((call) => call.path === '/api/v1/search/find');
const pause = (ms = 350) =>
  act(() => new Promise((resolve) => setTimeout(resolve, ms)));
const selected = () =>
  dialog().querySelector('[role="option"][aria-selected="true"]');

async function open() {
  await act(async () => {
    trigger().focus();
    trigger().click();
  });
  await settle();
}
async function type(value) {
  await act(async () => {
    Object.getOwnPropertyDescriptor(
      window.HTMLInputElement.prototype,
      'value',
    ).set.call(input(), value);
    input().dispatchEvent(new window.Event('input', { bubbles: true }));
  });
}
async function key(target, name, modifiers = {}) {
  await act(async () =>
    target.dispatchEvent(
      new window.KeyboardEvent('keydown', {
        bubbles: true,
        cancelable: true,
        key: name,
        ...modifiers,
      }),
    ),
  );
}

test('scoped shortcut opens live search; highest scored matches show compact rows and Enter opens the active result', async () => {
  const opened = [];
  const fetch = recordingFetch((call) => {
    if (call.path === '/api/v1/search/find')
      return ok({
        memories: [{ uri: second, abstract: 'Second excerpt', score: 0.4 }],
        resources: [{ uri: first, abstract: 'First excerpt', score: 0.9 }],
      });
    if (call.path.endsWith('/fs/ls'))
      return ok([{ uri: first }, { uri: second }]);
    if (call.path.endsWith('/content/download')) return new Response('image');
    return ok('');
  });
  const view = await mount(render(fetch, 'en-US', (uri) => opened.push(uri)));
  try {
    trigger().focus();
    await key(trigger(), 'k', { ctrlKey: true });
    assert.equal(document.activeElement, input());
    await type('prof');
    await pause(245);
    await type('  profile  ');
    assert.equal(calls(fetch).length, 0);
    await pause();
    assert.deepEqual(
      calls(fetch).map((call) => call.body),
      [{ query: 'profile', target_uri: scope, limit: 20 }],
    );
    assert.equal(
      new Headers(calls(fetch)[0].init.headers).get('X-OpenViking-User'),
      'search-user',
    );
    assert.equal(selected().getAttribute('title'), first);
    assert.match(selected().querySelector('mark').textContent, /profile/);
    assert.doesNotMatch(
      dialog().querySelector('[role="listbox"]').textContent,
      /excerpt/,
    );
    await key(input(), 'ArrowDown');
    assert.equal(selected().getAttribute('title'), second);
    assert.equal(input().getAttribute('aria-activedescendant'), selected().id);
    assert.match(dialog().querySelector('aside').textContent, /Second excerpt/);
    await view.render(render(fetch, 'zh-CN', (uri) => opened.push(uri)));
    assert.equal(input().value, '  profile  ');
    assert.equal(calls(fetch).length, 1);
    await act(async () =>
      input().form.dispatchEvent(
        new window.Event('submit', { bubbles: true, cancelable: true }),
      ),
    );
    await settle();
    assert.deepEqual(opened, [second]);
    assert.equal(
      view.container
        .querySelector('[data-file-tree-row-id][aria-selected="true"]')
        ?.getAttribute('data-file-tree-row-id'),
      second,
    );
  } finally {
    await view.unmount();
  }
});

test('failed live searches expose the cause and retry the same query', async () => {
  let available = false;
  const fetch = recordingFetch((call) => {
    if (call.path !== '/api/v1/search/find') return ok([]);
    return available
      ? ok({ resources: [] })
      : new Response(
          JSON.stringify({
            status: 'error',
            error: { code: 'FAILED', message: 'demo search failed' },
          }),
          { status: 503, headers: { 'content-type': 'application/json' } },
        );
  });
  const view = await mount(render(fetch));
  try {
    await open();
    await type('profile');
    await pause();
    assert.match(
      dialog().querySelector('[role="alert"]').textContent,
      /demo search failed/,
    );
    available = true;
    await act(async () =>
      Array.from(dialog().querySelectorAll('button'))
        .find((button) => button.textContent.trim() === 'Retry')
        .click(),
    );
    await pause();
    assert.equal(calls(fetch).length, 2);
    assert.match(dialog().textContent, /No matching resources/);
  } finally {
    await view.unmount();
  }
});

test('Escape aborts in-flight search, restores focus, and clears the draft', async () => {
  const pending = deferred();
  const fetch = recordingFetch((call) =>
    call.path === '/api/v1/search/find' ? pending.promise : ok([]),
  );
  const view = await mount(render(fetch));
  try {
    await open();
    await type('profile');
    await pause();
    const signal = calls(fetch)[0].init.signal;
    await key(input(), 'Escape');
    assert.equal(signal.aborted, true);
    await act(async () =>
      pending.resolve(await ok({ resources: [{ uri: first }] })),
    );
    await pause(330);
    assert.equal(document.activeElement, trigger());
    await open();
    assert.equal(input().value, '');
    assert.equal(dialog().querySelector('[role="listbox"]'), null);
    assert.equal(calls(fetch).length, 1);
  } finally {
    await view.unmount();
  }
});
