import { useCallback, useRef } from 'react';

function observeAnnouncement(element: HTMLSpanElement, message: string) {
  const view = element.ownerDocument.defaultView;
  if (!view) return () => {};
  let frame: number | undefined;
  let exposed = false;
  let stopped = false;
  const visible = () => {
    if (!element.isConnected) return false;
    // Visibility is inherited, but a descendant may explicitly override it.
    const { visibility } = view.getComputedStyle(element);
    if (visibility === 'hidden' || visibility === 'collapse') return false;
    for (
      let node: HTMLElement | null = element;
      node;
      node = node.parentElement
    ) {
      if (
        node.hidden ||
        node.hasAttribute('inert') ||
        node.getAttribute('aria-hidden')?.toLowerCase() === 'true'
      )
        return false;
      const style = view.getComputedStyle(node);
      if (style.display === 'none' || style.contentVisibility === 'hidden')
        return false;
    }
    return true;
  };
  const cancelFrame = () => {
    if (frame !== undefined) view.cancelAnimationFrame(frame);
    frame = undefined;
  };
  const refresh = () => {
    if (stopped) return;
    if (!visible()) {
      exposed = false;
      cancelFrame();
      element.textContent = '';
      return;
    }
    if (exposed) return;
    exposed = true;
    // Expose the empty live region before inserting its current announcement.
    element.textContent = '';
    frame = view.requestAnimationFrame(() => {
      frame = undefined;
      if (stopped) return;
      if (visible()) element.textContent = message;
      else exposed = false;
    });
  };
  const observer = new view.MutationObserver(refresh);
  for (let node: HTMLElement | null = element; node; node = node.parentElement)
    observer.observe(node, {
      attributes: true,
      attributeFilter: ['hidden', 'style', 'class', 'inert', 'aria-hidden'],
    });
  refresh();
  return () => {
    stopped = true;
    observer.disconnect();
    cancelFrame();
    element.textContent = '';
  };
}

/** Subscribe to host visibility without copying derived status into React state. */
export function useActivationAnnouncement(
  active: boolean,
  message: string,
  identity: object,
) {
  const stop = useRef<(() => void) | undefined>(undefined);
  // The host can hide/show ancestors without rendering this Console. The DOM ref
  // subscribes after commit; ref replacement disconnects observers and cancels
  // frames so old identities, languages and detached nodes cannot announce.
  return useCallback(
    (element: HTMLSpanElement | null) => {
      // A new request can carry the same status and translated message.
      void identity;
      stop.current?.();
      stop.current = undefined;
      if (!element) return;
      element.textContent = '';
      if (active && message)
        stop.current = observeAnnouncement(element, message);
    },
    [active, message, identity],
  );
}
