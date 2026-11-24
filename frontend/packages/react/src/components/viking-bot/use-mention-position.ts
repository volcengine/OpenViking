import { useLayoutEffect, useState, type CSSProperties } from 'react';
import type { MentionEditor } from './reference-picker.js';

const GAP = 4;
const VIEWPORT_MARGIN = 8;
const MIN_RESULTS_HEIGHT = 96;
const MAX_HEIGHT = 368;

/** Anchor the picker to the start of the @ query as async results change its height. */
export function useMentionPosition(
  editor: MentionEditor | undefined,
  query: string,
  surface: HTMLElement | null,
): CSSProperties {
  const [position, setPosition] = useState<CSSProperties>({
    visibility: 'hidden',
  });
  useLayoutEffect(() => {
    if (!editor || !surface) return;
    const update = () => {
      if (editor.isDestroyed) return;
      const start = Math.max(0, editor.state.selection.from - query.length - 1);
      const rect = editor.view.coordsAtPos(start);
      const above = rect.top - GAP - VIEWPORT_MARGIN;
      const below = window.innerHeight - rect.bottom - GAP - VIEWPORT_MARGIN;
      const placeAbove = above >= MIN_RESULTS_HEIGHT || above >= below;
      const next: CSSProperties = {
        left: Math.max(
          VIEWPORT_MARGIN,
          Math.min(
            rect.left,
            window.innerWidth - surface.offsetWidth - VIEWPORT_MARGIN,
          ),
        ),
        ...(placeAbove
          ? { bottom: window.innerHeight - rect.top + GAP }
          : { top: rect.bottom + GAP }),
        maxHeight: Math.max(
          0,
          Math.min(MAX_HEIGHT, placeAbove ? above : below),
        ),
      };
      setPosition((previous) =>
        previous.left === next.left &&
        previous.top === next.top &&
        previous.bottom === next.bottom &&
        previous.maxHeight === next.maxHeight
          ? previous
          : next,
      );
    };
    update();
    // Editor/viewport movement can occur outside React while the menu remains open.
    const observer =
      typeof ResizeObserver === 'undefined'
        ? undefined
        : new ResizeObserver(update);
    observer?.observe(editor.view.dom);
    editor.on('selectionUpdate', update);
    window.addEventListener('resize', update);
    window.addEventListener('scroll', update, true);
    return () => {
      observer?.disconnect();
      editor.off('selectionUpdate', update);
      window.removeEventListener('resize', update);
      window.removeEventListener('scroll', update, true);
    };
  }, [editor, query, surface]);
  return position;
}
