import assert from 'node:assert/strict';
import test from 'node:test';
import { act, createElement as h } from 'react';
import { FilePreview } from '@openviking/react/components/file-preview';
import { mount } from './helpers/module-dom.mjs';
import { waitForEditor } from './helpers/preview-editor-dom.mjs';
import { notificationError } from './helpers/feedback-dom.mjs';

const button = (name) =>
  [...document.querySelectorAll('button')].find(
    (node) =>
      node.textContent === name || node.getAttribute('aria-label') === name,
  );
const click = (node) =>
  act(async () => {
    assert.ok(node);
    node.click();
  });
const tree = (action, locale = 'en-US') =>
  h(FilePreview, {
    locale,
    tabs: [
      {
        kind: 'file',
        key: 'file',
        source: { fileName: 'draft.js', content: 'const draft = 1;' },
        actions: [{ key: 'export', label: 'Export', onClick: action }],
        editing: {
          save: async () => {
            throw new Error('Save backend reason');
          },
        },
      },
    ],
  });

test('dismissed action feedback stays dismissed across locale changes and returns for another attempt', async () => {
  let cancelled = false;
  const run = async () => {
    if (cancelled) throw new DOMException('Cancelled', 'AbortError');
    throw new Error('Raw reason');
  };
  const view = await mount(tree(run));
  try {
    await click(button('Export'));
    assert.match(notificationError().textContent, /Raw reason/);
    assert.doesNotMatch(view.container.textContent, /Raw reason/);
    await click(notificationError().querySelector('.ov-notification__close'));
    await view.render(tree(run, 'zh-CN'));
    assert.equal(Boolean(notificationError()), false);
    await click(button('Export'));
    assert.match(notificationError().textContent, /操作失败.*Raw reason/);
    await click(notificationError().querySelector('.ov-notification__close'));
    cancelled = true;
    await click(button('Export'));
    assert.equal(Boolean(notificationError()), false);
  } finally {
    await view.unmount();
  }
});

test('action and save failures share one stack while the editor retains its draft', async () => {
  const view = await mount(
    tree(async () => {
      throw new Error('Export backend reason');
    }),
  );
  try {
    await click(button('Export'));
    await click(button('Edit'));
    const editor = await waitForEditor(view.container);
    await click(button('Save'));
    assert.equal(document.querySelectorAll('.ov-notification-stack').length, 1);
    assert.equal(
      document.querySelectorAll('.ov-notification--error').length,
      2,
    );
    assert.match(document.body.textContent, /Export backend reason/);
    assert.match(document.body.textContent, /Save backend reason/);
    assert.doesNotMatch(view.container.textContent, /backend reason/);
    assert.match(editor.textContent, /const draft = 1/);
    assert.equal(button('Save').disabled, false);
  } finally {
    await view.unmount();
  }
});

test('action failures use the default notification timeout and stay dismissed across locale changes', async () => {
  const run = async () => {
    throw new Error('Default timeout');
  };
  const view = await mount(tree(run));
  try {
    await click(button('Export'));
    assert.ok(notificationError());
    await act(() => new Promise((resolve) => setTimeout(resolve, 3100)));
    assert.equal(Boolean(notificationError()), false);
    await view.render(tree(run, 'zh-CN'));
    assert.equal(Boolean(notificationError()), false);
  } finally {
    await view.unmount();
  }
});
