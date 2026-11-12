import assert from 'node:assert/strict';
import test from 'node:test';
import { act, createElement as h } from 'react';
import { FilePreview } from '@openviking/react/components/file-preview';
import { mount } from './helpers/module-dom.mjs';
const source = {
  content: '',
  fileName: 'guide.md',
  path: 'viking://resources/guide.md',
};
const action = (key) => ({
  key,
  icon: h('svg'),
  label: key,
  onClick: () => key,
});
const tree = (actions, extra = {}, props = {}) =>
  h(FilePreview, {
    tabs: [{ kind: 'file', key: 'guide', source, actions, ...extra }],
    ...props,
  });
const button = (label) =>
  document.querySelector(`button[aria-label="${label}"]`);
const click = async (node) => {
  assert.ok(node);
  await act(async () => node.click());
};
test('path copying is independent of injected actions and host visibility is a master switch', async () => {
  let copied;
  let fail = true;
  Object.defineProperty(navigator, 'clipboard', {
    configurable: true,
    value: {
      writeText: async (value) => {
        if (fail) throw new Error('Denied');
        copied = value;
      },
    },
  });
  const view = await mount(tree([]));
  try {
    await click(button('Copy path'));
    assert.match(
      document.querySelector('[role="status"]').textContent,
      /Could not copy/,
    );
    assert.equal(button('Copy path').disabled, false);
    fail = false;
    await click(button('Copy path'));
    assert.equal(copied, source.path);
    assert.match(
      document.querySelector('[role="status"]').textContent,
      /Copied/,
    );
    await view.render(
      tree(
        [action('Run')],
        { toolbar: { copyPath: true } },
        { toolbar: { copyPath: false } },
      ),
    );
    assert.equal(button('Copy path'), null);
    assert.ok(button('Run'));
    await view.render(tree([], { source: { ...source, path: undefined } }));
    assert.equal(button('Copy path'), null);
  } finally {
    await view.unmount();
  }
});
