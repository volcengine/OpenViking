import { useEffect, useLayoutEffect, useRef, useState } from 'react';

// 服务端渲染没有布局，useLayoutEffect 在那里只会告警；测量只在浏览器有意义。
// A server render has no layout, so a layout effect would only warn there.
const useMeasureEffect =
  typeof window === 'undefined' ? useEffect : useLayoutEffect;

const DESC_SELECTOR = '.ov-command-console__empty-desc';

/**
 * 跟踪哪些快捷命令的说明被单行省略截断，返回列表容器 ref 与截断项的下标。
 *
 * 说明是单行省略的文本，容器变窄或文案变长时会被截断，行内便看不到全文。
 *
 * useLayoutEffect 必要性：是否截断取决于容器的布局宽度与文本的自然宽度，二者
 * 只有提交后才可测量，无法在渲染或事件里算出；窗口缩放和面板宽度变化都会改变
 * 结果。
 *
 * 订阅只建立一次，之后一直有效：
 *
 * - 观察列表容器，而不是说明节点：说明是网格列里的单行文本，宽度由列宽决定，
 *   文本溢出时它自己的 content box 不变，观察它收不到回调。容器宽度随面板与
 *   窗口变化，是可靠的触发源。
 * - 订阅存在 ref 里，effect 依赖为空数组。此前依赖签名重跑 effect，会在截断
 *   状态变化引起重渲染时 disconnect 掉观察器，之后的宽度变化再无人接收。
 * - 文案变长不改变任何盒尺寸，观察器不会回调，因此每次渲染后再测一次：测量
 *   本身只读 scrollWidth/clientWidth，不写 DOM，代价是一次同步布局查询。
 *
 * Tracks which quick command descriptions the single-line ellipsis truncates,
 * returning the list container ref and the truncated indices.
 *
 * A layout effect is required: truncation depends on the container's laid-out
 * width and the text's natural width, measurable only after commit, and both
 * change with the window and the panel.
 *
 * The subscription is created once and stays valid:
 *
 * - The list container is observed, not the description nodes: each description
 *   is a single line inside a grid column, so its width follows the column and
 *   its own content box does not change when the text overflows — watching it
 *   receives no callback. The container's width tracks the panel and the window.
 * - The subscription lives in a ref and the effect takes no dependencies. Re-running
 *   it on a signature change disconnected the observer whenever a truncation change
 *   re-rendered the rows, leaving later width changes unheard.
 * - Longer copy changes no box at all, so the observer never fires; measuring after
 *   every render covers it. The measurement only reads `scrollWidth`/`clientWidth`
 *   and writes no DOM, so it costs one synchronous layout query.
 */
export function useCommandConsoleQuickOverflow() {
  const listRef = useRef<HTMLUListElement>(null);
  const [truncated, setTruncated] = useState<readonly number[]>([]);

  const measure = () => {
    const list = listRef.current;
    if (!list) return;
    const next: number[] = [];
    list.querySelectorAll<HTMLElement>(DESC_SELECTOR).forEach((node, index) => {
      if (node.scrollWidth > node.clientWidth) next.push(index);
    });
    // 只在结果变化时提交，避免每次渲染都触发一轮新渲染。
    // Commit only on change so a render does not schedule another one.
    setTruncated((current) =>
      current.length === next.length &&
      current.every((value, index) => value === next[index])
        ? current
        : next,
    );
  };

  // 订阅只建一次。ResizeObserver 的回调闭包读的是 ref 里的当前列表，列表本身
  // 不会在空态存在期间被替换。
  //
  // The subscription is built once. The observer callback reads the current list
  // through the ref, and that list is not replaced while the empty state lives.
  useMeasureEffect(() => {
    const list = listRef.current;
    if (!list) return;
    const observer = new ResizeObserver(measure);
    observer.observe(list);
    return () => observer.disconnect();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- 订阅只建一次，见上方说明。
  }, []);

  // 每次渲染后补测一次，覆盖文案变长这类不改变盒尺寸的变化。
  // Re-measure after every render to cover changes that resize no box, such as
  // longer copy.
  useMeasureEffect(measure);

  return { listRef, truncated };
}
