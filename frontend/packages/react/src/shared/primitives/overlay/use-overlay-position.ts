import { useEffect, useLayoutEffect, useState } from 'react';
import type { OverlayPlacement } from './types.js';

// Measure before paint in browsers; server renderers have no viewport.
const usePositionEffect =
  typeof window === 'undefined' ? useEffect : useLayoutEffect;

/** 浮层与触发元素之间的间隙。 Gap between the surface and its trigger. */
export const OVERLAY_OFFSET = 6;
/** 浮层与视口边缘的最小留白。 Minimum distance from the viewport edge. */
const VIEWPORT_MARGIN = 8;

export interface OverlayPosition {
  top: number;
  left: number;
  /** 实际采用的方位，可能与请求的不同。 The placement actually used. */
  placement: OverlayPlacement;
  anchorWidth?: number;
}

/** 方位翻转到对侧。 The opposite side of a placement. */
const OPPOSITE: Record<OverlayPlacement, OverlayPlacement> = {
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
  placement: OverlayPlacement,
  alignment: 'start' | 'center' | 'end' = 'center',
): OverlayPosition {
  const viewportWidth = window.innerWidth;
  const viewportHeight = window.innerHeight;

  const fits = (side: OverlayPlacement) => {
    if (side === 'top')
      return trigger.top - surface.height - OVERLAY_OFFSET >= VIEWPORT_MARGIN;
    if (side === 'bottom')
      return (
        trigger.bottom + surface.height + OVERLAY_OFFSET <=
        viewportHeight - VIEWPORT_MARGIN
      );
    if (side === 'left')
      return trigger.left - surface.width - OVERLAY_OFFSET >= VIEWPORT_MARGIN;
    return (
      trigger.right + surface.width + OVERLAY_OFFSET <=
      viewportWidth - VIEWPORT_MARGIN
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
        ? trigger.top - surface.height - OVERLAY_OFFSET
        : trigger.bottom + OVERLAY_OFFSET;
    const left =
      alignment === 'start'
        ? trigger.left
        : alignment === 'end'
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
      ? trigger.left - surface.width - OVERLAY_OFFSET
      : trigger.right + OVERLAY_OFFSET;
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
export function useOverlayPosition(
  open: boolean,
  triggerRef: React.RefObject<HTMLElement | null>,
  surface: HTMLElement | null,
  placement: OverlayPlacement,
  alignment: 'start' | 'center' | 'end' = 'center',
) {
  const [position, setPosition] = useState<OverlayPosition>({
    top: 0,
    left: 0,
    placement,
  });

  // Sync committed geometry and scoped theme; release both on close or unmount.
  // The server uses an inert effect instead of attempting layout measurement.
  usePositionEffect(() => {
    if (!open) return;
    const trigger = triggerRef.current;
    if (!trigger || !surface) return;

    // 浮层渲染到 `body`，拿不到宿主设在组件容器上的主题变量——CSS 变量沿 DOM
    // 树继承，跨 portal 就断了。把触发元素上的公开变量复制到浮层，宿主的局部
    // 覆盖才能跟着浮层走。
    //
    // 只复制公开的 `--ov-ui-color-*`，不复制组件私有的 `--ov-ui-*-default`：
    // 后者一旦内联，就不再随 `.dark` 切换变化，等于把浮层的主题锁死在打开那一刻。
    // 主题变化（`html` 的 class 改动）时重新同步，保证打开期间也跟着变。
    //
    // The surface renders into `body` and cannot see theme variables a host sets on
    // a component container — CSS variables inherit down the tree and the portal
    // breaks that chain. Copy the trigger's public variables so a local override
    // follows the surface.
    //
    // Only the public `--ov-ui-color-*` are copied, never the component-private
    // `--ov-ui-*-default`: inlining those would freeze the surface's theme at the
    // moment it opened instead of following `.dark`. Re-sync when the theme changes
    // (a class change on `html`) so an open surface follows along.
    // 记录上一次写进浮层的变量名，重新同步时先把不再出现的清掉——否则宿主
    // 移除某个覆盖后，浮层还留着旧值。
    //
    // Remember which names were written so a re-sync can drop the ones that no
    // longer apply — otherwise the surface keeps an old value after the host
    // removes its override.
    const written = new Set<string>();
    const syncTheme = () => {
      const triggerStyle = window.getComputedStyle(trigger);
      const seen = new Set<string>();
      for (const name of Array.from(triggerStyle)) {
        if (!name.startsWith('--ov-ui-color-')) continue;
        const value = triggerStyle.getPropertyValue(name).trim();
        if (!value) continue;
        seen.add(name);
        surface.style.setProperty(name, value);
      }
      for (const name of written) {
        if (seen.has(name)) continue;
        surface.style.removeProperty(name);
      }
      written.clear();
      seen.forEach((name) => written.add(name));
    };
    syncTheme();
    // 观察整条祖先链而不只是 `html`：宿主把主题覆盖写在某个容器上时，改的是
    // 那个元素的 `style`/class，`html` 不变，只盯根节点收不到通知。
    //
    // Watch the whole ancestor chain, not just `html`: a host that scopes its theme
    // override to a container changes that element's `style`/class while `html`
    // stays put, so observing only the root would miss it.
    const themeObserver = new MutationObserver(syncTheme);
    const observerOptions: MutationObserverInit = {
      attributes: true,
      attributeFilter: ['class', 'style', 'data-theme'],
    };
    for (let node: Element | null = trigger; node; node = node.parentElement) {
      themeObserver.observe(node, observerOptions);
    }

    const update = () => {
      const triggerRect = trigger.getBoundingClientRect();
      const surfaceRect = surface.getBoundingClientRect();
      const next = {
        ...resolvePosition(
          triggerRect,
          { width: surfaceRect.width, height: surfaceRect.height },
          placement,
          alignment,
        ),
        anchorWidth: triggerRect.width,
      };
      // 坐标没变就不写 state：每次写入新对象都会触发一次重渲染，而重渲染
      // 又会让本 effect 重跑，形成无限循环。
      setPosition((previous) =>
        previous.top === next.top &&
        previous.left === next.left &&
        previous.placement === next.placement &&
        previous.anchorWidth === next.anchorWidth
          ? previous
          : next,
      );
    };

    update();

    // 浮层内容是异步渲染的：`open` 刚变真时内容可能还没提交，尺寸为 0。
    // 用 ResizeObserver 盯住它，尺寸稳定下来后重新定位。
    const observer = new ResizeObserver(update);
    observer.observe(surface);
    observer.observe(trigger);

    window.addEventListener('scroll', update, true);
    window.addEventListener('resize', update);
    return () => {
      observer.disconnect();
      themeObserver.disconnect();
      written.forEach((name) => surface.style.removeProperty(name));
      window.removeEventListener('scroll', update, true);
      window.removeEventListener('resize', update);
    };
  }, [open, placement, surface, triggerRef, alignment]);

  return position;
}
