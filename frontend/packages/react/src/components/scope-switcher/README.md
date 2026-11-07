# ScopeSwitcher

English / [中文](README_CN.md)

`ScopeSwitcher` is an implemented, stateless breadcrumb layout. Compose independent `ScopeSelector` children from `@openviking/react/components/scope-switcher`; the old `levels` prop is no longer supported. Each child owns its local visibility and receives its own data, so the layout does not aggregate state or subscribe to requests.

```tsx
<ScopeSwitcher>
  <ScopeSelector level={libraryLevel} />
  <ScopeSelector level={spaceLevel} />
  <ScopeSelector level={userLevel} />
</ScopeSwitcher>
```

## Responsibilities

- The host determines the number, order and meaning of children. Omit unavailable levels; a `fixed` level displays a value without a dropdown.
- Selection, options, search keywords, loading, errors and pagination belong to the host. The component performs no requests, persistence or routing. `onScroll` reports metrics for host-managed pagination.
- For library → space → user requests, use `useScopeCascade` and `ScopeCascade` from `@openviking/react/modules/scope-cascade`. The module coordinates requests and independent subscriptions while these components remain pure views.

## Visibility and selection

- Without `level.visible`, each selector manages its own open state. `onVisibleChange` optionally reports open/close requests, including selection, Escape, outside clicks and focus leaving the selector.
- With `level.visible`, the host controls visibility and must write back changes through `onVisibleChange`. Selection requests close before invoking `onSelect`; a controlled host may keep the panel open until its asynchronous work completes.
- After selection, focus returns to the trigger when the panel actually closes, unless focus has already moved outside. Escape closes and returns focus to the trigger. A disabled selector cannot open.

## Search, slots and keyboard interaction

- Supply `onSearchKeywordChange` to enable search. The input appears when the loaded count or `total` exceeds `SCOPE_SWITCHER_SEARCH_THRESHOLD` (10), or when `searchKeyword` is non-empty. The host filters options; the view highlights matches.
- `renderPanel({ search, list })` arranges the built-in search and list/state nodes alongside host headings, actions and controls. Its custom controls retain their own keyboard behavior.
- `renderOption(content, option)` extends or replaces the default row content. Nested interactive controls do not select the row. Disabled options and all options while `busy` cannot be selected.
- On option rows, ArrowUp/ArrowDown and Home/End move focus; Enter/Space select. In the built-in search input, ArrowUp/ArrowDown enter the list while Home/End retain text-editing behavior.
- The trigger uses `aria-haspopup="dialog"` and `aria-expanded`. The panel contains a `listbox` with `option` rows and `aria-selected`; loading, empty and error states support labels and fallback nodes.

## Styles and development

Import `@openviking/react/components/scope-switcher/style.css` explicitly. It provides breadcrumb pills, expanded/selected states, dropdown panels and a bottom panel on narrow screens; JavaScript does not import CSS.

Development commands are in the [frontend workspace README](../../../../../README.md); module conventions are in the [package guide](../../../README.md). The type declarations define the full field contract.
