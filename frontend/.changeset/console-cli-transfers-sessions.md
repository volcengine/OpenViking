---
'@openviking/react': minor
---

## en-US

Add OpenViking CLI session, file download, and OVPack commands to Console. Local inputs use the browser file picker. Administrator-only backup and restore require opting in through `commandOptions.allowAdminCommands`.

With `--read-content`, Console search results display multiline file content and fall back to the abstract when content is unavailable or blank. Search result items support an optional `content` field for the multiline body.

## zh-CN

Console 新增 OpenViking CLI 会话、文件下载和 OVPack 命令。本地输入通过浏览器文件选择器提供。管理员备份和恢复需要通过 `commandOptions.allowAdminCommands` 显式启用。

Console 检索命令使用 `--read-content` 时展示多行正文，正文不可读或为空白时回退到摘要。检索结果项支持通过可选的 `content` 字段提供多行正文。
