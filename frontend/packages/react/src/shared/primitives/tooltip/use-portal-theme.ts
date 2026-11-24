import { useLayoutEffect } from 'react';
import type { RefObject } from 'react';

/** Keep a body portal in sync with the host's scoped public theme overrides. */
export function usePortalTheme(
  open: boolean,
  triggerRef: RefObject<HTMLElement | null>,
  surface: HTMLElement | null,
) {
  // Copy the committed trigger's scoped theme into its body portal; ancestor
  // styles can change outside React, so render or UI events cannot track them.
  // Close, node changes and unmount disconnect the observer and remove copied styles.
  useLayoutEffect(() => {
    const trigger = triggerRef.current;
    if (!open || !trigger || !surface) return;
    const written = new Set<string>();
    const sync = () => {
      const style = window.getComputedStyle(trigger);
      const seen = new Set<string>();
      for (const name of Array.from(style)) {
        // Defaults remain CSS-driven, so an open portal follows .dark changes.
        if (!name.startsWith('--ov-ui-color-')) continue;
        const value = style.getPropertyValue(name).trim();
        if (!value) continue;
        seen.add(name);
        surface.style.setProperty(name, value);
      }
      for (const name of written)
        if (!seen.has(name)) surface.style.removeProperty(name);
      written.clear();
      seen.forEach((name) => written.add(name));
    };
    sync();
    const observer = new window.MutationObserver(sync);
    for (let node: Element | null = trigger; node; node = node.parentElement)
      observer.observe(node, {
        attributes: true,
        attributeFilter: ['class', 'style', 'data-theme'],
      });
    return () => {
      observer.disconnect();
      written.forEach((name) => surface.style.removeProperty(name));
    };
  }, [open, triggerRef, surface]);
}
