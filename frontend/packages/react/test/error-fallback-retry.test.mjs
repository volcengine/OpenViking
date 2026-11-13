import assert from 'node:assert/strict';
import { test } from 'node:test';
import { act, createElement as h } from 'react';
import { FilePreview } from '@openviking/react/components/file-preview';
import { VikingBot } from '@openviking/react/components/viking-bot';
import { mount, setTextarea } from './helpers/module-dom.mjs';
import {
  fixture,
  tree,
  target,
  settle,
} from './helpers/preview-native-dom.mjs';

const fallback = (reset) =>
  h('button', { className: 'host-retry', onClick: reset }, 'Retry region');

test('a block-inherited render function retries only its failed preview, retaining selection and request state', async (t) => {
  t.mock.method(console, 'error', () => {});
  let broken = false;
  let fallbackCalls = 0;
  function Content() {
    if (broken) throw new Error('intentional preview failure');
    return h('span', null, 'Recovered preview');
  }
  const fetch = fixture(target, false);
  const view = () =>
    tree(fetch, target, {
      slots: {
        errorFallback: (reset) => {
          fallbackCalls++;
          return fallback(reset);
        },
      },
      previewSlots: { extraActions: h(Content) },
    });
  const host = await mount(view());
  try {
    await settle(5);
    assert.equal(
      fallbackCalls,
      0,
      'healthy rendering never calls the fallback',
    );
    const fileTree = host.container.querySelector('.ov-file-tree');
    const selected = host.container.querySelector('[aria-selected="true"]');
    const requests = fetch.calls.length;
    broken = true;
    await host.render(view());
    assert.ok(host.container.querySelector('.host-retry'));
    broken = false;
    await act(() => host.container.querySelector('.host-retry').click());
    assert.match(host.container.textContent, /Recovered preview/);
    assert.equal(host.container.querySelector('.host-retry'), null);
    assert.equal(host.container.querySelector('.ov-file-tree'), fileTree);
    assert.equal(
      host.container.querySelector('[aria-selected="true"]'),
      selected,
    );
    assert.equal(fetch.calls.length, requests);
  } finally {
    await host.unmount();
  }
});

test('inherited retry functions belong to individual failed regions and preserve the composer draft', async (t) => {
  t.mock.method(console, 'error', () => {});
  const broken = { list: true, footer: true };
  const attempts = { list: 0, footer: 0 };
  function Content({ region }) {
    attempts[region]++;
    if (broken[region]) throw new Error(`${region} failed`);
    return h('p', null, `Recovered ${region}`);
  }
  const host = await mount(
    h(VikingBot, {
      messages: [],
      slots: { errorFallback: fallback },
      messageList: h(Content, { region: 'list' }),
      footer: h(Content, { region: 'footer' }),
      composer: h('textarea', { 'aria-label': 'Draft', defaultValue: '' }),
    }),
  );
  try {
    const input = host.container.querySelector('textarea');
    await setTextarea(input, 'Draft to retain');
    const footerAttempts = attempts.footer;
    await act(() => host.container.querySelector('.host-retry').click());
    assert.ok(attempts.list > 1);
    assert.equal(attempts.footer, footerAttempts);
    assert.equal(host.container.querySelectorAll('.host-retry').length, 2);
    broken.list = false;
    await act(() => host.container.querySelector('.host-retry').click());
    assert.match(host.container.textContent, /Recovered list/);
    assert.equal(host.container.querySelectorAll('.host-retry').length, 1);
    broken.footer = false;
    await act(() => host.container.querySelector('.host-retry').click());
    assert.match(host.container.textContent, /Recovered footer/);
    assert.equal(host.container.querySelector('textarea'), input);
    assert.equal(input.value, 'Draft to retain');
  } finally {
    await host.unmount();
  }
});

for (const [fileName, tag] of [
  ['image.png', 'img'],
  ['movie.mp4', 'video'],
]) {
  test(`FilePreview ${tag} read failures pass the media retry action to the tab render function`, async () => {
    const host = await mount(
      h(FilePreview, {
        tabs: [
          {
            key: 'media',
            kind: 'file',
            source: { fileName, url: 'https://example.test/media' },
            slots: { errorFallback: fallback },
          },
        ],
      }),
    );
    try {
      const media = host.container.querySelector(tag);
      await act(() => media.dispatchEvent(new window.Event('error')));
      assert.ok(host.container.querySelector('.host-retry'));
      await act(() => host.container.querySelector('.host-retry').click());
      assert.ok(host.container.querySelector(tag));
      assert.notEqual(host.container.querySelector(tag), media);
      assert.equal(host.container.querySelector('.host-retry'), null);
    } finally {
      await host.unmount();
    }
  });
}
