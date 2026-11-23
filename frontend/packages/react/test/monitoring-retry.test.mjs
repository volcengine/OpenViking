import test from 'node:test';
import assert from 'node:assert/strict';
import { h, mount } from './helpers/monitoring-dom.mjs';
import { runtime as r } from './helpers/monitoring.mjs';

test('retry after enabling observes new preparation; replacing its source starts a separate workflow', async () => {
  const events = [];
  const Body = r.withMonitoring(
    ({ source }) =>
      h(r.FilePreviewBody, {
        fileTab: { kind: 'file', key: 'preview', source },
        text: { retry: 'Retry', loadFailed: 'Failed' },
        editing: {},
        viewMode: 'preview',
        onViewModeChange() {},
        onHandleChange() {},
        onViewModesChange() {},
        onCancelEditing() {},
      }),
    'FilePreview',
  );
  const source = { fileName: 'image.png', url: '/bad.png' };
  const tree = (enabled, value = source) =>
    h(
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
      h(Body, { source: value }),
    );
  const app = await mount(tree(false));
  try {
    await app.event(
      app.container.querySelector('[data-ov-preview-image]'),
      'error',
    );
    await app.render(tree(true));
    assert.equal(events.length, 0);
    await app.event(app.container.querySelector('button'), 'click');
    await app.event(
      app.container.querySelector('[data-ov-preview-image]'),
      'error',
    );
    const failure = events.find(
      (event) => event.name === 'file_preview_content_prepare_fail',
    );
    assert.ok(failure);
    assert.equal(failure.trace.trigger, 'retry');
    assert.ok(
      failure.params.traceSnapshot.gaps.includes('started-before-enabled'),
    );
    await app.render(tree(true, { fileName: 'next.png', url: '/next.png' }));
    await app.event(
      app.container.querySelector('[data-ov-preview-image]'),
      'load',
    );
    const ready = events.find(
      (event) => event.name === 'file_preview_content_prepare_ready',
    );
    assert.ok(ready);
    assert.notEqual(ready.trace.trigger, 'retry');
    assert.notEqual(ready.trace.traceId, failure.trace.traceId);
  } finally {
    await app.dispose();
  }
});

test('StrictMode reports empty-content preparation once per actual mounted view', async () => {
  const { StrictMode } = await import('react');
  const events = [];
  const Body = r.withMonitoring(
    () =>
      h(r.FilePreviewBody, {
        fileTab: {
          kind: 'file',
          key: 'empty',
          source: { fileName: 'empty.txt', content: '' },
        },
        text: { retry: 'Retry', loadFailed: 'Failed' },
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
      StrictMode,
      null,
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
    ),
  );
  try {
    assert.equal(
      events.filter(
        (event) => event.name === 'file_preview_content_prepare_ready',
      ).length,
      1,
    );
  } finally {
    await app.dispose();
  }
});
