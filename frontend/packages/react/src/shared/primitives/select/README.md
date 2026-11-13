# Select

Internal selection control with an Input shell, a borderless `variant="simple"`, and a custom `renderTrigger`. It reuses Dropdown's positioned top-layer surface and the Input theme. It has no public package export.

Compose options through `children` and `SelectOption`; fragments and Tooltip wrappers keep their structure. `value`, `defaultValue`, and `onChange` always use arrays. Empty selection is `[]`; single selection keeps at most one value, while `multiple` toggles values without closing the popup.

```tsx
<Select value={permissions} onChange={setPermissions} multiple>
  <Tooltip content="Allows reading resources">
    <SelectOption value="read">Read</SelectOption>
  </Tooltip>
  <SelectOption value="write">Write</SelectOption>
</Select>
```

`SelectOption.label` overrides the default echo of its children. `renderValue(selectedOptions)` receives an array of `{ value, label, disabled }` on every render, including `[]`. Without it, empty selection displays `placeholder` (default: `Select` / `请选择`), and the Input variant displays removable tags for multiple selection.

`maxTagCount` defaults to `2` and limits visible tags; the remaining selection appears as `+N`. Set it to `0` to show only the count. It does not limit selected values, form inputs, or the array received by `renderValue`. The Input variant defaults to 240px wide, stays on one line, and truncates long tag labels. Tags are 64–120px wide by default; when the container cannot fit their minimum widths, additional tags fold into `+N` even below `maxTagCount`. The internal `--ov-ui-select-tag-min-width` and `--ov-ui-select-tag-max-width` variables override these bounds. Override `style.width` or the root class to fit the host layout.

`disabled` disables the whole control, including tag removal, and closes an uncontrolled popup. `status="error"` applies Input's error styling and `aria-invalid`; passing `aria-invalid` directly is also supported. Use `aria-describedby` to associate an external error message. Custom triggers receive these accessible attributes through `triggerProps`. While the popup is open, the default frame retains its focus styling and the arrow points upward.

`searchable` adds a default-size Input at the top of the popup. By default, search filters declared option labels locally, case-insensitively, including options wrapped in fragments or Tooltip; `searchPlaceholder` overrides the localized search placeholder. No matches displays localized empty feedback. Filtering preserves selected values, and closing resets the query. The themed clear button resets the query, calls `onSearch('')` when provided, and returns focus to search; Tab reaches it before the list when a query is present. Arrow keys move between search and visible options; Shift+Tab from the list returns to search. Enter in the search field prevents form submission outside IME composition; composition keys are left to the browser.

For remote search, set `filterOption={false}`, handle `onSearch(keyword)`, and pass `searching` while the host retrieves query results. `onSearch` receives the unmodified search input on edits, including an empty string when cleared; it does not fire on open or close. Use `onOpenChange` to fetch initial results or cancel work on close. The host owns debouncing, cancellation, and stale-response handling; render results as `SelectOption` children. `loading` describes loading or refreshing the option data; `searching` describes a pending query. Both are independently controlled and do not become true merely because the search field contains text. Either state hides previous results, marks the list busy, and leaves search editable. `loadingContent` and `searchingContent` independently replace their localized progress content with any ReactNode; `undefined` uses the respective default and `null` is explicit empty content. When both flags are true, searching feedback takes priority; clearing `searching` still shows loading feedback if `loading` remains true. Progress semantics remain on the wrapper. `emptyContent` replaces the default empty feedback after either pending state settles, including request errors; `popupFooter` can provide retry actions. Selected labels and disabled metadata stay available when their declarations leave the results, refresh when redeclared, and are discarded on deselection. A value never declared still echoes its raw value.

The repository-only [remote-search demo](../../../../../../docs/docs/_demos/select-remote.tsx) uses a simulated request with a 250ms debounce for keyword searches and 600ms latency, supports cancellation and failure retry, and matches aliases such as `reader` that do not appear in the label.

The popup defaults to `placement="bottom"` with `alignment="start"`, placing it below the trigger with left edges aligned. It flips above when there is not enough space and stays within the viewport. Set `alignment="center"` or `"end"` to override horizontal alignment.

`renderTrigger({ triggerRef, triggerProps, selectedOptions, content, open })` replaces the entire trigger. Attach `triggerRef` to the trigger DOM element and spread `triggerProps` to retain keyboard behavior, disabled state, and accessible attributes. `content` contains the default echo or the result of `renderValue`; custom triggers have no Input or Simple wrapper. Callers can override activation and control visibility with `open` and `onOpenChange`.

Opening a non-searchable popup by mouse or keyboard focuses the selected option, or the opening boundary when nothing is selected. Arrow keys navigate enabled options and scroll them into view. Home/End and typeahead navigate the list; Enter/Space activate an option. Escape closes the popup and restores the trigger, preserving the selection. Tab reaches `popupFooter` actions and follows their native order; Shift+Tab from the first action returns to the list, and Tab from the last action leaves the control. Disabled options cannot be selected or removed through tags.

`name` renders one hidden form input per selected value. `locale` inherits the shared locale context and controls placeholders, loading, searching and empty feedback, search-clear labels, tag-removal labels, and count labels. Popup styling uses `popupClassName`, `listboxClassName`, and `popupStyle`; footer actions use `popupFooter`. Consumers include `select/style.css` in their stylesheet.
