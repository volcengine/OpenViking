import assert from 'node:assert/strict';
import { join } from 'node:path';
import { test } from 'node:test';
import { pathToFileURL } from 'node:url';
import { act, createElement } from 'react';
import { mount } from './helpers/module-dom.mjs';

const { ReferencePicker } = await import(
  pathToFileURL(join(process.env.OV_UI_TEST_HOOKS, 'reference-picker.js'))
);

const parentId = 'viking://resources/wiki';
for (const [kind, label, handler] of [
  ['load-more', 'Load more', 'onLoadMore'],
  ['error', 'Retry', 'onRetry'],
]) {
  test(`the reference picker invokes ${label} from the FileTree row button once while pending`, async () => {
    const calls = [];
    const { promise, resolve } = Promise.withResolvers();
    const view = await mount(
      createElement(ReferencePicker, {
        config: {
          rows: [{ kind, id: `${parentId}/${kind}`, parentId, depth: 1 }],
          selected: [],
          onChange() {},
          [handler](id) {
            calls.push(id);
            return promise;
          },
        },
        locale: 'en-US',
        onSelect() {},
      }),
    );
    try {
      const button = view.container.querySelector('.ov-file-tree__action');
      assert.equal(button.textContent, label);
      assert.equal(button.disabled, false);
      await act(() => button.click());
      assert.deepEqual(calls, [parentId]);

      const input = view.container.querySelector('input[type="search"]');
      const pressEnter = () =>
        input.dispatchEvent(
          new window.KeyboardEvent('keydown', {
            key: 'Enter',
            bubbles: true,
            cancelable: true,
          }),
        );
      await act(() => {
        pressEnter();
        button.click();
      });
      assert.deepEqual(calls, [parentId]);

      await act(() => resolve());
      await act(() => void pressEnter());
      assert.deepEqual(calls, [parentId, parentId]);
    } finally {
      await view.unmount();
    }
  });
}
