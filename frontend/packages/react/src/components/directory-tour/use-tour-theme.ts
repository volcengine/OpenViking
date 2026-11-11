import { useEffect } from 'react';

/** Inherit anchor tokens below the surface's own class and inline overrides. */
export function useTourTheme(
  anchor: HTMLElement | null,
  surface: HTMLDivElement | null,
) {
  // The portal loses the anchor's DOM ancestry. Copy its computed theme onto
  // a private parent, leaving surface declarations higher in the CSS cascade.
  // Track DOM reparenting and ancestor theme changes while open; disconnect the
  // observer and remove listeners and copied tokens on cleanup.
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
    let ancestors: HTMLElement[] = [];
    const observeAncestry = () => {
      const next: HTMLElement[] = [];
      for (
        let node: HTMLElement | null = anchor;
        node;
        node = node.parentElement
      )
        next.push(node);
      if (
        next.length === ancestors.length &&
        next.every((node, index) => node === ancestors[index])
      )
        return false;

      ancestors = next;
      observer.disconnect();
      for (const node of ancestors)
        observer.observe(node, {
          attributes: true,
          attributeFilter: ['class', 'style', 'data-theme'],
          childList: true,
        });
      // A detached anchor can be inserted into a different parent later.
      observer.observe(anchor.ownerDocument, {
        childList: true,
        subtree: !anchor.isConnected,
      });
      return true;
    };
    const observer = new MutationObserver((records) => {
      if (
        observeAncestry() ||
        records.some(({ type }) => type === 'attributes')
      )
        update();
    });
    observeAncestry();
    update();
    // Media queries can change computed tokens without a DOM mutation.
    window.addEventListener('resize', update);
    window.visualViewport?.addEventListener('resize', update);
    return () => {
      window.removeEventListener('resize', update);
      window.visualViewport?.removeEventListener('resize', update);
      observer.disconnect();
      for (const name of copied) host.style.removeProperty(name);
    };
  }, [anchor, surface]);
}
