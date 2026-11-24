import assert from 'node:assert/strict';
import test from 'node:test';
import { act, createElement as h } from 'react';
import { FilePreview } from '@openviking/react/components/file-preview';
import { mount } from './helpers/module-dom.mjs';

const uri = 'viking://resources/中文目录/guide.md';
const button = () => document.querySelector('.ov-path-copy-button');
const status = () =>
  document.querySelector('.ov-path-copy-status[role="status"]');
const feedback = () => document.querySelector('.ov-path-copy-feedback');
const copy = () => act(async () => button().click());
const wait = (delay) =>
  act(async () => new Promise((resolve) => setTimeout(resolve, delay)));
const clipboard = (writeText) =>
  Object.defineProperty(navigator, 'clipboard', {
    configurable: true,
    value: { writeText },
  });
const preview = (kind, path = uri, props = {}) => {
  const source = { content: '', fileName: 'guide.md', path };
  return h(FilePreview, {
    tabs: [
      kind === 'directory'
        ? {
            kind,
            key: 'guide',
            levels: [{ key: 'l0', label: 'L0', source }],
          }
        : { kind, key: 'guide', source },
    ],
    ...props,
  });
};

for (const kind of ['file', 'directory']) {
  test(`${kind} path feedback waits for clipboard success and ignores old-path results`, async () => {
    let complete;
    let copied;
    clipboard((value) => {
      copied = value;
      return new Promise((resolve) => (complete = resolve));
    });
    const view = await mount(preview(kind));
    try {
      const liveRegion = status();
      assert.equal(liveRegion.textContent, '');
      await copy();
      assert.equal(feedback(), null);
      assert.equal(button().hasAttribute('data-copied'), false);
      await act(async () => complete());
      assert.equal(copied, uri);
      assert.equal(feedback()?.textContent.trim(), 'Node path copied');
      assert.equal(status(), liveRegion);
      assert.equal(liveRegion.textContent.trim(), 'Node path copied');
      assert.equal(feedback().getAttribute('aria-hidden'), 'true');
      assert.equal(document.querySelector('.ov-path-copy-hint'), null);
      await copy();
      assert.equal(feedback(), null);
      assert.equal(button().hasAttribute('data-copied'), false);
      assert.equal(liveRegion.textContent, '');
      await view.render(preview(kind, 'viking://resources/next.md'));
      assert.equal(feedback(), null);
      await act(async () => complete());
      assert.equal(feedback(), null);
      assert.equal(button().hasAttribute('data-copied'), false);
    } finally {
      await view.unmount();
    }
  });
}

test('successful feedback follows language and explicit label changes, including empty text', async () => {
  clipboard(async () => {});
  const view = await mount(preview('file'));
  try {
    await copy();
    await view.render(preview('file', uri, { locale: 'zh-CN' }));
    assert.equal(feedback()?.textContent.trim(), '节点路径已复制');
    await view.render(
      preview('file', uri, { labels: { copied: 'Custom path copied' } }),
    );
    assert.equal(feedback()?.textContent.trim(), 'Custom path copied');
    await view.render(preview('file', uri, { labels: { copied: '' } }));
    assert.equal(feedback()?.textContent, '');
    assert.equal(button().getAttribute('data-copied'), 'true');
    await act(async () =>
      button().dispatchEvent(
        new window.KeyboardEvent('keydown', {
          key: 'Escape',
          bubbles: true,
        }),
      ),
    );
    assert.equal(feedback(), null);
    assert.equal(button().disabled, false);
  } finally {
    await view.unmount();
  }
});

test('a repeated successful click renews the 1.5 second feedback timer', async () => {
  clipboard(async () => {});
  const view = await mount(preview('file'));
  try {
    await copy();
    await wait(1000);
    await copy();
    await wait(650);
    assert.ok(feedback());
    await wait(900);
    assert.equal(feedback(), null);
    assert.equal(button().hasAttribute('data-copied'), false);
  } finally {
    await view.unmount();
  }
});
