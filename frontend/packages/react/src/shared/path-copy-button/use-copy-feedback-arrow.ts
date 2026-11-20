import { useEffect, useState, type RefObject } from 'react';

/** Keep the arrow on its trigger when the shared overlay clamps to viewport edges. */
export function useCopyFeedbackArrow(anchor: RefObject<HTMLElement | null>) {
  const [content, setContent] = useState<HTMLSpanElement | null>(null);
  // Effect necessity: the browser positions and clamps the portaled surface after
  // commit. Its box and the anchor's box cannot be derived while rendering.
  // Observe those boxes and the overlay's position; cleanup releases observers,
  // the initial animation frame, and this instance's CSS variable.
  useEffect(() => {
    const surface = content?.parentElement;
    const trigger = anchor.current;
    if (!surface || !trigger) return;
    const update = () => {
      const button = trigger.getBoundingClientRect();
      const popup = surface.getBoundingClientRect();
      const position = button.left + button.width / 2 - popup.left;
      const offset = Math.min(Math.max(position, 12), popup.width - 12);
      const value = `${offset}px`;
      if (surface.style.getPropertyValue('--ov-ui-path-copy-arrow-x') !== value)
        surface.style.setProperty('--ov-ui-path-copy-arrow-x', value);
    };
    const observer = new MutationObserver(update);
    observer.observe(surface, { attributes: true, attributeFilter: ['style'] });
    const resize = new ResizeObserver(update);
    resize.observe(surface);
    resize.observe(trigger);
    const frame = window.requestAnimationFrame(update);
    return () => {
      observer.disconnect();
      resize.disconnect();
      window.cancelAnimationFrame(frame);
      surface.style.removeProperty('--ov-ui-path-copy-arrow-x');
    };
  }, [anchor, content]);
  return setContent;
}
