import assert from 'node:assert/strict';
import test from 'node:test';
import { act, createElement as h } from 'react';
import { OvClientError } from '@openviking/react/client';
import { FilePreview } from '@openviking/react/components/file-preview';
import { mount } from './helpers/module-dom.mjs';
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
const clipboard = (writeText) =>
  Object.defineProperty(navigator, 'clipboard', {
    configurable: true,
    value: { writeText },
  });
const tree = (error, locale = 'en-US') =>
  h(FilePreview, {
    locale,
    labels: { actionFailed: 'Operation failed' },
    tabs: [
      {
        kind: 'file',
        key: 'file',
        source: { fileName: 'error.md', content: 'Normal content' },
        actions: [
          {
            key: 'export',
            label: 'Export',
            icon: h('svg'),
            onClick: async () => {
              throw error;
            },
          },
        ],
      },
    ],
  });

test('copy includes the full message and existing diagnostics, without response objects or markup', async () => {
  const message = `Backend <reason>\n${'Unabridged error detail. '.repeat(200)}`;
  const copied = [];
  clipboard(async (value) => copied.push(value));
  const view = await mount(
    tree(
      new OvClientError({
        code: 'SERVER_ERROR',
        message,
        statusCode: 503,
        requestId: 'request-42',
        responseBody: { arbitraryResponse: true },
      }),
    ),
  );
  try {
    await click(button('Export'));
    await click(button('Copy error details'));
    assert.deepEqual(copied, [
      `Export · Operation failed\n${message}\nError code: SERVER_ERROR\nHTTP status: 503\nRequest ID: request-42`,
    ]);
    assert.equal(
      notificationError().querySelector('[role="status"]').textContent,
      'Copied',
    );
    assert.equal(button('Copy error details').disabled, false);
    await click(button('Copy error details'));
    assert.equal(
      notificationError().querySelector('[role="status"]').textContent,
      'Copied\u00a0',
    );
    await click(button('Copy error details'));
    assert.equal(
      notificationError().querySelector('[role="status"]').textContent,
      'Copied',
    );
    assert.equal(copied.length, 3);
    assert.equal(copied[2], copied[0]);
    assert.doesNotMatch(view.container.textContent, /Backend|SERVER_ERROR/);
  } finally {
    await view.unmount();
  }
});

test('denied clipboard access reports a local Toast and permits another copy', async () => {
  let fail = true;
  let copied;
  clipboard(async (value) => {
    if (fail) throw new Error('Denied');
    copied = value;
  });
  const view = await mount(tree(new Error('Server reason')));
  try {
    await click(button('Export'));
    await click(button('Copy error details'));
    assert.match(
      document.querySelector('.ov-toast--error').textContent,
      /Could not copy/,
    );
    assert.match(notificationError().textContent, /Server reason/);
    assert.equal(button('Copy error details').disabled, false);
    fail = false;
    await click(button('Copy error details'));
    assert.equal(copied, 'Export · Operation failed\nServer reason');
    assert.equal(
      notificationError().querySelector('[role="status"]').textContent,
      'Copied',
    );
    assert.doesNotMatch(copied, /Could not copy|Copy error details/);
  } finally {
    await view.unmount();
  }
});

test('copy labels follow locale and each notification copies only its own failure', async () => {
  const copied = [];
  clipboard(async (value) => copied.push(value));
  const first = new OvClientError({
    code: 'INVALID_ARGUMENT',
    message: 'First reason',
    requestId: 'first-request',
  });
  const view = await mount(tree(first));
  let other;
  try {
    await click(button('Export'));
    await view.render(tree(first, 'zh-CN'));
    await click(button('复制错误信息'));
    assert.equal(
      copied[0],
      'Export · Operation failed\nFirst reason\n错误码: INVALID_ARGUMENT\nRequest ID: first-request',
    );
    other = await mount(tree(new Error('Second reason')));
    const exports = [...document.querySelectorAll('button')].filter(
      (node) => node.getAttribute('aria-label') === 'Export',
    );
    await click(exports[1]);
    await click(button('Copy error details'));
    assert.equal(copied[1], 'Export · Operation failed\nSecond reason');
    assert.doesNotMatch(copied[1], /First reason|first-request/);
  } finally {
    await other?.unmount();
    await view.unmount();
  }
});

test('a clipboard write completing after the notice closes produces no late feedback', async () => {
  let finish;
  clipboard(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  );
  const view = await mount(tree(new Error('Server reason')));
  await click(button('Export'));
  await click(button('Copy error details'));
  await view.unmount();
  await act(async () => finish());
  assert.equal(Boolean(document.querySelector('.ov-toast')), false);
  assert.equal(Boolean(document.querySelector('.ov-notification')), false);
});
