# FileTree

English / [中文](README_CN.md)

Optional `locale: 'zh-CN' | 'en-US'` overrides the nearest module or Provider; without either, copy defaults to `en-US`. Existing `labels` override individual fields. Hosts provide business content.

A directory tree for resource browsing. The host flattens its tree state into an ordered list of rows and supplies async execution through `operations`; the component renders, handles keyboard interaction and notifies after an operation succeeds.

It renders the five row kinds, indents by depth with a guide per ancestor level, and handles keyboard navigation; selection, expansion, paging and retry run through `operations`.

```tsx
import { FileTree } from '@openviking/react/components/file-tree';
import '@openviking/react/components/file-tree/style.css';

<FileTree
  locale="en-US"
  rows={rows}
  selectedId={selectedId}
  operations={{
    select: async (row) => setSelectedId(row.id),
    toggleExpand: async (row) => toggle(row.id),
  }}
/>;
```

Where `rows` come from, how pages are fetched and how permissions are decided are all up to the host — the component does not touch them.

## Why a flattened row list

The usual approach is to hand the tree a nested structure and let it recurse, keep its own expansion set and decide when to request children. Such a component then has to know the host's paging strategy, retry behavior and account-scope switching timing, and ends up knowing a little about everything while being welded to one backend.

This component inverts that: **the host flattens tree state into a one-dimensional array and the component only renders it in order.**

```ts
type FileTreeRow =
  | FileTreeEntryRow // directory or file
  | FileTreeLoadingRow // children loading
  | FileTreeLoadMoreRow // another page exists
  | FileTreeErrorRow // children failed
  | FileTreeEmptyRow; // expanded but empty
```

What that gives you:

- Paging, lazy loading and retries are implemented on the host side; the component never needs to know "how to fetch the next page".
- A data source that returns all children emits no `load-more` row, which is naturally equivalent without a switch.
- Rows are plain objects, so the host can process them before passing them in (sorting, filtering, permission trimming).

The cost is that the host writes a "tree state → row array" transform. What that buys is a component that serves paged and full data sources and different auth models with identical UI.

## What the component owns

- Rendering rows: icon, indentation, level marker, subtitle and writing indicator.
- Keyboard interaction: arrow keys to move, left/right to collapse and expand, Enter/Space to select, with complete `role="tree"` and `aria-*`.
- Selection, expansion, load-more, retry and opening retention settings run through `operations`, and the matching `on…` success notification fires only after they complete; nothing fires on failure. Omitting an executor disables that operation; omitting a notification does not affect execution.
- Presentation slots for the three tree-level states: loading, failed and empty, with content from the host.
- Delay, positioning and suppression of the hover tooltip.

## What the component does not own

- It requests no data, opens no connections and judges no permissions.
- It does not own the expansion set or the selection — both are controlled.
- Clicking a directory and clicking a file share `operations.select`; the host branches on `row.type`, and `onSelect` notifies after a successful selection.
- The host owns search UI, imports and permission flows. `slots.toolbar` adds custom actions above the built-in controls.

## Extension points

**Row end and hover**: `renderDashboard` places a row-end indicator (for example a dashboard entry) and `renderRowTooltip` places hover content. Returning `null` hides either; dashboard detection is product semantics, so the component ships no path rules.

**Directory copy**: the component resolves a directory's meaning from the structure of its `uri` and ships built-in description and trigger copy for the fixed `viking://` levels. A row's own `description` takes precedence, and `labels.info` overrides copy key by key — an empty string hides a key. The rules live in the component; a host never passes them.

**Per-row one-off effects**: highlighting, flashing and temporary outlines are not tree properties. The component sets `data-file-tree-row-id` (constant `FILE_TREE_ROW_ATTRIBUTE`) on every row element, so the page layer can query it directly, or use `getRowAttributes` to add per-row attributes, or use the `ref` handle's `getRowElement` / `scrollToRow`. The three combine freely and none changes component state.

**ref**: defined as a `forwardRef` contract (type `FileTreeComponent`), so React 18 and 19 both get the handle. Do not declare it as a `FileTreeProps` field — React 18 strips `ref` from props.

## Theme

Styles are scoped and never modify the application's global styles. Theme tokens are declared on the root element and default to theme-neutral values, so the tree reads correctly on both light and dark backgrounds.

## Docs and development

- The props table and runnable example are in the [component docs](../../../../../docs/docs/en/components/file-tree.mdx) ([Chinese](../../../../../docs/docs/zh/components/file-tree.mdx)).
- Development and build commands are in the [UI workspace README](../../../../../README.md); module conventions are in the [package README](../../../README.md).
- The standalone stylesheet entry `@openviking/react/components/file-tree/style.css` is separate from the JS entry and can be imported on demand.

## Optional search and refresh

`search` and `refresh` are hidden by default; pass `true` to enable either. `operations.search` opens host search UI, and `operations.refresh` reloads data. Missing executors disable their buttons. Pending refresh automatically disables the button and spins its icon; failure restores it without a success notification.

`search={{ disabled: true }}` controls search availability. `refresh={{ disabled, loading }}` adds host-controlled availability and pending state. Customize `labels.search` and `labels.refresh`; receive success notifications with `onSearch` and `onRefresh`. Search does not filter rows or register a global shortcut. `slots.toolbar` appears above built-in controls, and either control can be hidden independently.
