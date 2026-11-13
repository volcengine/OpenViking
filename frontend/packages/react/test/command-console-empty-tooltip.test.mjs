import assert from 'node:assert/strict';
import { test } from 'node:test';
import { mount, act, dom } from './helpers/command-console-dom.mjs';

// Tooltip 定位 hook 依赖这两个观察器，jsdom 只提供 MutationObserver。
// The Tooltip's positioning hook needs both observers; jsdom ships only one.
globalThis.MutationObserver = dom.window.MutationObserver;
globalThis.ResizeObserver = class {
  observe() {}
  unobserve() {}
  disconnect() {}
};

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
