import { useEffect, useId, useRef, useState, type KeyboardEvent } from 'react';
import type { SearchHit } from './search-types.js';

const HOVER_DELAY = 120;

export function useSearchHighlight(
  hits: readonly SearchHit[],
  composing: boolean,
  onOpen: (uri: string) => void,
  onRetry?: () => void,
) {
  const listId = useId();
  const [selection, setSelection] = useState<{
    hits: readonly SearchHit[];
    index: number;
  }>();
  const activeIndex = selection?.hits === hits ? selection.index : 0;
  const active = hits[activeIndex];
  const hoverTimer = useRef<ReturnType<typeof setTimeout> | undefined>(
    undefined,
  );
  // The pointer schedules a browser timer; when results change or the dialog
  // unmounts, cancel it so a row from the previous list cannot take selection.
  useEffect(() => () => clearTimeout(hoverTimer.current), [hits]);
  const onLeave = () => clearTimeout(hoverTimer.current);
  const onHover = (index: number) => {
    onLeave();
    if (index !== activeIndex) {
      hoverTimer.current = setTimeout(
        () => setSelection({ hits, index }),
        HOVER_DELAY,
      );
    }
  };
  const onKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (composing || event.nativeEvent.isComposing) {
      if (event.key === 'Escape') event.stopPropagation();
      return;
    }
    if (event.key === 'Enter' && (active || onRetry)) {
      event.preventDefault();
      onLeave();
      if (active) onOpen(active.uri);
      else onRetry?.();
    }
    if (!hits.length || !['ArrowDown', 'ArrowUp'].includes(event.key)) return;
    event.preventDefault();
    const step = event.key === 'ArrowDown' ? 1 : -1;
    const index = (activeIndex + step + hits.length) % hits.length;
    onLeave();
    setSelection({ hits, index });
    event.currentTarget.ownerDocument
      .getElementById(`${listId}-${index}`)
      ?.scrollIntoView?.({ block: 'nearest' });
  };
  return { active, activeIndex, listId, onHover, onLeave, onKeyDown };
}
