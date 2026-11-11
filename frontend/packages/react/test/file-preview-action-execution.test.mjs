import assert from 'node:assert/strict';
import test from 'node:test';
import { act, createElement as h } from 'react';
import { FilePreview } from '@openviking/react/components/file-preview';
import { mount, settle } from './helpers/module-dom.mjs';
const action = (key, extra = {}) => ({
  key,
  label: key,
  icon: h('svg', { 'data-icon': key }),
  onClick: () => key,
  ...extra,
});
const source = {
  content: '',
  fileName: 'guide.md',
  path: 'viking://resources/guide.md',
};
const tree = (actions, extra = {}, props = {}) =>
  h(FilePreview, {
    tabs: [{ kind: 'file', key: 'guide', source, actions, ...extra }],
    ...props,
  });
const button = (label) =>
  [...document.querySelectorAll('button')].find(
    (node) => node.getAttribute('aria-label') === label,
  );
const menu = () => document.querySelector('[role="menu"]');
const click = async (node) => {
  assert.ok(node);
  await act(async () => node.click());
};
const key = async (node, value) => {
  await act(async () =>
    node.dispatchEvent(
      new window.KeyboardEvent('keydown', {
        key: value,
        bubbles: true,
        cancelable: true,
      }),
    ),
  );
};
const pointer = async (node, type) => {
  const event = new window.MouseEvent(type, { bubbles: true });
  Object.defineProperty(event, 'pointerType', { value: 'mouse' });
  await act(async () => node.dispatchEvent(event));
};

test('action factory tracks selected directory summary and full render override wins', async () => {
  let context;
  const make = (extra = {}) =>
    h(FilePreview, {
      tabs: [
        {
          kind: 'directory',
          key: 'dir',
          loading: true,
          levels: [
            { key: 'l0', label: 'L0', source },
            {
              key: 'l1',
              label: 'L1',
              source: { ...source, path: 'viking://resources/.overview.md' },
            },
          ],
          actions: (value) => {
            context = value;
            return [action(value.path)];
          },
          ...extra,
        },
      ],
    });
  const view = await mount(make());
  try {
    assert.equal(context.path, source.path);
    assert.equal(context.source.fileName, 'guide.md');
    // A loaded empty summary exposes its level switch without loading Ark adapters.
    await view.render(make({ loading: false }));
    await click(
      [...document.querySelectorAll('[role="tab"]')].find(
        (node) => node.textContent === 'L1',
      ),
    );
    assert.equal(context.path, 'viking://resources/.overview.md');
    assert.ok(button(context.path));
    await view.render(make({ slots: { actions: null } }));
    assert.equal(document.querySelector('.ov-file-preview__toolbar'), null);
  } finally {
    await view.unmount();
  }
});

test('async non-void results are ignored; failures show error, unlock and allow retry', async () => {
  let calls = 0;
  let reject;
  let resolve;
  const run = action('Run', {
    onClick: () => {
      calls++;
      return new Promise((yes, no) => {
        resolve = yes;
        reject = no;
      });
    },
  });
  const view = await mount(tree([run]));
  try {
    await click(button('Run'));
    assert.equal(button('Run').disabled, true);
    await click(button('Run'));
    assert.equal(calls, 1);
    await act(async () => reject(new Error('Try again')));
    assert.match(
      document.querySelector('[role="alert"]').textContent,
      /Try again/,
    );
    assert.equal(button('Run').disabled, false);
    await click(button('Run'));
    assert.equal(document.querySelector('[role="alert"]'), null);
    await act(async () => resolve({ ok: true }));
    assert.equal(document.querySelector('[role="alert"]'), null);
    await view.render(tree([action('Sync', { onClick: () => 42 })]));
    await click(button('Sync'));
    assert.equal(document.querySelector('[role="alert"]'), null);
    await view.render(tree([action('False', { onClick: () => false })]));
    await click(button('False'));
    assert.equal(document.querySelector('[role="alert"]'), null);
    await settle();
  } finally {
    await view.unmount();
  }
});

test('changing locale preserves pending execution and translates a rejected result', async () => {
  let reject;
  let calls = 0;
  const run = action('Run', {
    onClick: () => {
      calls++;
      return new Promise((resolve, fail) => {
        reject = fail;
      });
    },
  });
  const view = await mount(tree([run]));
  try {
    await click(button('Run'));
    await view.render(tree([run], {}, { locale: 'zh-CN' }));
    assert.equal(button('Run').disabled, true);
    assert.equal(calls, 1);
    await act(async () => reject(undefined));
    assert.match(
      document.querySelector('[role="alert"]').textContent,
      /操作失败/,
    );
    await view.render(
      tree(
        [run],
        {},
        { locale: 'en-US', labels: { actionFailed: 'Host failure' } },
      ),
    );
    assert.equal(
      document.querySelector('[role="alert"]').textContent,
      'Host failure',
    );
    assert.equal(button('Run').disabled, false);
  } finally {
    await view.unmount();
  }
});
