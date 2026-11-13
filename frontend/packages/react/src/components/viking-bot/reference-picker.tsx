import { Input } from '../../shared/primitives/input/input.js';
import type { Locale } from '../../shared/locale.js';
import { useEffect, useId, useMemo, useState } from 'react';
import type { SenderProps } from '@ark-design/ui/sender';
import { usePortalTheme } from '../../shared/primitives/tooltip/use-portal-theme.js';
import { FileTree } from '../file-tree/index.js';
import type { FileTreeEntryRow } from '../file-tree/index.js';
import type { VikingBotLabels } from './model.js';
import type {
  VikingBotReference,
  VikingBotReferences,
} from './capabilities.js';
import { resolveVikingBotLabels } from './labels.js';

export type MentionConfig = NonNullable<
  NonNullable<SenderProps['richTextAreaProps']>['mentionConfig']
>;
type Editor = Parameters<NonNullable<MentionConfig['render']>>[0]['editor'];

/** The same host-owned tree serves the toolbar and Sender's mention extension. */
export function ReferencePicker({
  config,
  locale,
  query: externalQuery,
  editor,
  onSelect,
  labels: overrides,
}: {
  config: VikingBotReferences;
  locale: Locale;
  query?: string;
  editor?: Editor;
  onSelect: (reference: VikingBotReference) => void;
  labels?: VikingBotLabels;
}) {
  const labels = resolveVikingBotLabels(locale, overrides);
  const [search, setSearch] = useState('');
  const [active, setActive] = useState(0);
  const [surface, setSurface] = useState<HTMLDivElement | null>(null);
  const anchor = useMemo(
    () => ({ current: editor?.view.dom ?? null }),
    [editor],
  );
  usePortalTheme(!!editor, anchor, surface);
  const query = externalQuery ?? search;
  const { onQueryChange } = config;
  const id = useId();
  const rows =
    query && !config.onQueryChange
      ? config.rows.filter(
          (row) =>
            row.kind === 'entry' &&
            `${row.name} ${row.uri}`
              .toLowerCase()
              .includes(query.toLowerCase()),
        )
      : config.rows;
  const entries = rows.filter(
    (row): row is FileTreeEntryRow => row.kind === 'entry' && !row.disabled,
  );
  const activeEntry = entries[Math.min(active, entries.length - 1)];
  // Sender reports its query only through render(). Notify the host after commit,
  // rather than updating the host while its editor is rendering.
  useEffect(() => {
    onQueryChange?.(query);
  }, [query, onQueryChange]);
  // Mention keys originate in the external editor. Handle them there without
  // moving focus out of the editor or submitting while an IME is composing.
  useEffect(() => {
    if (!editor) return;
    const keydown = (event: KeyboardEvent) => {
      if (event.isComposing) return;
      if (event.key === 'Enter') {
        event.preventDefault();
        event.stopImmediatePropagation();
        if (activeEntry && !config.isLoading && !config.error)
          onSelect(activeEntry);
        return;
      }
      if (config.isLoading || config.error) return;
      if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
        event.preventDefault();
        event.stopImmediatePropagation();
        setActive((index) =>
          entries.length
            ? (index + (event.key === 'ArrowDown' ? 1 : -1) + entries.length) %
              entries.length
            : 0,
        );
      } else if (
        (event.key === 'ArrowRight' || event.key === 'ArrowLeft') &&
        activeEntry?.type === 'directory'
      ) {
        event.preventDefault();
        event.stopImmediatePropagation();
        config.onExpand?.(activeEntry.uri);
      }
    };
    editor.view.dom.addEventListener('keydown', keydown, true);
    return () => editor.view.dom.removeEventListener('keydown', keydown, true);
  }, [editor, activeEntry, entries.length, onSelect, config]);
  return (
    <div
      ref={setSurface}
      className={`ov-viking-bot__reference-picker${editor ? ' ov-viking-bot__reference-picker--mention' : ''}`}
    >
      {editor ? null : (
        <label htmlFor={id}>
          {labels.searchReferences}
          <Input
            id={id}
            type="search"
            value={search}
            onChange={(event) => {
              setSearch(event.target.value);
              setActive(0);
            }}
          />
        </label>
      )}
      {config.error ? (
        <p role="alert">{config.error}</p>
      ) : (
        <FileTree
          locale={locale}
          aria-label={labels.reference}
          rows={rows}
          isReady={!config.isLoading}
          selectedId={editor ? activeEntry?.id : undefined}
          operations={{
            select: () => Promise.resolve(),
            toggleExpand: () => Promise.resolve(),
          }}
          onSelect={onSelect}
          onToggleExpand={(row) => config.onExpand?.(row.uri)}
          renderRowTooltip={() => null}
          getRowAttributes={() =>
            editor ? { onMouseDown: (event) => event.preventDefault() } : {}
          }
          loadingFallback={<p role="status">{labels.loading}</p>}
          emptyFallback={<p role="status">{labels.emptyReferences}</p>}
          labels={{
            expand: labels.expandReference,
            collapse: labels.collapseReference,
          }}
        />
      )}
    </div>
  );
}
