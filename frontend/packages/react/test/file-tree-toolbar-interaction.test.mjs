import assert from 'node:assert/strict';
import test from 'node:test';
import { act, createElement as h } from 'react';
import { FileTree } from '@openviking/react/components/file-tree';
import { mount, settle } from './helpers/module-dom.mjs';

globalThis.MutationObserver = window.MutationObserver;

const click = async (button) => {
  await act(() => button.click());
  await settle();
};

test('search reports success; refresh ignores repeats, recovers from rejection and supports retry', async () => {
  let fail;
  let executions = 0;
  let notifications = 0;
  let searches = 0;
  const props = {
    rows: [],
    operations: {
      search: async () => {},
      refresh: () => {
        executions++;
        return new Promise((_, reject) => {
          fail = reject;
        });
      },
    },
    onSearch: () => searches++,
    onRefresh: () => notifications++,
  };
  const view = await mount(h(FileTree, props));
  try {
    await click(view.container.querySelector('.ov-file-tree__search'));
    assert.equal(searches, 1);
    assert.equal(document.querySelector('[role="dialog"]'), null);
    const button = view.container.querySelector('.ov-file-tree__refresh');
    await click(button);
    await click(button);
    assert.equal(executions, 1);
    assert.equal(button.disabled, true);
    assert.equal(button.getAttribute('aria-busy'), 'true');
    await act(() => fail(new Error('Simulated failure')));
    await settle();
    assert.equal(button.disabled, false);
    assert.equal(notifications, 0);
    await view.render(
      h(FileTree, {
        ...props,
        operations: {
          ...props.operations,
          refresh: async () => {
            executions++;
          },
        },
      }),
    );
    await click(button);
    assert.equal(executions, 2);
    assert.equal(notifications, 1);
    await view.render(h(FileTree, { ...props, loading: true }));
    await click(button);
    assert.equal(executions, 2);
    assert.equal(button.disabled, true);
  } finally {
    await view.unmount();
  }
});

test('refresh tooltip opens on focus and Escape dismisses it without moving focus', async () => {
  const view = await mount(
    h(FileTree, {
      rows: [],
      operations: { refresh: async () => {} },
      labels: { refresh: 'Reload directory' },
    }),
  );
  try {
    const button = view.container.querySelector('.ov-file-tree__refresh');
    await act(() => button.focus());
    await act(() => new Promise((resolve) => setTimeout(resolve, 340)));
    const tooltip = document.querySelector('[role="tooltip"]');
    assert.equal(tooltip?.textContent, 'Reload directory');
    assert.equal(button.getAttribute('aria-label'), 'Reload directory');
    assert.equal(
      button.getAttribute('aria-describedby'),
      null,
      'identical names are not announced twice',
    );
    await act(() =>
      button.dispatchEvent(
        new window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true }),
      ),
    );
    assert.equal(document.querySelector('[role="tooltip"]'), null);
    assert.equal(document.activeElement, button);
  } finally {
    await view.unmount();
  }
});
