import { useEffect, useState } from 'react';

type Edges = Pick<DOMRectReadOnly, 'left' | 'right' | 'top' | 'bottom'>;

function intersect(a: Edges, b: Edges): Edges {
  return {
    left: Math.max(a.left, b.left),
    right: Math.min(a.right, b.right),
    top: Math.max(a.top, b.top),
    bottom: Math.min(a.bottom, b.bottom),
  };
}

function hasArea(rect: Edges) {
  return rect.right > rect.left && rect.bottom > rect.top;
}

const clippingOverflow = /^(?:auto|scroll|hidden|clip|overlay)$/;

function getClip(anchor: HTMLElement, viewport: Edges): Edges {
  const clip = { ...viewport };
  for (
    let parent = anchor.parentElement;
    parent;
    parent = parent.parentElement
  ) {
    const { overflowX, overflowY } = window.getComputedStyle(parent);
    const clipX = clippingOverflow.test(overflowX);
    const clipY = clippingOverflow.test(overflowY);
    if (!clipX && !clipY) continue;
    const rect = parent.getBoundingClientRect();
    // Client dimensions exclude borders and scrollbars. Convert to viewport
    // coordinates so a scaled scroll container clips in the same space as its rows.
    if (clipX) {
      const scale = parent.offsetWidth ? rect.width / parent.offsetWidth : 1;
      const left = rect.left + parent.clientLeft * scale;
      clip.left = Math.max(clip.left, left);
      clip.right = Math.min(clip.right, left + parent.clientWidth * scale);
    }
    if (clipY) {
      const scale = parent.offsetHeight ? rect.height / parent.offsetHeight : 1;
      const top = rect.top + parent.clientTop * scale;
      clip.top = Math.max(clip.top, top);
      clip.bottom = Math.min(clip.bottom, top + parent.clientHeight * scale);
    }
  }
  return clip;
}

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
      const missing = Boolean(
        anchor && (!anchor.isConnected || !a?.width || !a.height),
      );
      let visible = false;
      if (anchor && a && !missing) {
        const clip = getClip(anchor, {
          left: x,
          top: y,
          right: x + width,
          bottom: y + height,
        });
        highlight = intersect(group ?? a, clip);
        visible = hasArea(highlight);
        const firstRow = intersect(a, clip);
        // Keep grouped tours anchored to the first row while it is visible.
        const reference = hasArea(firstRow) ? firstRow : highlight;
        const right = reference.right + 6;
        const leftSide = reference.left - box.width - 6;
        const fitsRight = right + box.width <= x + width - margin;
        const fitsLeft = leftSide >= x + margin;
        if (fitsRight || fitsLeft) {
          left =
            fitsRight && (placement === 'right' || !fitsLeft)
              ? right
              : leftSide;
          top = reference.top - 16;
        } else {
          // On narrow screens, keep the visible highlight clear when possible.
          const row = highlight;
          const below = row.bottom + 6;
          const above = row.top - box.height - 6;
          left = row.left + (row.right - row.left - box.width) / 2;
          top =
            below + box.height <= y + height - margin ||
            row.top - y < y + height - row.bottom
              ? below
              : above;
        }
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
