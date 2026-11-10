# ScopeSwitcher

English / [中文](README_CN.md)

Optional `locale: 'zh-CN' | 'en-US'` overrides the nearest module or Provider; without either, copy defaults to `en-US`. Existing `labels` override individual fields. Hosts provide business content.

`ScopeSwitcher` is an implemented, stateless breadcrumb layout. Compose independent `ScopeSelector` children from `@openviking/react/components/scope-switcher`; the old `levels` prop is no longer supported. Each child owns its local visibility and receives its own data, so the layout does not aggregate state or subscribe to requests.

```tsx
<ScopeSwitcher locale="en-US">
  <ScopeSelector locale="en-US" kind="selectable" identity={...} data={...} state={...} actions={...} />
  <ScopeSelector locale="en-US" kind="selectable" identity={...} data={...} state={...} actions={...} />
  <ScopeSelector locale="en-US" kind="fixed" identity={...} trigger={{ label: 'default' }} />
</ScopeSwitcher>
```

## Responsibilities

- The host determines the number, order and meaning of children. Omit unavailable levels; a `fixed` level displays a value without a dropdown.
- Selection, options, search keywords, loading, errors and pagination belong to the host. `data.options` is the authoritative set that decides whether the selection is valid; `data.visibleOptions` is the render subset, so search filtering never invalidates the current scope. The component performs no requests, persistence or routing. `actions.onScroll` reports metrics for host-managed pagination.
- For library → space → user requests, use `useScopeCascade` and `ScopeCascade` from `@openviking/react/modules/scope-cascade`. The module coordinates requests and independent subscriptions while these components remain pure views.

## Visibility and selection

- Without `state.visible`, each selector manages its own open state. `actions.onVisibleChange` optionally reports open/close requests, including selection, Escape, outside clicks and focus leaving the selector.
- With `state.visible`, the host controls visibility and must write back changes through `actions.onVisibleChange`. Selection requests close before invoking `actions.onSelect`; a controlled host may keep the panel open until its asynchronous work completes.
- After selection, focus returns to the trigger when the panel actually closes, unless focus has already moved outside. Escape closes and returns focus to the trigger. A disabled selector cannot open.

## Search, slots and keyboard interaction

- Supply `actions.onSearchKeywordChange` to enable search. The input appears when the loaded count or `total` exceeds `SCOPE_SWITCHER_SEARCH_THRESHOLD` (10), or when `searchKeyword` is non-empty. The host filters options; the view highlights matches.
- `slots.renderPanel({ search, list })` arranges the built-in search and list/state nodes alongside host headings, actions and controls. Its custom controls retain their own keyboard behavior. The panel keeps its height cap, so a taller host layout scrolls inside it.
- `slots.renderOption(content, option)` extends or replaces the default row content. Nested interactive controls do not select the row. Disabled options and all options while `busy` cannot be selected.
- On option rows, ArrowUp/ArrowDown and Home/End move focus; Enter/Space select. In the built-in search input, ArrowUp/ArrowDown enter the list while Home/End retain text-editing behavior.
- The trigger uses `aria-haspopup="dialog"` and `aria-expanded`. The panel contains a `listbox` with `option` rows and `aria-selected`; loading, empty and error states support labels and fallback nodes.

## Styles and development

Import `@openviking/react/components/scope-switcher/style.css` explicitly. It provides breadcrumb pills, expanded/selected states, dropdown panels and a bottom panel on narrow screens; JavaScript does not import CSS.

The row is 32px tall with no outline. Each level is its own 32px tall, 16px radius pill with 8px horizontal padding and an 8px icon-to-label gap; labels are 13px at weight 500. A level paints its outline in the primary colour on hover, focus and expand, and keeps a transparent outline at rest so nothing shifts when it appears.

The row never wraps. Levels shrink and labels ellipsize when space runs out, so switching a label between languages never changes the frame's height.

A level with a dropdown tints its button on hover, focus and expand — the same feedback an option row gives inside the panel. A level without one (`kind: 'fixed'`) keeps the identical geometry but drops the arrow and the hover feedback, so read-only scopes stay visually part of the breadcrumb. Adjacent levels are separated by a CSS `/` in `#c9cbd4`.

Inside the panel a hovered row tints lightly and a selected row keeps the primary tint with a bold label and a check mark; the label colour is unchanged, since the design separates selection by weight and the tint. Search hits are tinted in the primary colour rather than highlighted in yellow.

Each row's leading icon sits in a 24px well: white surface, 0.5px `#edeef2` outline, 12px radius, with the glyph itself at 14px. A host that renders round avatars sets `--ov-ui-scope-switcher-option-icon-radius: 50%`.

Theme variables are declared on `.ov-scope-switcher` so a host can override any single one: `--ov-ui-scope-switcher-divider`, `-icon`, `-option-icon`, `-option-icon-size`, `-option-icon-radius`, `-row-hover`, `-tag-background`, `-tag-text`, `-panel-width`, `-option-height`, `-option-gap`, `-list-max-height`, `-panel-max-height`.

Shared colours are read through the package theme baseline, written as `var(--ov-ui-color-<token>, var(--ov-ui-<token>-default, fallback))`. A host that sets `--ov-ui-color-*` on an ancestor wins; otherwise the baseline default applies. The component never assigns a public `--ov-ui-color-*` on its own root, because that would block the host's value from inheriting. Tokens used here: `text-1`, `text-3`, `text-disabled`, `border-1`, `border-2`, `border-3`, `bg-1`, `primary`, `primary-light`.

Development commands are in the [frontend workspace README](../../../../../README.md); module conventions are in the [package guide](../../../README.md). The type declarations define the full field contract.
