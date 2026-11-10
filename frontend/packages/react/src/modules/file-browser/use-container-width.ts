import { useEffect, useRef, useState } from 'react';

/**
 * 订阅元素的内容宽度，返回 ref 与当前宽度（四舍五入到整数像素）。
 *
 * 宽度要用来算拖拽上限，不能只在窗口 `resize` 时更新：宿主侧栏展开、字体变化
 * 都会让容器变宽或变窄，而窗口尺寸不变。
 *
 * Subscribes to an element's content width, returning the ref and the current
 * width rounded to whole pixels.
 *
 * The width feeds the drag limit, so a window `resize` listener is not enough: a
 * host sidebar opening or a font change resizes the container while the window
 * stays the same.
 */
export function useContainerWidth<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  const [width, setWidth] = useState(0);

  // useEffect 必要性：与 ResizeObserver 同步当前容器的盒尺寸。订阅只能在 DOM
  // 节点提交后进行，且宽度由外部布局决定，无法在渲染时算出；容器更换或组件
  // 卸载时 disconnect，避免旧观察继续回调。
  //
  // Why an effect: it syncs with ResizeObserver for the current container's box
  // size. The subscription needs the committed node, and the width is decided by
  // outside layout, so rendering cannot compute it. Disconnecting on cleanup stops
  // a stale observer from calling back.
  useEffect(() => {
    const element = ref.current;
    if (!element) return;

    const update = (next: number) =>
      setWidth((current) => (current === next ? current : next));

    update(Math.round(element.getBoundingClientRect().width));

    const observer = new ResizeObserver(([entry]) => {
      if (entry) update(Math.round(entry.contentRect.width));
    });
    observer.observe(element);

    return () => observer.disconnect();
  }, []);

  return { ref, width };
}
