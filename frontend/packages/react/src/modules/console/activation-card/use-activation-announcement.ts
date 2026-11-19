import { useCallback, useRef } from 'react';

function observeAnnouncement(element: HTMLSpanElement, message: string) {
  const view = element.ownerDocument.defaultView;
  if (!view) return () => {};
  let frame: number | undefined;
  let motionFrame: number | undefined;
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
  const resize = view.ResizeObserver
    ? new view.ResizeObserver(refresh)
    : undefined;
  const ancestors: HTMLElement[] = [];
  const motionEvents = [
    'transitionstart',
    'transitionend',
    'transitioncancel',
    'animationstart',
    'animationend',
    'animationcancel',
  ] as const;
  // A delayed visibility rule can settle without another mutation or resize.
  const motion = (event: Event) => {
    if (stopped || event.target !== event.currentTarget) return;
    if (event.type !== 'transitionstart' && event.type !== 'animationstart') {
      refresh();
      return;
    }
    // At start, computed visibility may still reflect progress zero. Recheck once
    // on the next frame, independently of the frame that writes the announcement.
    if (motionFrame !== undefined) return;
    motionFrame = view.requestAnimationFrame(() => {
      motionFrame = undefined;
      refresh();
    });
  };
  // Hosts can use data attributes, container queries or viewport media queries.
  // Observe only this ancestor chain, never document children or a frame loop.
  for (
    let node: HTMLElement | null = element;
    node;
    node = node.parentElement
  ) {
    ancestors.push(node);
    observer.observe(node, { attributes: true });
    resize?.observe(node);
    for (const event of motionEvents) node.addEventListener(event, motion);
  }
  const viewport = view.visualViewport;
  view.addEventListener('resize', refresh);
  viewport?.addEventListener('resize', refresh);
  refresh();
  return () => {
    stopped = true;
    observer.disconnect();
    resize?.disconnect();
    for (const ancestor of ancestors)
      for (const event of motionEvents)
        ancestor.removeEventListener(event, motion);
    view.removeEventListener('resize', refresh);
    viewport?.removeEventListener('resize', refresh);
    cancelFrame();
    if (motionFrame !== undefined) view.cancelAnimationFrame(motionFrame);
    motionFrame = undefined;
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
  // The host can change ancestor attributes or responsive layout without rendering
  // this Console. The DOM ref subscribes after commit; ref replacement disconnects
  // observers/listeners and cancels frames for old identities/languages/nodes.
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
