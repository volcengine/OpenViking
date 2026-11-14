import { useEffect, useLayoutEffect } from 'react';

const usePositionEffect =
  typeof window === 'undefined' ? useEffect : useLayoutEffect;
const SHIFT = '--ov-ui-scope-switcher-panel-shift';

export function usePanelPosition(panel: HTMLDivElement | null) {
  // Sync the committed panel with viewport edges before paint. Wrapping, async
  // content, scrolling and resizing can move its box without a user action.
  // Disconnect observers/listeners and remove the correction on close/unmount.
  usePositionEffect(() => {
    if (!panel) return;
    const update = () => {
      panel.style.removeProperty(SHIFT);
      // The phone layout is already a fixed, inset bottom sheet.
      if (window.getComputedStyle(panel).position === 'fixed') return;
      const rect = panel.getBoundingClientRect();
      if (!rect.width) return;
      const left = Math.min(
        Math.max(rect.left, 8),
        window.innerWidth - rect.width - 8,
      );
      const scale = rect.width / (panel.offsetWidth || rect.width);
      panel.style.setProperty(SHIFT, `${(left - rect.left) / scale}px`);
    };
    update();
    const observer = new ResizeObserver(update);
    const switcher = panel.closest('.ov-scope-switcher');
    // A preceding level can resize and move this one while the row count and
    // both this level's and the switcher's dimensions remain unchanged.
    for (const node of [
      panel,
      panel.parentElement,
      switcher,
      ...(switcher?.querySelectorAll('.ov-scope-switcher__level') ?? []),
    ]) {
      if (node) observer.observe(node);
    }
    window.addEventListener('resize', update);
    window.addEventListener('scroll', update, true);
    return () => {
      observer.disconnect();
      window.removeEventListener('resize', update);
      window.removeEventListener('scroll', update, true);
      panel.style.removeProperty(SHIFT);
    };
  }, [panel]);
}
