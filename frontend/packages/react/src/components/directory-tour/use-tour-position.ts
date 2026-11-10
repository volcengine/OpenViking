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
}

export function useTourPosition(
  anchor: HTMLElement | null,
  surface: HTMLDivElement | null,
  placement: 'right' | 'left',
) {
  const [position, setPosition] = useState<Position>();
  // Subscribe to external browser layout, scroll and DOM removal after both
  // elements commit. Native popover supplies the top layer without a z-index
  // override. Disconnect every observer/listener and close the popover on cleanup.
  useEffect(() => {
    if (!surface) return;
    surface.showPopover?.();
    const update = () => {
      const a = anchor?.getBoundingClientRect();
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
        visible =
          anchor.isConnected &&
          a.width > 0 &&
          a.height > 0 &&
          a.top < clip.bottom &&
          a.bottom > clip.top &&
          a.left < clip.right &&
          a.right > clip.left;
        const right = a.right + 6;
        const leftSide = a.left - box.width - 6;
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
        targetTop: a?.top ?? 0,
        targetLeft: a?.left ?? 0,
        targetWidth: a?.width ?? 0,
        targetHeight: a?.height ?? 0,
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
      if (surface.isConnected) surface.hidePopover?.();
    };
  }, [anchor, surface, placement]);
  return position;
}
