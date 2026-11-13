import { act, dom } from './command-console-dom.mjs';

// Tooltip 定位 hook 需要 MutationObserver；jsdom 只提供它，ResizeObserver 由
// command-console-dom 装了空替身。这里换成能记录观察目标、按目标手动派发的版本：
// 只向确实被观察且仍在文档里的节点派发，否则「观察器绑在已卸载节点上」这类问题
// 测不出来。
//
// The Tooltip's positioning hook needs MutationObserver, which jsdom provides;
// command-console-dom stubs ResizeObserver. This one records its targets and
// dispatches per target: it only fires for a target that is still observed and
// still in the document, so a subscription bound to an unmounted node cannot pass
// silently.
globalThis.MutationObserver = dom.window.MutationObserver;

/** 每个观察器实例及其目标。 Each observer instance and its targets. */
const observers = new Set();
globalThis.ResizeObserver = class {
  constructor(callback) {
    this.callback = callback;
    this.targets = new Set();
    observers.add(this);
  }
  observe(target) {
    this.targets.add(target);
  }
  unobserve(target) {
    this.targets.delete(target);
  }
  disconnect() {
    this.targets.clear();
    observers.delete(this);
  }
};

/**
 * 向仍被某个观察器盯着、且仍在文档里的列表派发一次尺寸变化。
 *
 * Dispatches one resize to every list that is still observed and still attached.
 */
export const resize = () =>
  act(() => {
    for (const observer of [...observers]) {
      const live = [...observer.targets].filter((t) => t.isConnected);
      if (live.length) observer.callback([]);
    }
  });

/** 当前仍被观察、且在文档里的列表数量。 Live observed lists. */
export const observedLists = () =>
  [...observers].reduce(
    (count, observer) =>
      count + [...observer.targets].filter((t) => t.isConnected).length,
    0,
  );

// 说明节点的可用宽度，测试里按需改。The width a description node reports.
let descClientWidth = 1000;

// 只让说明节点报告宽度，且脱离 DOM 后一律为 0：真实浏览器里脱离文档的节点没有
// 布局盒。把宽度装在原型上会让任何元素都报宽度，测不出「测量盯着已卸载节点」这
// 类问题。
//
// Only description nodes report a width, and a detached node reports 0: a node
// outside the document has no layout box in a real browser. Putting the width on
// the prototype would make every element report one and hide regressions where the
// measurement holds an unmounted node.
{
  const proto = dom.window.HTMLElement.prototype;
  Object.defineProperty(proto, 'clientWidth', {
    configurable: true,
    get() {
      if (!this.classList?.contains('ov-command-console__empty-desc')) return 0;
      return this.isConnected ? descClientWidth : 0;
    },
  });
  Object.defineProperty(proto, 'scrollWidth', {
    configurable: true,
    get() {
      if (!this.classList?.contains('ov-command-console__empty-desc')) return 0;
      if (!this.isConnected) return 0;
      return (this.textContent?.length ?? 0) * 8;
    },
  });
}

/** 设置说明节点的可用宽度。 Sets the width description nodes report. */
export const setDescWidth = (width) => {
  descClientWidth = width;
};

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
export async function hover(button) {
  await act(() =>
    button.dispatchEvent(
      new dom.window.MouseEvent('mouseover', { bubbles: true }),
    ),
  );
  await act(() => new Promise((resolve) => setTimeout(resolve, 340)));
  return document.querySelector('[role="tooltip"]')?.textContent ?? null;
}
