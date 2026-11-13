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
const find = '/api/v1/search/find';
const scope = 'viking://resources';
const uri = `${scope}/profile.md`;
const requestOptions = { userId: 'search-user' };
const render = (fetch, locale = 'en-US') =>
  h(
    OpenVikingProvider,
    { fetch, baseURL: 'https://ov.test' },
    h(FileBrowser, { locale, rootUri: scope, requestOptions }),
  );
const trigger = () =>
  document.querySelector('.ov-file-browser__search-trigger');
const dialog = () => document.querySelector('.ov-file-browser-search');
const input = () => dialog().querySelector('.ov-file-browser-search__input');
const modeSwitch = () =>
  dialog().querySelector('.ov-file-browser-search__switch');
const calls = (fetch) => fetch.calls.filter((call) => call.path === find);
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
/** 输入之后等过防抖窗口。Waits past the debounce window after typing. */
async function waitForDebounce() {
  await act(async () => new Promise((resolve) => setTimeout(resolve, 450)));
  await settle();
}

test('search runs as you type, keeps the scope, and survives a locale change', async () => {
  const fetch = recordingFetch((call) =>
    call.path === find
      ? ok({ resources: [{ uri, abstract: 'Profile result' }] })
      : ok([]),
  );
  const view = await mount(render(fetch));
  try {
    const treeText = view.container.querySelector(
      '.ov-file-browser__tree-scroll',
    ).textContent;
    assert.equal(trigger().disabled, false);
    await open();
    assert.equal(
      dialog().querySelector('[role="dialog"]').getAttribute('aria-label'),
      'Search files and content',
    );
    assert.equal(document.activeElement, input());
    assert.equal(modeSwitch(), null);

    await type('  profile  ');
    assert.equal(calls(fetch).length, 0);
    await waitForDebounce();

    assert.equal(calls(fetch).length, 1);
    assert.equal(calls(fetch)[0].init.method, 'POST');
    assert.deepEqual(calls(fetch)[0].body, {
      query: 'profile',
      target_uri: scope,
      limit: 20,
    });
    assert.equal(
      new Headers(calls(fetch)[0].init.headers).get('X-OpenViking-User'),
      'search-user',
    );
    assert.match(
      dialog().querySelector('.ov-file-browser-search__list').textContent,
      /profile\.md/,
    );
    await act(async () => modeSwitch().click());
    assert.equal(
      dialog().querySelectorAll('.ov-file-browser-search__menu-item').length,
      4,
    );
    assert.equal(
      view.container.querySelector('.ov-file-browser__tree-scroll').textContent,
      treeText,
    );

    await view.render(render(fetch, 'zh-CN'));
    assert.equal(input().value, '  profile  ');
    assert.equal(calls(fetch).length, 1);
  } finally {
    await view.unmount();
  }
});

test('an empty input never issues a request', async () => {
  const fetch = recordingFetch(() => ok([]));
  const view = await mount(render(fetch));
  try {
    await open();
    await type('   ');
    await waitForDebounce();
    assert.equal(calls(fetch).length, 0);
  } finally {
    await view.unmount();
  }
});

test('failed searches expose the reason and can retry the same query', async () => {
  let available = false;
  const fetch = recordingFetch((call) => {
    if (call.path !== find) return ok([]);
    return available
      ? ok({ resources: [] })
      : new Response(
          JSON.stringify({
            status: 'error',
            error: { code: 'FAILED', message: 'demo search failed' },
          }),
          { status: 503, headers: { 'Content-Type': 'application/json' } },
        );
  });
  const view = await mount(render(fetch));
  try {
    await open();
    await type('profile');
    await waitForDebounce();
    assert.match(
      dialog().querySelector('[role="alert"]').textContent,
      /demo search failed/,
    );
    assert.equal(modeSwitch(), null);

    available = true;
    await act(async () =>
      [...dialog().querySelectorAll('button')]
        .find((button) => button.textContent === 'Retry')
        .click(),
    );
    await settle();
    assert.equal(calls(fetch).length, 2);
    assert.equal(calls(fetch)[1].body.query, 'profile');
    assert.match(dialog().textContent, /No matching resources/);
    assert.equal(modeSwitch(), null);
  } finally {
    await view.unmount();
  }
});

test('closing cancels the pending search, clears the draft and restores focus', async () => {
  const pending = deferred();
  const fetch = recordingFetch((call) =>
    call.path === find ? pending.promise : ok([]),
  );
  const view = await mount(render(fetch));
  try {
    await open();
    await type('profile');
    await waitForDebounce();
    const signal = calls(fetch)[0].init.signal;
    assert.ok(dialog().querySelector('[aria-busy="true"]'));
    assert.equal(modeSwitch(), null);

    await act(async () => dialog().querySelector('.ov-modal__close').click());
    await settle();
    assert.equal(signal.aborted, true);

    await act(async () => pending.resolve(await ok({ resources: [{ uri }] })));
    await act(async () => new Promise((resolve) => setTimeout(resolve, 330)));
    assert.equal(document.activeElement, trigger());

    await open();
    assert.equal(input().value, '');
    assert.equal(dialog().querySelector('.ov-file-browser-search__list'), null);
    assert.equal(calls(fetch).length, 1);
  } finally {
    await view.unmount();
  }
});
