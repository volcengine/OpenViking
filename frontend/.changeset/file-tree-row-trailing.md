---
'@openviking/react': patch
---

## en-US

Rename the file tree's row-end slot from `renderDashboard` to `renderRowTrailing`, and its CSS class from `ov-file-tree__dashboard` to `ov-file-tree__trailing`.

The slot never knew what a dashboard was: `FileTree` takes any row-end content, and the board entry is only one use of it. The old name read as if the component owned dashboard semantics, which live in the `FileBrowser` module. `FileBrowser`'s own `renderDashboard` prop, which replaces the dashboard tab content, keeps its name and meaning.

This is a breaking change for `FileTree` consumers: update the prop name and any CSS that targeted the slot's class.

The slot now aligns itself to the right edge of the row, so a host no longer needs to give its content `margin-left: auto`. When the row also shows a trailing marker such as the level or access text, the slot follows it directly.

## zh-CN

FileTree 行尾插槽从 `renderDashboard` 更名为 `renderRowTrailing`，对应 CSS 类从 `ov-file-tree__dashboard` 更名为 `ov-file-tree__trailing`。FileBrowser 用于替换看板页签内容的 `renderDashboard` 保持原名和原有行为。

这是 FileTree 的破坏性变更：宿主需更新属性名及引用旧类名的 CSS。

插槽自动靠右，宿主无需设置 `margin-left: auto`。存在层级或访问权限等尾部标记时，插槽紧随其后。
