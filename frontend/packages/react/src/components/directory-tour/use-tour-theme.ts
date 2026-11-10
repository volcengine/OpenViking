import { useEffect } from 'react';

/** Inherit anchor tokens below the surface's own class and inline overrides. */
export function useTourTheme(
  anchor: HTMLElement | null,
  surface: HTMLDivElement | null,
) {
  // The portal loses the anchor's DOM ancestry. Copy its computed theme onto
  // a private parent, leaving surface declarations higher in the CSS cascade.
  // Observe ancestor theme changes and remove all listeners/tokens on cleanup.
  useEffect(() => {
    const host = surface?.parentElement;
    if (!anchor || !host) return;
    const copied = new Set<string>();
    const update = () => {
      const css = window.getComputedStyle(anchor);
      const next = new Set<string>();
      for (let index = 0; index < css.length; index++) {
        const name = css[index];
        if (!name?.startsWith('--ov-ui-')) continue;
        next.add(name);
        host.style.setProperty(name, css.getPropertyValue(name));
      }
      for (const name of copied)
        if (!next.has(name)) host.style.removeProperty(name);
      copied.clear();
      for (const name of next) copied.add(name);
    };
    update();
    const observer = new MutationObserver(update);
    for (let node: HTMLElement | null = anchor; node; node = node.parentElement)
      observer.observe(node, {
        attributes: true,
        attributeFilter: ['class', 'style'],
      });
    return () => {
      observer.disconnect();
      for (const name of copied) host.style.removeProperty(name);
    };
  }, [anchor, surface]);
}
