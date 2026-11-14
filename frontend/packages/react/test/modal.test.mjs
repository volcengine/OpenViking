import assert from 'node:assert/strict';
import test from 'node:test';
import { createElement as h, useRef, useState, act } from 'react';
import { Modal, FileBrowserActionBar } from './helpers/internal-hooks.mjs';
import { mount, settle } from './helpers/module-dom.mjs';
for (const name of ['MutationObserver', 'HTMLIFrameElement', 'SVGElement'])
  globalThis[name] = window[name];
globalThis.getComputedStyle = window.getComputedStyle.bind(window);
const click = async (element) => {
  await act(() => {
    element.focus();
    element.click();
  });
  await settle();
};
const finish = () =>
  act(() => new Promise((resolve) => setTimeout(resolve, 350)));
const panel = () => document.querySelector('.ov-modal [role="dialog"]');
function Host({ locale, closeLabel, onIntent, blocked = false }) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);
  return h(
    'div',
    null,
    h('button', { ref, onClick: () => setOpen(true) }, 'Open'),
    h(
      Modal,
      {
        open,
        title: 'Settings',
        triggerRef: ref,
        locale,
        closeLabel,
        onClose: () => {
          onIntent?.();
          if (!blocked) setOpen(false);
        },
      },
      h('h2', null, 'Settings'),
      h('input', {
        'aria-label': 'Name',
        'data-autofocus': '',
        defaultValue: 'Draft',
        onKeyDown: (event) => {
          if (event.key === 'Escape') event.preventDefault();
        },
      }),
    ),
  );
}

test('internal Modal preserves scoped themes and exit-safe focus/scroll lifetime', async () => {
  const overflow = document.body.style.overflow;
  const view = await mount(h(Host));
  try {
    const trigger = view.container.querySelector('button');
    trigger.style.setProperty('--ov-ui-color-bg-1', 'rgb(20, 30, 40)');
    await click(trigger);
    assert.ok(panel() && !view.container.contains(panel()));
    assert.equal(document.querySelector('dialog'), null);
    assert.equal(
      panel().closest('.ov-modal').style.getPropertyValue('--ov-ui-color-bg-1'),
      'rgb(20, 30, 40)',
    );
    assert.equal(document.activeElement.getAttribute('aria-label'), 'Name');
    assert.equal(document.body.style.overflowY, 'hidden');
    const previous = panel();
    await click(panel().querySelector('.ov-modal__close'));
    assert.equal(panel(), previous);
    assert.equal(document.body.style.overflowY, 'hidden');
    await finish();
    assert.equal(panel(), null);
    assert.equal(document.body.style.overflow, overflow);
    assert.equal(document.activeElement, trigger);
    await click(trigger);
    await click(document.querySelector('.ov-modal__mask'));
    await finish();
    assert.equal(panel(), null);
    await click(trigger);
    await act(() =>
      panel()
        .querySelector('input')
        .dispatchEvent(
          new window.KeyboardEvent('keydown', {
            key: 'Escape',
            bubbles: true,
            cancelable: true,
          }),
        ),
    );
    assert.ok(panel(), 'a child that handles Escape keeps the modal open');
    await act(() =>
      panel()
        .querySelector('.ov-modal__close')
        .dispatchEvent(
          new window.KeyboardEvent('keydown', {
            key: 'Escape',
            bubbles: true,
            cancelable: true,
          }),
        ),
    );
    await finish();
    assert.equal(panel(), null);
  } finally {
    await view.unmount();
  }
});

test('controlled close intent may be blocked and locale changes retain content', async () => {
  const intents = [];
  const onIntent = () => intents.push('close');
  const view = await mount(h(Host, { blocked: true, onIntent }));
  try {
    await click(view.container.querySelector('button'));
    const input = panel().querySelector('input');
    input.value = 'Unsaved content';
    await click(panel().querySelector('.ov-modal__close'));
    await click(document.querySelector('.ov-modal__mask'));
    assert.equal(intents.length, 2);
    assert.ok(panel());
    await view.render(h(Host, { blocked: true, onIntent, locale: 'zh-CN' }));
    assert.equal(
      panel().querySelector('.ov-modal__close').getAttribute('aria-label'),
      '关闭弹窗',
    );
    assert.equal(panel().querySelector('input'), input);
    assert.equal(input.value, 'Unsaved content');
    await view.render(
      h(Host, { blocked: true, onIntent, locale: 'zh-CN', closeLabel: '' }),
    );
    assert.equal(
      panel().querySelector('.ov-modal__close').getAttribute('aria-label'),
      '',
    );
  } finally {
    await view.unmount();
  }
});

for (const removedFirst of ['Modal', 'Drawer']) {
  test(`Modal and Drawer keep a shared scroll lock when ${removedFirst} leaves first`, async () => {
    const original = document.body.style.cssText;
    document.body.style.setProperty('overflow-x', 'scroll', 'important');
    document.body.style.setProperty('padding-right', '7px', 'important');
    const overflowPriority =
      document.body.style.getPropertyPriority('overflow-x');
    const pair = (modalOpen, showDrawer = true) =>
      h(
        'div',
        null,
        showDrawer
          ? h(FileBrowserActionBar, {
              key: 'entry',
              agentConnect: { apiKey: 'demo-key' },
            })
          : null,
        h(
          Modal,
          {
            key: 'modal',
            open: modalOpen,
            title: 'Overlay',
            triggerRef: { current: null },
            onClose: () => {},
          },
          h('button', null, 'Content'),
        ),
      );
    const view = await mount(pair(false));
    try {
      await click(view.container.querySelector('button'));
      await view.render(pair(true));
      assert.equal(document.querySelectorAll('[aria-modal="true"]').length, 2);
      await view.render(
        pair(removedFirst !== 'Modal', removedFirst !== 'Drawer'),
      );
      await finish();
      assert.equal(document.querySelectorAll('[aria-modal="true"]').length, 1);
      assert.equal(document.body.style.overflowY, 'hidden');
    } finally {
      await view.unmount();
      assert.equal(document.body.style.overflowX, 'scroll');
      assert.equal(
        document.body.style.getPropertyPriority('overflow-x'),
        overflowPriority,
      );
      assert.equal(document.body.style.paddingRight, '7px');
      assert.equal(
        document.body.style.getPropertyPriority('padding-right'),
        'important',
      );
      document.body.style.cssText = original;
    }
  });
}
