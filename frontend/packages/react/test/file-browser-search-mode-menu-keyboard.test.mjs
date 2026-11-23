import assert from 'node:assert/strict';
import test from 'node:test';
import { act, createElement as h } from 'react';
import { FileBrowser } from '@openviking/react/modules/file-browser';
import { OpenVikingProvider } from '@openviking/react/client';
import { mount, ok, recordingFetch, settle } from './helpers/module-dom.mjs';
for (const name of ['HTMLIFrameElement', 'SVGElement'])
  globalThis[name] = window[name];
globalThis.getComputedStyle = window.getComputedStyle.bind(window);

const find = '/api/v1/search/find';
const uri = 'viking://resources/profile.md';
const modeSwitch = () =>
  document.querySelector(
    '.ov-file-browser-search__summary button[role="combobox"]',
  );
const items = () => [
  ...document.querySelectorAll('.ov-file-browser-search__menu-item'),
];
const key = (target, name) =>
  act(async () =>
    target.dispatchEvent(
      new window.KeyboardEvent('keydown', {
        bubbles: true,
        cancelable: true,
        key: name,
      }),
    ),
  );

async function openWithResults() {
  const fetch = recordingFetch((call) => {
    if (call.path === find) return ok({ resources: [{ uri }] });
    if (call.path === '/api/v1/search/grep') return ok({ matches: [] });
    return ok([]);
  });
  const view = await mount(
    h(
      OpenVikingProvider,
      { fetch, baseURL: 'https://ov.test' },
      h(FileBrowser, { rootUri: 'viking://resources' }),
    ),
  );
  await act(async () =>
    view.container.querySelector('.ov-file-browser__search-trigger').click(),
  );
  await settle();
  const input = document.querySelector('.ov-file-browser-search__input');
  await act(async () => {
    Object.getOwnPropertyDescriptor(
      window.HTMLInputElement.prototype,
      'value',
    ).set.call(input, 'profile');
    input.dispatchEvent(new window.Event('input', { bubbles: true }));
  });
  await act(async () => new Promise((resolve) => setTimeout(resolve, 450)));
  await settle();
  return view;
}

/**
 * 键盘打开模式菜单时焦点进入当前项，选择模式或按 Esc 后回到模式选择器。
 *
 * 菜单 portal 到 `document.body` 后不在弹窗的 DOM 子树里，只靠 `FocusLock` 的
 * shard 登记也能 Tab 到，但打开那一刻没有焦点反馈。这里断言的是真实的键盘路径，
 * 而不只是按钮存在。
 *
 * Keyboard opening focuses the current item. Selection or Escape restores the trigger.
 *
 * A portaled menu sits outside the dialog's DOM subtree; the shard registration makes it
 * reachable by Tab, but without moving focus on open a keyboard user gets no feedback.
 * This asserts the real keyboard path, not merely that the buttons exist.
 */
test('the mode menu takes focus on open and returns it on close', async () => {
  const view = await openWithResults();
  try {
    modeSwitch().focus();
    await key(modeSwitch(), 'Enter');
    await settle();
    assert.equal(items().length, 4);
    assert.equal(
      document.activeElement,
      items().find((item) => item.getAttribute('aria-selected') === 'true'),
    );

    await act(async () => items()[2].click());
    await settle();
    assert.equal(items().length, 0);
    assert.equal(document.activeElement, modeSwitch());
    assert.match(modeSwitch().getAttribute('aria-label'), /grep/);

    await key(modeSwitch(), 'ArrowDown');
    await settle();
    // Select reopens on the current value, preserving the user's selection context.
    assert.equal(
      document.activeElement,
      items().find((item) => item.getAttribute('aria-selected') === 'true'),
    );
    await key(items()[0], 'Escape');
    await settle();
    assert.equal(items().length, 0);
    assert.equal(document.activeElement, modeSwitch());
    // Esc 只关菜单，搜索弹窗仍开着。
    // Escape closes the menu only; the search dialog stays open.
    assert.ok(document.querySelector('.ov-file-browser-search'));
  } finally {
    await view.unmount();
  }
});

test('clicking the switch again toggles the menu closed', async () => {
  const view = await openWithResults();
  // 真实鼠标按下会先派发 pointerdown，再派发 click。只用 `element.click()` 会跳过
  // pointerdown，测不到「先关再开」的回归。
  //
  // A real press dispatches pointerdown before click; `element.click()` skips
  // pointerdown, so it cannot catch the close-then-reopen regression.
  const press = async (target) => {
    await act(async () => {
      target.dispatchEvent(
        new window.PointerEvent('pointerdown', { bubbles: true }),
      );
    });
    await act(async () => target.click());
    await settle();
  };
  try {
    await press(modeSwitch());
    assert.equal(items().length, 4);

    await press(modeSwitch());
    assert.equal(items().length, 0);
    assert.equal(modeSwitch().getAttribute('aria-expanded'), 'false');

    // 再点一次仍能打开，切换是双向的。
    // Pressing once more opens it again; the toggle works both ways.
    await press(modeSwitch());
    assert.equal(items().length, 4);
  } finally {
    await view.unmount();
  }
});

test('the portaled menu is part of the modal tab order', async () => {
  const view = await openWithResults();
  try {
    modeSwitch().focus();
    await key(modeSwitch(), 'Enter');
    await settle();
    // 菜单挂在 body 上，却登记进了弹窗的焦点范围：焦点在菜单项上时，弹窗的
    // `FocusLock` 不会把焦点抢回去。
    //
    // The menu lives on body yet is registered into the dialog's focus scope: while
    // focus sits on a menu item, the dialog's `FocusLock` does not pull it back.
    items()[1].focus();
    await settle();
    assert.equal(document.activeElement, items()[1]);
    assert.equal(
      document.querySelector('.ov-file-browser-search').contains(items()[1]),
      false,
    );
  } finally {
    await view.unmount();
  }
});
