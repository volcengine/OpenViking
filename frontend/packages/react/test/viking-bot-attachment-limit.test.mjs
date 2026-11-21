import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createElement as h, useState, act } from 'react';
import { pathToFileURL } from 'node:url';
import { join } from 'node:path';
import { mount } from './helpers/module-dom.mjs';

const { ComposerFooter, ComposerAttachments, resolveVikingBotLabels } =
  await import(
    pathToFileURL(join(process.env.OV_UI_TEST_HOOKS, 'bot-composer-tools.js'))
  );

async function showHint(element, interaction = 'hover') {
  await act(async () => {
    if (interaction === 'focus') element.focus();
    else
      element.dispatchEvent(
        new window.MouseEvent('mouseover', { bubbles: true }),
      );
    await new Promise((resolve) => setTimeout(resolve, 350));
  });
}

async function renderTools({ locale, limit, labels: overrides }) {
  let receivedFiles;
  function Host() {
    const [attachments, setAttachments] = useState(
      Array.from({ length: limit }, (_, index) => ({
        key: String(index),
        name: `document-${index}.txt`,
      })),
    );
    const labels = resolveVikingBotLabels(locale, overrides);
    return h(
      'div',
      null,
      h(ComposerAttachments, {
        items: attachments,
        labels,
        onRemove: ({ key }) =>
          setAttachments((items) => items.filter((item) => item.key !== key)),
      }),
      h(ComposerFooter, {
        locale,
        labels,
        onFiles: (files) => {
          receivedFiles = files;
        },
        filesDisabled: attachments.length === limit,
        attachmentLimit: attachments.length === limit ? limit : undefined,
      }),
    );
  }
  const view = await mount(h(Host));
  return {
    ...view,
    get receivedFiles() {
      return receivedFiles;
    },
  };
}

for (const [locale, limit, hint, attach, remove] of [
  ['zh-CN', 10, '单次最多上传 10 个附件', '添加资源', '移除附件'],
  [
    'en-US',
    10,
    'Upload up to 10 attachments per message',
    'Add resources',
    'Remove attachment',
  ],
  ['zh-CN', 3, '单次最多上传 3 个附件', '添加资源', '移除附件'],
  [
    'en-US',
    3,
    'Upload up to 3 attachments per message',
    'Add resources',
    'Remove attachment',
  ],
])
  test(`${locale} attachment tools explain the ${limit}-file limit and recover after removal`, async () => {
    const view = await renderTools({ locale, limit });
    try {
      const findButton = () =>
        view.container.querySelector(`button[aria-label="${attach}"]`);
      let button = findButton();
      assert.ok(button, 'the real composer footer renders its add-file button');
      assert.equal(button.disabled, true);
      await showHint(button.parentElement, limit === 10 ? 'focus' : 'hover');
      assert.equal(
        document.querySelector('[role="tooltip"]')?.textContent,
        hint,
      );
      const input = view.container.querySelector('input[type="file"]');
      let pickerOpened = 0;
      input.addEventListener('click', () => {
        pickerOpened += 1;
      });
      await act(() => button.click());
      assert.equal(pickerOpened, 0, 'the full native button remains disabled');
      await act(() =>
        view.container
          .querySelector(`button[aria-label="${remove}: document-0.txt"]`)
          .click(),
      );
      button = findButton();
      assert.equal(button.disabled, false);
      await showHint(button);
      assert.equal(
        document.querySelector('[role="tooltip"]')?.textContent,
        attach,
      );
      await act(() => button.click());
      assert.equal(
        pickerOpened,
        1,
        'the restored button opens the file picker',
      );
      const file = new window.File(['contents'], 'next.txt');
      Object.defineProperty(input, 'files', {
        value: [file],
        configurable: true,
      });
      await act(() =>
        input.dispatchEvent(new window.Event('change', { bubbles: true })),
      );
      assert.deepEqual(view.receivedFiles, [file]);
    } finally {
      await view.unmount();
    }
  });

test('the attachment limit hint accepts a host label formatter', async () => {
  const view = await renderTools({
    locale: 'en-US',
    limit: 2,
    labels: { attachmentLimit: (limit) => `Maximum ${limit} files` },
  });
  try {
    const button = view.container.querySelector(
      'button[aria-label="Add resources"]',
    );
    await showHint(button.parentElement);
    assert.equal(
      document.querySelector('[role="tooltip"]')?.textContent,
      'Maximum 2 files',
    );
    assert.equal(button.disabled, true);
  } finally {
    await view.unmount();
  }
});

test('an open attachment hint stays anchored when removing a file', async () => {
  const originalRect = HTMLElement.prototype.getBoundingClientRect;
  HTMLElement.prototype.getBoundingClientRect = function () {
    return this.isConnected &&
      (this.matches('.ov-viking-bot__attach, .ov-viking-bot__attach-trigger') ||
        this.getAttribute('role') === 'tooltip')
      ? {
          x: 100,
          y: 100,
          top: 100,
          left: 100,
          right: 132,
          bottom: 132,
          width: 32,
          height: 32,
        }
      : originalRect.call(this);
  };
  const view = await renderTools({ locale: 'zh-CN', limit: 10 });
  try {
    const button = view.container.querySelector(
      'button[aria-label="添加资源"]',
    );
    const trigger = button.parentElement;
    await showHint(trigger);
    const tooltip = () => document.querySelector('[role="tooltip"]');
    assert.equal(tooltip().style.top, '62px');
    assert.equal(trigger.getAttribute('aria-describedby'), tooltip().id);
    await act(() =>
      view.container
        .querySelector('button[aria-label="移除附件: document-0.txt"]')
        .click(),
    );
    await act(() => window.dispatchEvent(new window.Event('scroll')));
    assert.equal(tooltip().textContent, '添加资源');
    assert.equal(tooltip().style.top, '62px');
    assert.equal(tooltip().style.left, '100px');
    await act(() =>
      document.dispatchEvent(
        new window.KeyboardEvent('keydown', { key: 'Escape' }),
      ),
    );
    assert.equal(tooltip(), null);
  } finally {
    await view.unmount();
    HTMLElement.prototype.getBoundingClientRect = originalRect;
  }
});
