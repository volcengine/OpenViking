import assert from 'node:assert/strict';
import { test } from 'node:test';
import { mount, act, dom } from './helpers/command-console-dom.mjs';

// Tooltip 定位 hook 需要 MutationObserver；jsdom 只提供它，ResizeObserver 由
// helper 装了空替身。这里换成能手动触发回调的版本，用来模拟容器尺寸变化。
//
// The Tooltip's positioning hook needs MutationObserver, which jsdom provides;
// the helper stubs ResizeObserver. This file swaps in one whose callbacks can be
// fired by hand to simulate a container resize.
globalThis.MutationObserver = dom.window.MutationObserver;
const resizeCallbacks = new Set();
globalThis.ResizeObserver = class {
  constructor(callback) {
    this.callback = callback;
    resizeCallbacks.add(callback);
  }
  observe() {}
  unobserve() {}
  disconnect() {
    resizeCallbacks.delete(this.callback);
  }
};
/** 触发所有 ResizeObserver 回调，模拟容器尺寸变化。 Fires the observers. */
const resize = () =>
  act(() => {
    for (const callback of [...resizeCallbacks]) callback([]);
  });

/**
 * jsdom 没有布局，`scrollWidth` 与 `clientWidth` 恒为 0。按元素文本长度给
 * `scrollWidth` 一个值，`clientWidth` 由调用方指定，即可模拟截断与不截断。
 *
 * jsdom has no layout, so `scrollWidth` and `clientWidth` are both 0. Give
 * `scrollWidth` a value derived from the text length and `clientWidth` a fixed
 * one to simulate truncated and untruncated descriptions.
 */
function stubTextWidths(clientWidth) {
  const proto = dom.window.HTMLElement.prototype;
  const originalScroll = Object.getOwnPropertyDescriptor(proto, 'scrollWidth');
  const originalClient = Object.getOwnPropertyDescriptor(proto, 'clientWidth');
  Object.defineProperty(proto, 'scrollWidth', {
    configurable: true,
    get() {
      return (this.textContent?.length ?? 0) * 8;
    },
  });
  Object.defineProperty(proto, 'clientWidth', {
    configurable: true,
    get: () => clientWidth,
  });
  return () => {
    if (originalScroll)
      Object.defineProperty(proto, 'scrollWidth', originalScroll);
    if (originalClient)
      Object.defineProperty(proto, 'clientWidth', originalClient);
  };
}

const commands = [
  {
    name: 'ls',
    description: 'List directory entries',
    usage: 'ov ls [uri]',
  },
  {
    name: 'find',
    summary: 'Find matching context',
    description: 'Unused when a summary exists',
    usage: 'ov find <query>',
  },
];

const actions = (container) => [
  ...container.querySelectorAll('.ov-command-console__empty-action'),
];

/**
 * 悬停并等过 Tooltip 的 300ms 延迟，返回浮层文本。
 *
 * React 由冒泡的 mouseover 合成 onMouseEnter，不监听 mouseenter 本身。
 *
 * Hovers past the Tooltip's 300ms delay and returns the surface text.
 *
 * React synthesises `onMouseEnter` from a bubbling `mouseover`, not from
 * `mouseenter` itself.
 */
async function hover(button) {
  await act(() =>
    button.dispatchEvent(
      new dom.window.MouseEvent('mouseover', { bubbles: true }),
    ),
  );
  await act(() => new Promise((resolve) => setTimeout(resolve, 340)));
  return document.querySelector('[role="tooltip"]')?.textContent ?? null;
}

test('a row shows its description on hover only when a summary hides it', async () => {
  const withSummary = await mount({
    commands,
    quickCommands: [{ name: 'find', value: '' }],
  });
  try {
    assert.equal(
      await hover(actions(withSummary.container)[0]),
      'Unused when a summary exists',
    );
  } finally {
    await withSummary.dispose();
  }
  const withoutSummary = await mount({
    commands,
    quickCommands: [{ name: 'ls', value: '' }],
  });
  try {
    assert.equal(await hover(actions(withoutSummary.container)[0]), null);
  } finally {
    await withoutSummary.dispose();
  }
});

test('a truncated description keeps its tooltip without a summary', async () => {
  // 'List directory entries' 是 22 个字符，自然宽度 176px；容器给 80px 便截断。
  // The 22-character description measures 176px; an 80px container truncates it.
  const restore = stubTextWidths(80);
  const view = await mount({
    commands,
    quickCommands: [{ name: 'ls', value: '' }],
  });
  try {
    assert.equal(
      await hover(actions(view.container)[0]),
      'List directory entries',
    );
  } finally {
    await view.dispose();
    restore();
  }
});

test('a description that fits keeps no tooltip and gains one when truncated', async () => {
  const restore = stubTextWidths(1000);
  const view = await mount({
    commands,
    quickCommands: [{ name: 'ls', value: '' }],
  });
  try {
    assert.equal(await hover(actions(view.container)[0]), null);
    restore();
    // 容器收窄后重新测量，Tooltip 随之出现。
    // Re-measuring after the container narrows brings the tooltip back.
    stubTextWidths(80);
    await resize();
    assert.equal(
      await hover(actions(view.container)[0]),
      'List directory entries',
    );
    // 恢复宽度后说明完整可见，Tooltip 撤销。
    // Widening again makes the whole description visible, so the tooltip goes.
    restore();
    stubTextWidths(1000);
    await resize();
    assert.equal(await hover(actions(view.container)[0]), null);
  } finally {
    await view.dispose();
    restore();
  }
});
