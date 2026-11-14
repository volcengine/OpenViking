import { useId, useState, type KeyboardEvent } from 'react';
import type { SearchHit } from './search-types.js';

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
  const onHighlight = (index: number) => setSelection({ hits, index });
  const onKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (composing || event.nativeEvent.isComposing) {
      if (event.key === 'Escape') event.stopPropagation();
      return;
    }
    if (event.key === 'Enter' && (active || onRetry)) {
      event.preventDefault();
      if (active) onOpen(active.uri);
      else onRetry?.();
    }
    if (!hits.length || !['ArrowDown', 'ArrowUp'].includes(event.key)) return;
    event.preventDefault();
    const step = event.key === 'ArrowDown' ? 1 : -1;
    const index = (activeIndex + step + hits.length) % hits.length;
    onHighlight(index);
    event.currentTarget.ownerDocument
      .getElementById(`${listId}-${index}`)
      ?.scrollIntoView?.({ block: 'nearest' });
  };
  return { active, activeIndex, listId, onHighlight, onKeyDown };
}
