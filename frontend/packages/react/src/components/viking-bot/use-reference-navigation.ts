import { useEffect, useMemo, useState, type RefObject } from 'react';
import type { FileTreeHandle, FileTreeRow } from '../file-tree/index.js';
import type {
  VikingBotReference,
  VikingBotReferences,
} from './capabilities.js';
import type { MentionEditor } from './reference-picker.js';

/** Move through results without taking focus from the editor or toolbar search. */
export function useReferenceNavigation({
  config,
  rows,
  query,
  editor,
  input,
  tree,
  onSelect,
}: {
  config: VikingBotReferences;
  rows: readonly FileTreeRow[];
  query: string;
  editor?: MentionEditor;
  input: RefObject<HTMLInputElement | null>;
  tree: RefObject<FileTreeHandle | null>;
  onSelect: (reference: VikingBotReference) => void;
}) {
  const [active, setActive] = useState<{ query: string; id: string }>();
  const { onLoadMore, onRetry, onExpand, isLoading, error } = config;
  const entries = useMemo(
    () =>
      rows.filter((row) => {
        if (row.kind === 'entry')
          return (
            !row.disabled &&
            (row.selectable !== false ||
              (onExpand &&
                row.type === 'directory' &&
                row.hasChildren !== false))
          );
        if (row.kind === 'load-more')
          return Boolean(onLoadMore && !row.loading);
        return row.kind === 'error' && Boolean(onRetry);
      }),
    [rows, onExpand, onLoadMore, onRetry],
  );
  const activeEntry =
    entries.find((row) => active?.query === query && row.id === active.id) ??
    (query.trim()
      ? entries.find((row) => row.kind === 'entry' && row.selectable !== false)
      : undefined) ??
    entries[0];
  const activeId = activeEntry?.id;

  useEffect(() => {
    if (active?.query === query && activeId)
      tree.current?.scrollToRow(activeId);
  }, [active, activeId, query, tree]);

  useEffect(() => {
    const target = editor?.view.dom ?? input.current;
    if (!target) return;
    const keydown = (event: KeyboardEvent) => {
      if (event.isComposing || event.keyCode === 229) return;
      if (event.key === 'Enter') {
        event.preventDefault();
        event.stopImmediatePropagation();
        if (isLoading) return;
        if (error) {
          onRetry?.();
          return;
        }
        switch (activeEntry?.kind) {
          case 'entry':
            if (activeEntry.selectable !== false) onSelect(activeEntry);
            else if (!activeEntry.expanded) onExpand?.(activeEntry.uri);
            break;
          case 'load-more':
            onLoadMore?.(activeEntry.parentId);
            break;
          case 'error':
            onRetry?.(activeEntry.parentId);
            break;
        }
        return;
      }
      if (!activeEntry || isLoading || error) return;
      if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
        event.preventDefault();
        event.stopImmediatePropagation();
        const index = entries.indexOf(activeEntry);
        const step = event.key === 'ArrowDown' ? 1 : -1;
        const next = entries[(index + step + entries.length) % entries.length];
        if (next) setActive({ query, id: next.id });
      } else if (
        editor &&
        onExpand &&
        activeEntry.kind === 'entry' &&
        activeEntry.type === 'directory' &&
        activeEntry.hasChildren !== false &&
        ((event.key === 'ArrowRight' && !activeEntry.expanded) ||
          (event.key === 'ArrowLeft' && activeEntry.expanded))
      ) {
        event.preventDefault();
        event.stopImmediatePropagation();
        onExpand(activeEntry.uri);
      }
    };
    target.addEventListener('keydown', keydown, true);
    return () => target.removeEventListener('keydown', keydown, true);
  }, [
    editor,
    input,
    activeEntry,
    entries,
    query,
    onSelect,
    onExpand,
    onLoadMore,
    onRetry,
    isLoading,
    error,
  ]);
  return activeId;
}
