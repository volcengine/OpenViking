import assert from 'node:assert/strict';
import test from 'node:test';
import { createElement as h, act } from 'react';
import { FilePreview } from '@openviking/react/components/file-preview';
import { mount, settle } from './helpers/module-dom.mjs';

for (const [fileName, tag] of [
  ['diagram.svg', 'img'],
  ['recording.mp4', 'video'],
]) {
  test(`${tag} is rendered natively without FileViewer and supports loading/error/retry`, async () => {
    const source = { fileName, url: 'https://example.test/media' };
    let context;
    const view = await mount(
      h(FilePreview, {
        tabs: [
          {
            key: 'media',
            kind: 'file',
            source,
            editing: { save: async () => {} },
            slots: {
              actions: (value) => {
                context = value;
                return null;
              },
            },
          },
        ],
      }),
    );
    try {
      assert.equal(view.container.querySelector('[data-file-viewer]'), null);
      assert.equal(context.canEdit, false);
      await act(async () => context.startEditing());
      assert.equal(view.container.querySelector('textarea'), null);
      const media = view.container.querySelector(tag);
      assert.ok(media);
      if (tag === 'img')
        Object.defineProperties(media, {
          naturalWidth: { value: 640 },
          naturalHeight: { value: 360 },
        });
      if (tag === 'video') {
        assert.equal(media.controls, true);
        assert.equal(media.getAttribute('controlslist'), 'nodownload');
        assert.equal(media.autoplay, false);
      }
      await act(async () =>
        media.dispatchEvent(
          new window.Event(tag === 'img' ? 'load' : 'loadedmetadata'),
        ),
      );
      assert.equal(view.container.querySelector('[aria-busy="true"]'), null);
      if (tag === 'img') assert.match(view.container.textContent, /640 × 360/);
      assert.equal(
        view.container.querySelector('[aria-label="Switch view"]'),
        null,
      );
      await act(async () => media.dispatchEvent(new window.Event('error')));
      assert.match(view.container.textContent, /load/i);
      const retry = [...view.container.querySelectorAll('button')].find(
        (button) => button.textContent === 'Retry',
      );
      assert.ok(retry);
      await act(async () => retry.click());
      await settle();
      assert.ok(view.container.querySelector(tag));
    } finally {
      await view.unmount();
    }
  });
}

test('blob media URLs are replaced and revoked without retaining the previous source', async () => {
  const originalCreate = URL.createObjectURL;
  const originalRevoke = URL.revokeObjectURL;
  const revoked = [];
  let next = 0;
  URL.createObjectURL = () => `blob:media-${++next}`;
  URL.revokeObjectURL = (url) => revoked.push(url);
  const render = (file) =>
    h(FilePreview, {
      tabs: [
        { key: 'image', kind: 'file', source: { fileName: 'same.png', file } },
      ],
    });
  const view = await mount(render(new Blob(['first'], { type: 'image/png' })));
  try {
    assert.equal(
      view.container.querySelector('img').getAttribute('src'),
      'blob:media-1',
    );
    await view.render(render(new Blob(['second'], { type: 'image/png' })));
    assert.equal(
      view.container.querySelector('img').getAttribute('src'),
      'blob:media-2',
    );
    assert.deepEqual(revoked, ['blob:media-1']);
  } finally {
    await view.unmount();
    URL.createObjectURL = originalCreate;
    URL.revokeObjectURL = originalRevoke;
  }
  assert.deepEqual(revoked, ['blob:media-1', 'blob:media-2']);
});

for (const [fileName, tag] of [
  ['photo.png', 'img'],
  ['recording.mp4', 'video'],
]) {
  test(`${tag} preserves custom loading, error and missing-source fallbacks`, async () => {
    const render = (loading, url, error = 'CUSTOM_ERROR') =>
      h(FilePreview, {
        tabs: [
          {
            key: 'media',
            kind: 'file',
            loading,
            source: { fileName, url },
            slots: {
              loadingFallback: h('p', null, 'CUSTOM_LOADING'),
              errorFallback: h('p', null, error),
              unsupportedFallback: h('p', null, 'CUSTOM_UNSUPPORTED'),
            },
          },
        ],
      });
    const url = 'https://example.test/media';
    const view = await mount(render(true, url));
    try {
      assert.match(view.container.textContent, /CUSTOM_LOADING/);
      await view.render(render(false, url));
      assert.match(view.container.textContent, /CUSTOM_LOADING/);
      const media = view.container.querySelector(tag);
      await act(async () => media.dispatchEvent(new window.Event('error')));
      assert.match(view.container.textContent, /CUSTOM_ERROR/);
      assert.doesNotMatch(view.container.textContent, /Failed to load/);
      await view.render(render(false, url, 'UPDATED_ERROR'));
      assert.match(view.container.textContent, /UPDATED_ERROR/);
      await view.render(render(false, undefined));
      assert.match(view.container.textContent, /CUSTOM_UNSUPPORTED/);
      assert.equal(view.container.querySelector(tag), null);
    } finally {
      await view.unmount();
    }
  });
}
