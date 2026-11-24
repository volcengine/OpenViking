import { notificationError } from './helpers/feedback-dom.mjs';
import { waitForEditor } from './helpers/preview-editor-dom.mjs';
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
      await waitForEditor(view.container);
      assert.ok(document.querySelector('.cm-content'));
      await click(button('Cancel'));
      await click(button('Run'));
      assert.equal(calls, 1);
      assert.equal(button('Run').disabled, true);
      await act(async () =>
        fails ? reject(new Error('Retry me')) : resolve(42),
      );
      assert.equal(button('Run').disabled, false);
      if (fails) assert.match(notificationError().textContent, /Retry me/);
      await click(button('Run'));
      assert.equal(calls, 2);
      assert.equal(Boolean(notificationError()), false);
    } finally {
      resolve();
      await view.unmount();
    }
  });
}

test('settling one resource does not unlock a different pending resource', async () => {
  let resolveA, resolveB;
  const a = new Promise((resolve) => {
    resolveA = resolve;
  });
  const b = new Promise((resolve) => {
    resolveB = resolve;
  });
  const render = (activeKey) =>
    h(FilePreview, {
      activeKey,
      tabs: ['a', 'b'].map((key) => ({
        kind: 'file',
        key,
        source: {
          fileName: key + '.md',
          path: 'viking://resources/' + key + '.md',
          content: '',
        },
        actions: [
          {
            key: 'run',
            label: 'Run ' + key,
            icon: h('svg'),
            onClick: () => (key === 'a' ? a : b),
          },
        ],
      })),
    });
  const view = await mount(render('a'));
  try {
    await click(button('Run a'));
    await view.render(render('b'));
    await click(button('Run b'));
    assert.equal(button('Run b').disabled, true);
    await act(async () => resolveA());
    assert.equal(button('Run b').disabled, true);
    await view.render(render('a'));
    assert.equal(button('Run a').disabled, false);
    await view.render(render('b'));
    assert.equal(button('Run b').disabled, true);
    await act(async () => resolveB());
    assert.equal(button('Run b').disabled, false);
  } finally {
    resolveA();
    resolveB();
    await view.unmount();
  }
});
