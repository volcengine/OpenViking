import { useEffect, useState } from 'react';

/** maxTagCount is an upper bound; reserve room for +N before tags get too small. */
export function useTagCount(total: number, maximum: number) {
  const [node, setNode] = useState<HTMLSpanElement | null>(null);
  const [fitting, setFitting] = useState<number>();
  const requested = Math.max(0, Math.floor(maximum));
  // useEffect 必要性：订阅标签容器的实际宽度，宿主布局和缩放可在无点击时改变它；
  // DOM 提交前无法测量，不能由渲染计算或选中事件替代；容器更换或卸载时断开观察。
  useEffect(() => {
    if (!node) return;
    const update = () => {
      if (!node.clientWidth) return;
      const style = getComputedStyle(node);
      const available =
        node.clientWidth - parseFloat(style.paddingInlineEnd || '0');
      const minimum =
        parseFloat(style.getPropertyValue('--ov-ui-select-tag-min-width')) ||
        64;
      const gap = parseFloat(style.columnGap) || 4;
      const countWidth = Math.max(
        24,
        (parseFloat(style.fontSize) || 12) * (1 + String(total).length) * 0.7 +
          8,
      );
      let count = Math.min(
        requested,
        total,
        Math.floor((available + gap) / (minimum + gap)),
      );
      if (count < total)
        count = Math.min(
          count,
          Math.floor((available - countWidth) / (minimum + gap)),
        );
      setFitting(Math.max(0, count));
    };
    update();
    const observer = new ResizeObserver(update);
    observer.observe(node);
    return () => observer.disconnect();
  }, [node, requested, total]);
  return { tagRef: setNode, count: Math.min(requested, fitting ?? requested) };
}
