---
'@openviking/react': minor
---

Rename public components and subpaths. This is a breaking change for the affected imports.

- `Tui` is now `CommandConsole` (`@openviking/react/components/command-console`). Its types, CSS classes (`ov-command-console*`) and CSS variables (`--ov-ui-command-console-*`) are renamed to match, and the `Console` module's `labels.tui` is now `labels.commandConsole`.
- `FileBrowserEntries` is now `FileBrowserActionBar` (`@openviking/react/components/file-browser-action-bar`), with matching type, CSS class and CSS variable names.
- The six login components moved under `components/auth/`, for example `@openviking/react/components/auth/email-login-form`. Export names are unchanged.
- `LoadingState` is no longer exported from `@openviking/react/components/file-preview`.
