# ScopeSwitcher

English / [中文](README_CN.md)

A scope breadcrumb for the header. The host declares how many levels exist, what each is called and where the options come from; the component renders the breadcrumb triggers, the dropdown panels and the option list, and reports search and switch intent.

`scope-switcher.tsx` maps to `@openviking/react/components/scope-switcher`. It is currently a placeholder: it renders a fixed-size empty shell and consumes no props, so the package can be published for hosts to compare against the API. The real implementation will be rewritten against this document and the type declarations; the source is the source of truth and this document does not repeat the field list.

## Responsibilities

- The component knows nothing about "library / data space / account / user". The host declares how many levels exist, what each is called and where the options come from; the only difference is the number and order of levels, the component itself does not change.
- The level count can vary with the sign-in method: when the host cannot resolve a level it simply does not declare it, and the component neither renders nor reserves space for it, with no separate visibility switch.
- The search keyword, paging, selection, loading and failure are all controlled; switching only reports the selected option, and persistence and route sync belong to the host. The component has no built-in paging rule: the first screen is prepared by the host when `visible` opens, and whether to load the next page is the host's call inside `onScroll`.
- The component renders the scope selection only: triggers, panels, option list and search box. Everything in a panel except the list — heading, top entries, bottom management entries and explanatory text — is implemented by the host's `renderPanel`; the component defines no action shape, decides nothing about what is clickable and takes over no navigation.

## Optional capabilities

Some fields express host capabilities; omitting one degrades to the corresponding behaviour:

| Field           | When omitted                | When provided                                                |
| --------------- | --------------------------- | ------------------------------------------------------------ |
| Search          | No search box               | The panel offers search and highlights matches               |
| Paging          | Only the given items        | The component reports `onScroll` position; the host appends  |
| Fixed level     | Renders a dropdown          | Shows the current value only, no dropdown                    |
| Disabled        | Clickable                   | The level cannot be switched; the trigger is dimmed          |
| Panel shell     | Renders the list block only | The host implements heading, top and bottom content          |
| Row trailing    | Nothing rendered            | The host renders row controls such as "more"                 |
| Busy            | Clickable                   | A change is in flight; the component disables the whole list |
| State fallbacks | Built-in copy               | The host's own nodes replace the empty and failed states     |
| Panel events    | The component ignores them  | Panel open and close notify the host to load on demand       |

## Internal conventions

- Search visibility is decided by the component: shown when the loaded count or `total` exceeds `SCOPE_SWITCHER_SEARCH_THRESHOLD` (10), or when `searchKeyword` is non-empty. The host passes no switch.
- Hit highlighting is computed inside the component from `searchKeyword`; no separate highlight field is accepted.
- Option row content (`renderOption(content, option)`) can append to or replace the default content the component rendered; the row's click, selection state and keyboard interaction always stay with the component.
- Panel content (`renderPanel`) receives `{ search, list }`: `search` is the search box, `list` is the list with its states. Panel visibility is controlled, and the host drives it through `visible`.
- Selection is expressed with `role="listbox"` + `role="option"` + `aria-selected`; the trigger carries `aria-haspopup="listbox"` and `aria-expanded`.
- Panel visibility is controlled: the host drives open and close through `visible` and writes the new state back in `onVisibleChange`. Both "load when the panel opens" and "close only after an async switch succeeds" are the host's call.

## Development entry

Development and build commands are in the [frontend workspace README](../../../../../README.md); module conventions are in the [package guide](../../../README.md).

The standalone style entry is `@openviking/react/components/scope-switcher/style.css`, which currently contains only the placeholder shell styles.
