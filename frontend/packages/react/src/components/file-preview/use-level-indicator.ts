import { useLayoutEffect, useRef, useState } from 'react';

/**
 * 选中指示条的位置：一条 14×2 的横线，宽度与位置跟随选中项，切换时滑动过去。
 *
 * 它不属于任何一级，所以切换是位移而不是各自显隐。
 *
 * The position of the selection indicator: one 14×2 rule whose position follows the
 * active level and slides when it changes.
 *
 * It belongs to no single level, so a switch is a movement rather than one rule
 * hiding and another appearing.
 */
export interface LevelIndicatorState {
  left: number;
  width: number;
}

const INDICATOR_WIDTH = 14;

/**
 * 测量选中页签的布局盒，得出指示条应放的位置。
 *
 * useLayoutEffect 必要性：位置来自页签按钮的实际布局盒，只有提交后才可测量；
 * 字体加载、容器缩放与层级变化都会改变它，无法在渲染时算出。ResizeObserver
 * 覆盖前两者，`activeKey` 与 `levelCount` 覆盖切换与层级增减；观察对象随组件
 * 卸载断开，避免旧订阅继续回调。
 *
 * A layout effect is required: the position comes from the tab buttons' layout
 * boxes, measurable only after commit, and font loading, container resizing or a
 * level change all move them. The ResizeObserver covers the first two, `activeKey`
 * and `levelCount` the last two; the subscription is disconnected on unmount so a
 * stale one cannot keep calling back.
 */
export function useLevelIndicator(
  activeSelector: string,
  activeKey: string | undefined,
  levelCount: number,
) {
  const ref = useRef<HTMLDivElement>(null);
  const [indicator, setIndicator] = useState<LevelIndicatorState>({
    left: 0,
    width: 0,
  });

  useLayoutEffect(() => {
    const root = ref.current;
    if (!root) return;
    const measure = () => {
      const active = root.querySelector<HTMLElement>(activeSelector);
      // 没有选中项（例如所有层级都不可用）时把指示条收起，不留上一条的位置。
      // With nothing selected, such as every level disabled, hide the indicator
      // rather than leaving the previous position on screen.
      if (!active) {
        setIndicator({ left: 0, width: 0 });
        return;
      }
      setIndicator({
        left: active.offsetLeft + (active.offsetWidth - INDICATOR_WIDTH) / 2,
        width: INDICATOR_WIDTH,
      });
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(root);
    return () => observer.disconnect();
  }, [activeSelector, activeKey, levelCount]);

  return { ref, indicator };
}
