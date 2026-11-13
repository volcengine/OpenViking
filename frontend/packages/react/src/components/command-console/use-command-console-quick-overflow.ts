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
 * 观察每个说明节点本身，而不是列表容器：容器宽度不变、只是其中一行的文案变长
 * 时，容器尺寸不会变化，只看容器会漏掉这次截断。节点随行数增减，因此依赖列表
 * 长度重新订阅，卸载时 disconnect。
 *
 * Tracks which quick command descriptions the single-line ellipsis truncates,
 * returning the list container ref and the truncated indices.
 *
 * A layout effect is required: truncation depends on the container's laid-out
 * width and the text's natural width, measurable only after commit, and both
 * change with the window and the panel.
 *
 * Each description node is observed rather than the list: when the container
 * keeps its width and only one row's text grows, the container's box does not
 * change and watching it alone would miss the truncation. The nodes come and go
 * with the row count, so the subscription is rebuilt on length changes and
 * disconnected on unmount.
 */
export function useCommandConsoleQuickOverflow(count: number) {
  const listRef = useRef<HTMLUListElement>(null);
  const [truncated, setTruncated] = useState<readonly number[]>([]);

  useMeasureEffect(() => {
    const list = listRef.current;
    if (!list) return;

    const measure = (nodes: readonly HTMLElement[]) => {
      const next: number[] = [];
      nodes.forEach((node, index) => {
        if (node.scrollWidth > node.clientWidth) next.push(index);
      });
      // 只在结果变化时提交，避免观察器回调引起多余渲染。
      // Commit only on change so observer callbacks do not re-render needlessly.
      setTruncated((current) =>
        current.length === next.length &&
        current.every((value, index) => value === next[index])
          ? current
          : next,
      );
    };

    const nodes = [...list.querySelectorAll<HTMLElement>(DESC_SELECTOR)];
    measure(nodes);
    const observer = new ResizeObserver(() => measure(nodes));
    for (const node of nodes) observer.observe(node);
    return () => observer.disconnect();
  }, [count]);

  return { listRef, truncated };
}
