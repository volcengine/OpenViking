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
    const options = [...modal.querySelectorAll('[role="option"]')];
    const pointer = (index, type) =>
      options[index].dispatchEvent(
        new window.MouseEvent(type, { bubbles: true }),
      );
    assert.equal(modal.querySelector('aside'), null);
    await act(async () => pointer(0, 'pointermove'));
    await wait(180);
    const preview = modal.querySelector('aside');
    assert.match(preview.textContent, /First file body/);
    await act(async () => pointer(1, 'pointermove'));
    assert.match(preview.textContent, /First file body/);
    await act(async () => pointer(1, 'pointerout'));
    await act(async () => pointer(2, 'pointermove'));
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
