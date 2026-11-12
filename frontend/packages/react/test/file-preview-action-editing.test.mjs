import assert from 'node:assert/strict';
import test from 'node:test';
import { act, createElement as h } from 'react';
import { FilePreview } from '@openviking/react/components/file-preview';
import { mount } from './helpers/module-dom.mjs';
const button = (name) =>
  [...document.querySelectorAll('button')].find(
    (node) =>
      node.getAttribute('aria-label') === name || node.textContent === name,
  );
const click = async (node) => {
  assert.ok(node);
  await act(async () => node.click());
};
for (const fails of [false, true]) {
  test(`command lock survives editing and cancellation (${fails ? 'rejection' : 'success'})`, async () => {
    let calls = 0,
      resolve,
      reject;
    const waiting = new Promise((yes, no) => {
      resolve = yes;
      reject = no;
    });
    const view = await mount(
      h(FilePreview, {
        tabs: [
          {
            kind: 'file',
            key: 'a',
            source: { fileName: 'a.md', content: '' },
            editing: { save: async () => {} },
            actions: [
              {
                key: 'run',
                icon: h('svg'),
                label: 'Run',
                onClick: () => (++calls === 1 ? waiting : 42),
              },
            ],
          },
        ],
      }),
    );
    try {
      await click(button('Run'));
      await click(button('Edit'));
      assert.ok(document.querySelector('textarea'));
      await click(button('Cancel'));
      await click(button('Run'));
      assert.equal(calls, 1);
      assert.equal(button('Run').disabled, true);
      await act(async () =>
        fails ? reject(new Error('Retry me')) : resolve(42),
      );
      assert.equal(button('Run').disabled, false);
      if (fails)
        assert.match(
          document.querySelector('[role="alert"]').textContent,
          /Retry me/,
        );
      await click(button('Run'));
      assert.equal(calls, 2);
      assert.equal(document.querySelector('[role="alert"]'), null);
    } finally {
      resolve();
      await view.unmount();
    }
  });
}
