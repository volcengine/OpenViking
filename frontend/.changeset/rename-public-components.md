---
'@openviking/react': minor
---

## en-US

Rename public components and subpaths. This is a breaking change for the affected imports.

- `Tui` is now `CommandConsole` (`@openviking/react/components/command-console`). Its types, CSS classes (`ov-command-console*`) and CSS variables (`--ov-ui-command-console-*`) are renamed to match, and the `Console` module's `labels.tui` is now `labels.commandConsole`.
- `FileBrowserEntries` is now `FileBrowserActionBar` (`@openviking/react/components/file-browser-action-bar`), with matching type, CSS class and CSS variable names.
- The six login components moved under `components/auth/`, for example `@openviking/react/components/auth/email-login-form`. Export names are unchanged.
- `LoadingState` is no longer exported from `@openviking/react/components/file-preview`.

## zh-CN

调整公开组件名和子路径，相关导入需要迁移。

- `Tui` 更名为 `CommandConsole`（`@openviking/react/components/command-console`），同步调整类型、CSS 类（`ov-command-console*`）及变量（`--ov-ui-command-console-*`）。Console 的 `labels.tui` 更名为 `labels.commandConsole`。
- `FileBrowserEntries` 更名为 `FileBrowserActionBar`（`@openviking/react/components/file-browser-action-bar`），同步调整类型、CSS 类及变量。
- 六个登录组件移至 `components/auth/`，例如 `@openviking/react/components/auth/email-login-form`，导出名保持不变。
- `@openviking/react/components/file-preview` 不再导出 `LoadingState`。
