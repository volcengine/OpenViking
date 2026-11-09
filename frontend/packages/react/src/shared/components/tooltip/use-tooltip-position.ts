import { useEffect, useLayoutEffect, useState } from 'react';

import type { TooltipPlacement } from './types.js';
import { usePortalTheme } from './use-portal-theme.js';

// Server renderers cannot measure a viewport; retain pre-paint positioning in browsers.
const usePositionEffect =
  typeof window === 'undefined' ? useEffect : useLayoutEffect;

/** 浮层与触发元素之间的间隙。 Gap between the surface and its trigger. */
export const OFFSET = 6;
/** 浮层与视口边缘的最小留白。 Minimum distance from the viewport edge. */
const VIEWPORT_MARGIN = 8;

export interface TooltipPosition {
  top: number;
  left: number;
  /** 实际采用的方位，可能与请求的不同。 The placement actually used. */
  placement: TooltipPlacement;
}

/** 方位翻转到对侧。 The opposite side of a placement. */
const OPPOSITE: Record<TooltipPlacement, TooltipPlacement> = {
  top: 'bottom',
  bottom: 'top',
  left: 'right',
  right: 'left',
};

/**
 * 按触发元素与浮层尺寸算出一个视口坐标。空间不足时翻到对侧，仍放不下就
 * 贴着视口边缘，保证浮层始终可见。
 *
 * Resolves a viewport position from the trigger and surface boxes. It flips to
 * the opposite side when space runs out and clamps to the viewport otherwise, so
 * the surface stays visible.
 */
export function resolvePosition(
  trigger: DOMRect,
  surface: { width: number; height: number },
  placement: TooltipPlacement,
  alignment: 'center' | 'end' = 'center',
): TooltipPosition {
  const viewportWidth = window.innerWidth;
  const viewportHeight = window.innerHeight;

  const fits = (side: TooltipPlacement) => {
    if (side === 'top')
      return trigger.top - surface.height - OFFSET >= VIEWPORT_MARGIN;
    if (side === 'bottom')
      return (
        trigger.bottom + surface.height + OFFSET <=
        viewportHeight - VIEWPORT_MARGIN
      );
    if (side === 'left')
      return trigger.left - surface.width - OFFSET >= VIEWPORT_MARGIN;
    return (
      trigger.right + surface.width + OFFSET <= viewportWidth - VIEWPORT_MARGIN
    );
  };

  const side =
    fits(placement) || !fits(OPPOSITE[placement])
      ? placement
      : OPPOSITE[placement];

  const clamp = (value: number, size: number, limit: number) =>
    Math.min(Math.max(value, VIEWPORT_MARGIN), limit - size - VIEWPORT_MARGIN);

  if (side === 'top' || side === 'bottom') {
    const top =
      side === 'top'
        ? trigger.top - surface.height - OFFSET
        : trigger.bottom + OFFSET;
    const left =
      alignment === 'end'
        ? trigger.right - surface.width
        : trigger.left + trigger.width / 2 - surface.width / 2;
    return {
      top: clamp(top, surface.height, viewportHeight),
      left: clamp(left, surface.width, viewportWidth),
      placement: side,
    };
  }

  const left =
    side === 'left'
      ? trigger.left - surface.width - OFFSET
      : trigger.right + OFFSET;
  const top = trigger.top + trigger.height / 2 - surface.height / 2;
  return {
    top: clamp(top, surface.height, viewportHeight),
    left: clamp(left, surface.width, viewportWidth),
    placement: side,
  };
}

/**
 * 跟踪浮层的视口坐标。
 *
 * useLayoutEffect 必要性：坐标来自触发元素与浮层自身的布局盒，只有提交后才
 * 可测量；浮层内容变化会改变尺寸，窗口滚动与缩放也会改变相对位置，这些都
 * 不能靠渲染计算得出。监听 scroll（捕获阶段，覆盖内层滚动容器）与 resize，
 * 卸载或关闭时移除，避免旧回调继续写坐标。
 *
 * Tracks the surface's viewport position.
 *
 * A layout effect is required: the coordinates come from the trigger's and the
 * surface's boxes, measurable only after commit; content changes resize the
 * surface, and scrolling or resizing moves it relative to the trigger — none of
 * that is derivable during render. Scroll is captured so inner scroll containers
 * count too; listeners are removed on close or unmount.
 */
export function useTooltipPosition(
  open: boolean,
  triggerRef: React.RefObject<HTMLElement | null>,
  surface: HTMLElement | null,
  placement: TooltipPlacement,
  alignment: 'center' | 'end' = 'center',
) {
  const [position, setPosition] = useState<TooltipPosition>({
    top: 0,
    left: 0,
    placement,
  });

  usePortalTheme(open, triggerRef, surface);

  // Sync with viewport/element geometry after DOM commit; render and event-only
  // logic cannot observe scroll, resize or theme changes. Cleanup disconnects
  // observers and listeners. The server uses an inert effect without layout warnings.
  usePositionEffect(() => {
    if (!open) return;
    const trigger = triggerRef.current;
    if (!trigger || !surface) return;

    const update = () => {
      const triggerRect = trigger.getBoundingClientRect();
      const surfaceRect = surface.getBoundingClientRect();
      const next = resolvePosition(
        triggerRect,
        { width: surfaceRect.width, height: surfaceRect.height },
        placement,
        alignment,
      );
      // 坐标没变就不写 state：每次写入新对象都会触发一次重渲染，而重渲染
      // 又会让本 effect 重跑，形成无限循环。
      setPosition((previous) =>
        previous.top === next.top &&
        previous.left === next.left &&
        previous.placement === next.placement
          ? previous
          : next,
      );
    };

    update();

    // 浮层内容是异步渲染的：`open` 刚变真时内容可能还没提交，尺寸为 0。
    // 用 ResizeObserver 盯住它，尺寸稳定下来后重新定位。
    const observer = new ResizeObserver(update);
    observer.observe(surface);

    window.addEventListener('scroll', update, true);
    window.addEventListener('resize', update);
    return () => {
      observer.disconnect();
      window.removeEventListener('scroll', update, true);
      window.removeEventListener('resize', update);
    };
  }, [open, placement, surface, triggerRef, alignment]);

  return position;
}
