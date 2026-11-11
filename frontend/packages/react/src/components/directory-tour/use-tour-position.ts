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
      const width = window.innerWidth;
      const height = window.innerHeight;
      const margin = 8;
      let left = (width - box.width) / 2;
      let top = (height - box.height) / 2;
      const missing = Boolean(
        anchor && (!anchor.isConnected || !a?.width || !a.height),
      );
      let visible = false;
      if (anchor && a && !missing) {
        // A row clipped by a scrolling panel must not leave a detached callout.
        let clip = { left: 0, top: 0, right: width, bottom: height };
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
        const fitsRight = right + box.width <= width - margin;
        const fitsLeft = leftSide >= margin;
        if (fitsRight || fitsLeft) {
          left =
            fitsRight && (placement === 'right' || !fitsLeft)
              ? right
              : leftSide;
          top = a.top - 16;
        } else {
          // On narrow screens, keep the highlighted row clear when possible.
          const below = a.bottom + 6;
          const above = a.top - box.height - 6;
          left = a.left + (a.width - box.width) / 2;
          top =
            below + box.height <= height - margin || a.top < height - a.bottom
              ? below
              : above;
        }
        // Carry host theme overrides across the portal boundary.
        const css = window.getComputedStyle(anchor);
        for (let i = 0; i < css.length; i++) {
          const name = css[i];
          if (name?.startsWith('--ov-ui-'))
            surface.style.setProperty(name, css.getPropertyValue(name));
        }
      }
      const next = {
        left: Math.max(margin, Math.min(left, width - box.width - margin)),
        top: Math.max(margin, Math.min(top, height - box.height - margin)),
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
    update();
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
