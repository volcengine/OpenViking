import { test } from 'node:test';
import assert from 'node:assert/strict';
import { h, mount } from './helpers/monitoring-dom.mjs';
import { runtime as r } from './helpers/monitoring.mjs';
const text = {
  previewImage: 'Preview image',
  closePreview: 'Close',
  loading: 'Loading',
};
function mediaTree(events, input = {}) {
  const {
    enabled = true,
    reports = true,
    source = { fileName: 'image.png', url: '/safe-example.png' },
    kind = 'image',
  } = input;
  return h(
    r.OpenVikingProvider,
    {
      monitoring: enabled
        ? {
            onEvent(event) {
              events.push(event);
            },
          }
        : false,
    },
    h(Media, { source, kind, reports, text }),
  );
}
const Media = r.withMonitoring(r.FilePreviewMedia, 'FilePreview');

test('native image readiness settles once, and closed dialog loads do not report', async () => {
  const events = [],
    app = await mount(mediaTree(events));
  try {
    const image = app.container.querySelector('[data-ov-preview-image]');
    await app.event(image, 'load');
    await app.event(image, 'load');
    await app.event(app.container.querySelector('dialog img'), 'load');
    await app.event(app.container.querySelector('dialog img'), 'error');
    assert.ok(app.container.querySelector('[data-ov-preview-image]'));
    assert.deepEqual(
      events.map((e) => e.name),
      ['file_preview_image_load_ready'],
    );
    await app.event(
      app.container.querySelector('.ov-file-preview__image-button'),
      'click',
    );
    await app.event(app.container.querySelector('dialog img'), 'load');
    await app.event(app.container.querySelector('dialog img'), 'load');
    assert.equal(
      events.filter((e) => e.name === 'file_preview_image_dialog_ready').length,
      1,
    );
    await app.event(app.container.querySelector('dialog button'), 'click');
    await app.event(app.container.querySelector('dialog img'), 'load');
    assert.equal(events.length, 2);
    await app.event(
      app.container.querySelector('.ov-file-preview__image-button'),
      'click',
    );
    await app.event(app.container.querySelector('dialog img'), 'load');
    assert.equal(
      events.filter((e) => e.name === 'file_preview_image_dialog_ready').length,
      2,
    );
  } finally {
    await app.dispose();
  }
});

test('background media reports only when promoted; enabling after a completed load does not replay', async () => {
  const events = [],
    app = await mount(mediaTree(events, { reports: false }));
  try {
    await app.event(
      app.container.querySelector('[data-ov-preview-image]'),
      'load',
    );
    assert.equal(events.length, 0);
    await app.render(mediaTree(events));
    assert.equal(
      events.filter((e) => e.name === 'file_preview_image_load_ready').length,
      1,
    );
    await app.render(mediaTree(events, { reports: false }));
    await app.render(mediaTree(events));
    assert.equal(events.length, 1);
    await app.render(
      mediaTree(events, {
        enabled: false,
        source: { fileName: 'next.png', url: '/next.png' },
      }),
    );
    await app.event(
      app.container.querySelector('[data-ov-preview-image]'),
      'load',
    );
    await app.render(
      mediaTree(events, { source: { fileName: 'next.png', url: '/next.png' } }),
    );
    assert.equal(events.length, 1);
  } finally {
    await app.dispose();
  }
});

test('video metadata readiness has its own event; cancellation before readiness emits nothing', async () => {
  const events = [],
    app = await mount(
      mediaTree(events, {
        source: { fileName: 'video.mp4', url: '/video.mp4' },
        kind: 'video',
      }),
    );
  await app.event(app.container.querySelector('video'), 'loadedmetadata');
  await app.event(app.container.querySelector('video'), 'loadedmetadata');
  assert.deepEqual(
    events.map((e) => e.name),
    ['file_preview_video_metadata_load_ready'],
  );
  await app.dispose();
  const cancelled = await mount(mediaTree(events));
  await cancelled.dispose();
  assert.equal(events.length, 1);
});

test('retrying a failed native preview settles a new preparation and resource attempt', async () => {
  const events = [];
  const source = { fileName: 'image.png', url: '/preview.png' };
  const Body = r.withMonitoring(
    () =>
      h(r.FilePreviewBody, {
        fileTab: { kind: 'file', key: 'preview', source },
        text: { ...text, retry: 'Retry', loadFailed: 'Failed' },
        editing: {},
        viewMode: 'preview',
        onViewModeChange() {},
        onHandleChange() {},
        onViewModesChange() {},
        onCancelEditing() {},
      }),
    'FilePreview',
  );
  const app = await mount(
    h(
      r.OpenVikingProvider,
      {
        monitoring: {
          onEvent(event) {
            events.push(event);
          },
        },
      },
      h(Body),
    ),
  );
  try {
    await app.event(
      app.container.querySelector('[data-ov-preview-image]'),
      'error',
    );
    assert.equal(
      events.filter((e) => e.name === 'file_preview_content_prepare_fail')
        .length,
      1,
    );
    assert.equal(
      events.filter((e) => e.name === 'file_preview_content_prepare_ready')
        .length,
      0,
    );
    await app.event(app.container.querySelector('button'), 'click');
    await app.event(
      app.container.querySelector('[data-ov-preview-image]'),
      'load',
    );
    const preparation = events.filter(
      (e) => e.name === 'file_preview_content_prepare_ready',
    );
    assert.equal(preparation.length, 1);
    assert.equal(preparation[0].trace.trigger, 'retry');
    assert.equal(
      events.filter((e) => e.name === 'file_preview_image_load_ready').length,
      1,
    );
  } finally {
    await app.dispose();
  }
});
