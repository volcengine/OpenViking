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
      document.querySelector('.ov-toast--error').textContent,
      /Could not copy/,
    );
    assert.equal(button('Copy path').disabled, false);
    fail = false;
    await click(button('Copy path'));
    assert.equal(copied, source.path);
    assert.match(
      document.querySelector('.ov-path-copy-status[role="status"]').textContent,
      /Node path copied/,
    );
    await view.render(
      tree(
        [action('Run')],
        { toolbar: { copyPath: true } },
        { toolbar: { copyPath: false } },
      ),
    );
    assert.equal(Boolean(button('Copy path')), false);
    assert.ok(button('Run'));
    await view.render(tree([], { source: { ...source, path: undefined } }));
    assert.equal(Boolean(button('Copy path')), false);
  } finally {
    await view.unmount();
  }
});

for (const [path, expected] of [
  [
    'viking://resources/%E4%B8%AD%E6%96%87/报告.md',
    'viking://resources/中文/报告.md',
  ],
  [
    'viking://resources/%E4%B8%AD%2F%23%3F%25%5C.md',
    'viking://resources/中%2F%23%3F%25%5C.md',
  ],
  [
    'viking://resources/%E4%B8%AD%3A%5B%5d%40%21%24%26%27%28%29%2A%2B%2C%3B%3D.md',
    'viking://resources/中%3A%5B%5d%40%21%24%26%27%28%29%2A%2B%2C%3B%3D.md',
  ],
  ['viking://resources/bad%E4%xx.md', 'viking://resources/bad%E4%xx.md'],
  ['/tmp/report%20name.md', '/tmp/report%20name.md'],
]) {
  test(`copy path formats readable text without changing resource identity: ${path}`, async () => {
    let copied;
    let notified;
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: {
        writeText: async (value) => {
          copied = value;
        },
      },
    });
    const view = await mount(
      tree([], {
        source: { ...source, path },
        onCopyPath: (value) => {
          notified = value;
        },
      }),
    );
    try {
      await click(button('Copy path'));
      assert.equal(copied, expected);
      assert.equal(notified, expected);
    } finally {
      await view.unmount();
    }
  });
}
