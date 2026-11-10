import { useEffect, useState } from 'react';

interface Position {
  top: number;
  left: number;
  targetTop: number;
  targetLeft: number;
  targetWidth: number;
  targetHeight: number;
  visible: boolean;
  missing: boolean;
  maxWidth: number;
  maxHeight: number;
  layoutHeight: number;
}

export function useTourPosition(
  anchor: HTMLElement | null,
  surface: HTMLDivElement | null,
  placement: 'right' | 'left',
  elements: readonly HTMLElement[],
) {
  const [position, setPosition] = useState<Position>();
  // Subscribe to external browser layout, scroll and DOM removal after both
  // elements commit. The private parent popover supplies the top layer without a z-index
  // override. Disconnect every observer/listener and close the popover on cleanup.
  useEffect(() => {
    if (!surface) return;
    const frameElement = surface.parentElement;
    frameElement?.showPopover?.();
    const update = () => {
      const a = anchor?.getBoundingClientRect();
      const bounds = elements
        .filter((node) => node.isConnected)
        .map((node) => node.getBoundingClientRect());
      const group = bounds.length
        ? {
            top: Math.min(...bounds.map((r) => r.top)),
            left: Math.min(...bounds.map((r) => r.left)),
            right: Math.max(...bounds.map((r) => r.right)),
            bottom: Math.max(...bounds.map((r) => r.bottom)),
          }
        : a;
      let highlight = group;
      const box = surface.getBoundingClientRect();
      const viewport = window.visualViewport;
      const width = viewport?.width ?? window.innerWidth;
      const height = viewport?.height ?? window.innerHeight;
      const x = viewport?.offsetLeft ?? 0;
      const y = viewport?.offsetTop ?? 0;
      const margin = 8;
      let left = x + (width - box.width) / 2;
      let top = y + (height - box.height) / 2;
      const missing = Boolean(anchor && !anchor.isConnected);
      let visible = !anchor || missing;
      if (anchor && a && !missing) {
        // A row clipped by a scrolling panel must not leave a detached callout.
        let clip = { left: x, top: y, right: x + width, bottom: y + height };
        for (
          let parent = anchor.parentElement;
          parent;
          parent = parent.parentElement
        ) {
          const css = window.getComputedStyle(parent);
          const rect = parent.getBoundingClientRect();
          if (/(auto|scroll|hidden|clip)/.test(css.overflowX))
            clip = {
              ...clip,
              left: Math.max(clip.left, rect.left),
              right: Math.min(clip.right, rect.right),
            };
          if (/(auto|scroll|hidden|clip)/.test(css.overflowY))
            clip = {
              ...clip,
              top: Math.max(clip.top, rect.top),
              bottom: Math.min(clip.bottom, rect.bottom),
            };
        }
        const target = group ?? a;
        visible =
          target.right > target.left &&
          target.bottom > target.top &&
          target.top < clip.bottom &&
          target.bottom > clip.top &&
          target.left < clip.right &&
          target.right > clip.left;
        if (group)
          highlight = {
            top: Math.max(group.top, clip.top),
            left: Math.max(group.left, clip.left),
            right: Math.min(group.right, clip.right),
            bottom: Math.min(group.bottom, clip.bottom),
          };
        const right = (group?.right ?? a.right) + 6;
        const leftSide = (group?.left ?? a.left) - box.width - 6;
        left =
          placement === 'right'
            ? right + box.width <= x + width - margin || leftSide < x + margin
              ? right
              : leftSide
            : leftSide >= x + margin || right + box.width > x + width - margin
              ? leftSide
              : right;
        top = a.top - 16;
      }
      const next = {
        left: Math.max(
          x + margin,
          Math.min(left, x + width - box.width - margin),
        ),
        top: Math.max(
          y + margin,
          Math.min(top, y + height - box.height - margin),
        ),
        maxWidth: Math.max(0, width - margin * 2),
        maxHeight: Math.max(0, height - margin * 2),
        layoutHeight: window.innerHeight,
        targetTop: highlight?.top ?? 0,
        targetLeft: highlight?.left ?? 0,
        targetWidth: highlight
          ? Math.max(0, highlight.right - highlight.left)
          : 0,
        targetHeight: highlight
          ? Math.max(0, highlight.bottom - highlight.top)
          : 0,
        visible,
        missing,
      };
      setPosition((old) =>
        old &&
        Object.keys(next).every(
          (key) => old[key as keyof Position] === next[key as keyof Position],
        )
          ? old
          : next,
      );
    };
    // Layout can move without resizing (transforms, sibling reflow, animations).
    // One frame loop coalesces those changes; equal positions retain state.
    let frame = 0;
    const track = () => {
      update();
      frame = window.requestAnimationFrame(track);
    };
    track();
    const resize = new ResizeObserver(update);
    resize.observe(surface);
    for (let node: HTMLElement | null = anchor; node; node = node.parentElement)
      resize.observe(node);
    for (const element of elements) resize.observe(element);
    const mutation = new MutationObserver(update);
    mutation.observe(document.body, { childList: true, subtree: true });
    window.addEventListener('scroll', update, true);
    window.addEventListener('resize', update);
    window.visualViewport?.addEventListener('resize', update);
    window.visualViewport?.addEventListener('scroll', update);
    return () => {
      window.cancelAnimationFrame(frame);
      resize.disconnect();
      mutation.disconnect();
      window.removeEventListener('scroll', update, true);
      window.removeEventListener('resize', update);
      window.visualViewport?.removeEventListener('resize', update);
      window.visualViewport?.removeEventListener('scroll', update);
      if (frameElement?.isConnected) frameElement.hidePopover?.();
    };
  }, [anchor, surface, placement, elements]);
  return position;
}
