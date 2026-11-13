# Dropdown

Internal menu for actions, history and preset selection. It shares positioning and theme synchronization with Popover. Callers render current values, check marks and custom content; Dropdown does not manage a selected value.

Pass a ref-forwarding button as `trigger` and compose options as children. Dropdown has no item-data array prop. Fragments and wrapper components render unchanged; DropdownItem forwards its ref and DOM events so Tooltip can wrap the entire option.

```tsx
<Dropdown trigger={<button type="button">More actions</button>}>
  <Tooltip content="Export the current document">
    <DropdownItem onClick={exportDocument}>Export</DropdownItem>
  </Tooltip>
  <DropdownSeparator />
  <DropdownItem disabled={busy} onClick={deleteDocument}>
    Delete
  </DropdownItem>
</Dropdown>
```

Click is the default trigger mode; `triggerMode="hover"` also allows pointer preview. Arrow keys enter and move through enabled items, Home/End reach the ends, and Escape restores the trigger. Activation closes the menu before invoking the item's handler; the caller owns async progress, results and errors.

Consumers include `popover/popover.css` and `dropdown/dropdown.css` in their component stylesheet. This primitive has no public package export.

`open` and `onOpenChange` allow controlled visibility. Items can use `role="menuitemradio"` with `aria-checked` for mutually exclusive presets. `closeOnClick={false}` keeps the menu open for actions such as loading more history. The surface uses the native top layer when available, with fixed positioning as its fallback.
