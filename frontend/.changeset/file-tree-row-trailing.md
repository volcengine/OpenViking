---
'@openviking/react': patch
---

Rename the file tree's row-end slot from `renderDashboard` to `renderRowTrailing`, and its CSS class from `ov-file-tree__dashboard` to `ov-file-tree__trailing`.

The slot never knew what a dashboard was: `FileTree` takes any row-end content, and the board entry is only one use of it. The old name read as if the component owned dashboard semantics, which live in the `FileBrowser` module. `FileBrowser`'s own `renderDashboard` prop, which replaces the dashboard tab content, keeps its name and meaning.

This is a breaking change for `FileTree` consumers: update the prop name and any CSS that targeted the slot's class.

The slot now aligns itself to the right edge of the row, so a host no longer needs to give its content `margin-left: auto`. When the row also shows a trailing marker such as the level or access text, the slot follows it directly.
